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
        if id.starts_with("bt.") {
            return crate::catalog::static_availability(id);
        }
        match id.split('.').next().unwrap_or("") {
            "wifi" => self.b.wifi.availability(),
            "modem" | "sms" | "call" => self.b.phoned.availability(),
            "camera" => self.b.camera.availability(),
            "display" => self.b.display.availability(),
            "service" => self.b.services.availability(),
            "system" => self.b.recovery.availability(),
            _ => None,
        }
    }
}

/// Append-only JSON lines. Every state-changing request leaves one record with its outcome.
pub struct Audit {
    file: Mutex<std::fs::File>,
}

impl Audit {
    pub fn open(path: &std::path::Path) -> std::io::Result<Self> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)?;
        Ok(Self {
            file: Mutex::new(file),
        })
    }

    pub fn record(&self, action: &str, risk: &str, outcome: &str, detail: Value) {
        let ts = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        let line =
            json!({"ts": ts, "action": action, "risk": risk, "outcome": outcome, "detail": detail});
        if let Ok(mut f) = self.file.lock() {
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
    }
}
