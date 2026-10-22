//! Shared state and the policy that belongs to s22d itself, not to a backend: the audit log, the
//! camera lock, the single pending owner question, and the request guards on the TCP listener.

use crate::backends::Backends;
use crate::config::Config;
use crate::error::ApiError;
use axum::{
    extract::{ConnectInfo, Request},
    http::{header, StatusCode},
    middleware::Next,
    response::Response,
};
use serde_json::{json, Value};
use std::io::Write;
use std::net::SocketAddr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub struct App {
    pub cfg: Arc<Config>,
    pub b: Backends,
    pub audit: Audit,
    pub camera: CameraGuard,
    /// Only one owner question can be on screen; a second risky request is refused, not queued.
    pub confirm_lock: Arc<tokio::sync::Mutex<()>>,
}

pub type Shared = Arc<App>;

impl App {
    pub fn new(cfg: Arc<Config>, b: Backends, audit: Audit) -> Shared {
        let camera = CameraGuard::new(cfg.camera_min_interval, cfg.camera_failure_cooldown);
        Arc::new(Self {
            cfg,
            b,
            audit,
            camera,
            confirm_lock: Arc::new(tokio::sync::Mutex::new(())),
        })
    }

    /// Availability of one capability: `None` when usable, else why not.
    pub fn availability(&self, id: &str) -> Option<String> {
        if id == "bt.scan" {
            return crate::catalog::static_availability(id);
        }
        if id.starts_with("bt.") {
            return (!self.cfg.bt_tool.exists())
                .then(|| format!("s22-bt missing: {}", self.cfg.bt_tool.display()));
        }
        match id.split('.').next().unwrap_or("") {
            "wifi" => self.b.wifi.availability(),
            "modem" | "sms" | "call" | "mobile_data" => self.b.phoned.availability(),
            "camera" => self.b.camera.availability(),
            "display" => self.b.display.availability(),
            "service" => self.b.services.availability(),
            "system" => self.b.recovery.availability(),
            _ => None,
        }
    }
}

tokio::task_local! {
    /// Who sent the request being served (`X-S22-Caller`), for the audit log.
    pub static CALLER: &'static str;
}

/// `X-S22-Caller: agent | shell | cli`; anything else (or nothing) is `cli`.
pub fn caller_from(header: Option<&str>) -> &'static str {
    match header.map(|h| h.trim().to_ascii_lowercase()).as_deref() {
        Some("agent") => "agent",
        Some("shell") => "shell",
        _ => "cli",
    }
}

/// Serve the request with its caller in scope.
pub async fn caller_scope(req: Request, next: Next) -> Response {
    let caller = caller_from(
        req.headers()
            .get("x-s22-caller")
            .and_then(|v| v.to_str().ok()),
    );
    CALLER.scope(caller, next.run(req)).await
}

/// `+306912345678` -> `+306*******78`: enough to tell numbers apart in the audit log, not to dial.
pub fn mask_number(n: &str) -> String {
    let total = n.chars().filter(|c| c.is_ascii_digit()).count();
    let mut seen = 0;
    n.chars()
        .filter(|c| !c.is_whitespace())
        .map(|c| {
            if !c.is_ascii_digit() {
                return c;
            }
            seen += 1;
            if seen <= 3 || seen + 2 > total {
                c
            } else {
                '*'
            }
        })
        .collect()
}

/// Mask every phone number (`number`, `to`) anywhere in an audit detail.
fn mask_numbers(v: &mut Value) {
    match v {
        Value::Object(m) => {
            for (k, x) in m.iter_mut() {
                match (k.as_str(), &x) {
                    ("number" | "to", Value::String(s)) => *x = Value::String(mask_number(s)),
                    _ => mask_numbers(x),
                }
            }
        }
        Value::Array(a) => a.iter_mut().for_each(mask_numbers),
        _ => {}
    }
}

/// Append-only JSON lines, size-capped: past `max_bytes` the file becomes `<name>.1` (one
/// generation kept) and a new one starts. Every state-changing request and every device API call
/// leaves one record `{ts, caller, action, risk, outcome, detail}` with phone numbers masked.
pub struct Audit {
    path: std::path::PathBuf,
    max_bytes: u64,
    file: Mutex<std::fs::File>,
}

impl Audit {
    #[cfg(test)]
    pub fn open(path: &std::path::Path) -> std::io::Result<Self> {
        Self::open_capped(path, 1 << 20)
    }

    pub fn open_capped(path: &std::path::Path, max_bytes: u64) -> std::io::Result<Self> {
        if let Some(dir) = path.parent() {
            use std::os::unix::fs::PermissionsExt;
            std::fs::create_dir_all(dir)?;
            std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700))?;
        }
        Ok(Self {
            path: path.to_path_buf(),
            max_bytes,
            file: Mutex::new(Self::append(path)?),
        })
    }

    fn append(path: &std::path::Path) -> std::io::Result<std::fs::File> {
        use std::os::unix::fs::OpenOptionsExt;
        std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .mode(0o600)
            .open(path)
    }

    pub fn record(&self, action: &str, risk: &str, outcome: &str, detail: Value) {
        let ts = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        let caller = CALLER.try_with(|c| *c).unwrap_or("internal");
        let mut detail = detail;
        mask_numbers(&mut detail);
        let line = json!({"ts": ts, "caller": caller, "action": action, "risk": risk, "outcome": outcome, "detail": detail});
        if let Ok(mut f) = self.file.lock() {
            if f.metadata()
                .map(|m| m.len() >= self.max_bytes)
                .unwrap_or(false)
            {
                let mut old = self.path.clone().into_os_string();
                old.push(".1");
                if std::fs::rename(&self.path, &old).is_ok() {
                    if let Ok(n) = Self::append(&self.path) {
                        *f = n;
                    }
                }
            }
            let _ = writeln!(f, "{line}");
            let _ = f.flush();
        }
    }
}

/// One capture at a time, never back to back, and a long pause after any failure.
pub struct CameraGuard {
    busy: AtomicBool,
    next_ok: Mutex<Option<Instant>>,
    min_interval: Duration,
    failure_cooldown: Duration,
}

pub struct CameraPermit<'a> {
    guard: &'a CameraGuard,
    done: bool,
}

impl CameraGuard {
    pub fn new(min_interval: Duration, failure_cooldown: Duration) -> Self {
        Self {
            busy: AtomicBool::new(false),
            next_ok: Mutex::new(None),
            min_interval,
            failure_cooldown,
        }
    }

    pub fn is_busy(&self) -> bool {
        self.busy.load(Ordering::SeqCst)
    }

    pub fn cooldown_s(&self) -> u64 {
        self.next_ok
            .lock()
            .ok()
            .and_then(|g| *g)
            .map(|t| {
                t.saturating_duration_since(Instant::now())
                    .as_secs_f64()
                    .ceil() as u64
            })
            .unwrap_or(0)
    }

    pub fn acquire(&self) -> Result<CameraPermit<'_>, ApiError> {
        let wait = self.cooldown_s();
        if wait > 0 {
            return Err(ApiError::new(
                StatusCode::TOO_MANY_REQUESTS,
                "camera_cooldown",
                format!("camera is cooling down, retry in {wait}s"),
            ));
        }
        if self.busy.swap(true, Ordering::SeqCst) {
            return Err(ApiError::busy("camera_busy", "another capture is running"));
        }
        Ok(CameraPermit {
            guard: self,
            done: false,
        })
    }
}

impl CameraPermit<'_> {
    pub fn finish(mut self, ok: bool) {
        let pause = if ok {
            self.guard.min_interval
        } else {
            self.guard.failure_cooldown
        };
        if let Ok(mut g) = self.guard.next_ok.lock() {
            *g = (!pause.is_zero()).then(|| Instant::now() + pause);
        }
        self.done = true;
    }
}

impl Drop for CameraPermit<'_> {
    fn drop(&mut self) {
        if !self.done {
            // Dropped without a verdict (handler cancelled): treat as a failure.
            if let Ok(mut g) = self.guard.next_ok.lock() {
                *g = Some(Instant::now() + self.guard.failure_cooldown);
            }
        }
        self.guard.busy.store(false, Ordering::SeqCst);
    }
}

/// Guards for the TCP listener: loopback peers only, a loopback `Host`, and no browser `Origin`
/// (a web page in the phone's browser must not be able to drive the daemon).
pub async fn tcp_guard(req: Request, next: Next) -> Result<Response, ApiError> {
    let peer_ok = req
        .extensions()
        .get::<ConnectInfo<SocketAddr>>()
        .map(|c| c.0.ip().is_loopback())
        .unwrap_or(false);
    if !peer_ok {
        return Err(ApiError::denied(
            "non_loopback",
            "only loopback clients are accepted",
        ));
    }
    if req.headers().contains_key(header::ORIGIN) {
        return Err(ApiError::denied(
            "browser_origin",
            "requests with an Origin header are refused",
        ));
    }
    if let Some(h) = req.headers().get(header::HOST) {
        let host = h.to_str().unwrap_or("");
        let name = host.rsplit_once(':').map(|(n, _)| n).unwrap_or(host);
        if !["127.0.0.1", "localhost", "[::1]"].contains(&name) {
            return Err(ApiError::denied("bad_host", "Host must be a loopback name"));
        }
    }
    Ok(next.run(req).await)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn camera_guard_is_exclusive_and_paces_captures() {
        let g = CameraGuard::new(Duration::ZERO, Duration::from_secs(60));
        let p = g.acquire().unwrap();
        assert!(g.is_busy());
        assert_eq!(g.acquire().err().unwrap().code, "camera_busy");
        p.finish(true);
        assert!(!g.is_busy());
        let p = g.acquire().unwrap(); // min_interval is zero: allowed immediately
        p.finish(false);
        let e = g.acquire().err().unwrap();
        assert_eq!(
            (e.status, e.code),
            (StatusCode::TOO_MANY_REQUESTS, "camera_cooldown")
        );
        assert!(g.cooldown_s() > 0);
    }

    #[test]
    fn dropping_a_permit_without_a_verdict_counts_as_failure() {
        let g = CameraGuard::new(Duration::ZERO, Duration::from_secs(60));
        drop(g.acquire().unwrap());
        assert!(!g.is_busy());
        assert_eq!(g.acquire().err().unwrap().code, "camera_cooldown");
    }

    #[test]
    fn audit_appends_json_lines() {
        let t = crate::testutil::Tmp::new();
        let p = t.path().join("a/audit.jsonl");
        let a = Audit::open(&p).unwrap();
        a.record("wifi.connect", "reversible", "ok", json!({"ssid": "Lab"}));
        a.record(
            "sms.send",
            "risky",
            "refused",
            json!({"code": "owner_denied"}),
        );
        let lines: Vec<Value> = std::fs::read_to_string(&p)
            .unwrap()
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect();
        assert_eq!(lines.len(), 2);
        assert_eq!(lines[0]["detail"]["ssid"], "Lab");
        assert_eq!(lines[1]["outcome"], "refused");
        assert!(lines[0]["ts"].as_u64().unwrap() > 1_700_000_000);
        assert_eq!(lines[0]["caller"], "internal");
    }

    #[test]
    fn audit_masks_numbers_and_rotates_at_the_cap() {
        let t = crate::testutil::Tmp::new();
        let p = t.path().join("audit.jsonl");
        let a = Audit::open_capped(&p, 300).unwrap();
        for _ in 0..4 {
            a.record(
                "sms.send",
                "action",
                "ok",
                json!({"args": {"number": "+30 691 234 5678"}}),
            );
        }
        let cur = std::fs::read_to_string(&p).unwrap();
        let old = std::fs::read_to_string(t.path().join("audit.jsonl.1")).unwrap();
        assert!(cur.len() < 300 && !old.is_empty());
        assert!(!cur.contains("2345") && !old.contains("2345"));
        assert!(cur.contains("+306*******78"), "{cur}");
    }

    #[test]
    fn mask_keeps_prefix_and_last_two_digits() {
        assert_eq!(mask_number("+306912345678"), "+306*******78");
        assert_eq!(mask_number("0030 6912"), "003***12");
        assert_eq!(mask_number("12"), "12");
    }

    #[test]
    fn caller_header_is_one_of_three_names() {
        assert_eq!(caller_from(Some("Agent")), "agent");
        assert_eq!(caller_from(Some("shell")), "shell");
        assert_eq!(caller_from(Some("evil\nx")), "cli");
        assert_eq!(caller_from(None), "cli");
    }
}
