//! The remote listener (`REMOTE.md`): the rig's OpenUnum uses the phone as a device over the tailnet.
//!
//! Design approved by corpunum-82 on 2026-11-12 (`docs/PHONE_AS_DEVICE_DESIGN.md`):
//! - its own listener, bound to the phone's tailnet address only (config `/srv/s22/state/s22d/remote.json`);
//! - two factors, both required: the TCP peer must be a pinned tailnet node (tailscaled LocalAPI `whois`,
//!   compared by `Node.StableID`), AND it must send that peer's bearer token (only its SHA-256 is stored);
//! - remote callers are never the owner: only the capability ids in [`REMOTE_CAPS`] are reachable, each
//!   behind an on/off switch in `/srv/s22/state/device-switches.json` (native root only, 0600);
//! - captures (photos, listening) answer with the artifact in the response and keep nothing on the phone
//!   unless the request says `"save": true`; every capture appends one line to the capture audit
//!   (`/srv/s22/state/audit/device-captures.jsonl`: who, what, size, sha256, kept; never the content).

use crate::app::{Shared, CALLER};
use crate::error::ApiError;
use crate::real::httpc;
use axum::{
    extract::{ConnectInfo, Path, Request, State},
    http::{header, StatusCode},
    middleware::{self, Next},
    response::Response,
    routing::get,
    Json, Router,
};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::io::Write;
use std::net::SocketAddr;
use std::path::{Path as FsPath, PathBuf};
use std::time::Duration;

/// Capability ids a remote peer may use at all (the switch file can only narrow this further).
/// Owner-only and risky ones (Wi-Fi/mobile data/Bluetooth/autosleep changes, service restarts,
/// reboot, model, pairing) are deliberately absent.
pub const REMOTE_CAPS: &[&str] = &[
    "device.status",
    "wifi.get",
    "mobile_data.get",
    "bluetooth.get",
    "volume.get",
    "brightness.get",
    "torch.get",
    "calls.get",
    "camera.capture",
    "sms.send",
    "call.dial",
    "call.hangup",
    "haptic.play",
    "speak",
    "task.submit",
    "task.get",
];

/// Captures: results go back in the response, then the file is deleted unless `save: true`.
const CAPTURES: &[&str] = &["camera.capture"];

/// Default switch state when the file has no entry: listening ships OFF (owner decision, 11-12).
pub fn default_on(id: &str) -> bool {
    !matches!(id, "audio.listen")
}

#[derive(Debug, Clone)]
pub struct Peer {
    pub name: String,
    pub node_id: String,
    pub token_sha256: String,
}

#[derive(Debug, Clone)]
pub struct RemoteConfig {
    pub bind: SocketAddr,
    pub peers: Vec<Peer>,
    pub tailscaled_sock: PathBuf,
    pub switches: PathBuf,
    pub capture_audit: PathBuf,
}

impl RemoteConfig {
    /// `None` when the file is missing (no remote listener) ; an error when it exists but is bad.
    pub fn load(path: &FsPath) -> Result<Option<RemoteConfig>, String> {
        let raw = match std::fs::read(path) {
            Ok(r) => r,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(e) => return Err(format!("{}: {e}", path.display())),
        };
        let v: Value =
            serde_json::from_slice(&raw).map_err(|e| format!("{}: {e}", path.display()))?;
        Self::from_json(&v).map(Some)
    }

    pub fn from_json(v: &Value) -> Result<RemoteConfig, String> {
        let bind: SocketAddr = v["bind"]
            .as_str()
            .ok_or("bind missing")?
            .parse()
            .map_err(|e| format!("bind: {e}"))?;
        if !is_tailnet(&bind) {
            return Err(format!(
                "bind {bind} is not a tailnet (100.64.0.0/10) address"
            ));
        }
        let mut peers = Vec::new();
        for p in v["peers"].as_array().ok_or("peers missing")? {
            let s = |k: &str| p[k].as_str().map(str::to_string).filter(|x| !x.is_empty());
            let tok = s("token_sha256").ok_or("peer token_sha256 missing")?;
            if tok.len() != 64 || !tok.bytes().all(|b| b.is_ascii_hexdigit()) {
                return Err("token_sha256 must be 64 hex characters".into());
            }
            peers.push(Peer {
                name: s("name").ok_or("peer name missing")?,
                node_id: s("node_id").ok_or("peer node_id missing")?,
                token_sha256: tok.to_ascii_lowercase(),
            });
        }
        if peers.is_empty() {
            return Err("no peers".into());
        }
        let path = |k: &str, d: &str| PathBuf::from(v[k].as_str().unwrap_or(d));
        Ok(RemoteConfig {
            bind,
            peers,
            tailscaled_sock: path("tailscaled_sock", "/run/tailscale/tailscaled.sock"),
            switches: path("switches", "/srv/s22/state/device-switches.json"),
            capture_audit: path(
                "capture_audit",
                "/srv/s22/state/audit/device-captures.jsonl",
            ),
        })
    }
}

fn is_tailnet(a: &SocketAddr) -> bool {
    match a.ip() {
        std::net::IpAddr::V4(ip) => {
            let o = ip.octets();
            o[0] == 100 && (64..128).contains(&o[1])
        }
        std::net::IpAddr::V6(_) => false,
    }
}

/// Whois answer to node identity. Swappable in tests.
pub type Whois = fn(&FsPath, &SocketAddr) -> Result<String, String>;

/// tailscaled LocalAPI `whois` (`Host: local-tailscaled.sock` is required) to the peer's `Node.StableID`.
pub fn tailscale_whois(sock: &FsPath, peer: &SocketAddr) -> Result<String, String> {
    let target = format!("/localapi/v0/whois?addr={peer}");
    let mut s =
        std::os::unix::net::UnixStream::connect(sock).map_err(|e| format!("tailscaled: {e}"))?;
    let _ = s.set_read_timeout(Some(Duration::from_secs(3)));
    let _ = s.set_write_timeout(Some(Duration::from_secs(3)));
    let (code, v) = httpc::request_on_host(&mut s, "GET", &target, None, "local-tailscaled.sock")
        .map_err(|e| format!("whois: {e}"))?;
    if code != 200 {
        return Err(format!("whois: HTTP {code}"));
    }
    v["Node"]["StableID"]
        .as_str()
        .filter(|x| !x.is_empty())
        .map(str::to_string)
        .ok_or_else(|| "whois: no Node.StableID".into())
}

pub fn sha256_hex(b: &[u8]) -> String {
    Sha256::digest(b)
        .iter()
        .map(|x| format!("{x:02x}"))
        .collect()
}

fn ct_eq(a: &str, b: &str) -> bool {
    a.len() == b.len()
        && a.bytes()
            .zip(b.bytes())
            .fold(0u8, |acc, (x, y)| acc | (x ^ y))
            == 0
}

#[derive(Clone)]
pub struct RemoteState {
    pub app: Shared,
    pub cfg: std::sync::Arc<RemoteConfig>,
    pub whois: Whois,
}

/// The peer this request authenticated as (set by [`guard`]).
#[derive(Clone)]
struct PeerName(String);

/// Both factors, in this order: tailnet peer address, pinned node (whois), bearer token. Also no
/// browser `Origin`. Every refusal is audited (who tried is the interesting part).
async fn guard(
    State(st): State<RemoteState>,
    mut req: Request,
    next: Next,
) -> Result<Response, ApiError> {
    let peer = req
        .extensions()
        .get::<ConnectInfo<SocketAddr>>()
        .map(|c| c.0)
        .ok_or_else(|| ApiError::denied("no_peer", "no peer address"))?;
    let refuse = |code: &'static str, msg: &str, detail: Value| {
        st.app.audit.record(
            "remote.auth",
            "read",
            "denied",
            json!({"peer": peer.ip().to_string(), "code": code, "detail": detail}),
        );
        ApiError::denied(code, msg.to_string())
    };
    if !is_tailnet(&peer) {
        return Err(refuse("not_tailnet", "tailnet peers only", json!(null)));
    }
    if req.headers().contains_key(header::ORIGIN) {
        return Err(refuse(
            "browser_origin",
            "requests with an Origin header are refused",
            json!(null),
        ));
    }
    let sock = st.cfg.tailscaled_sock.clone();
    let whois = st.whois;
    let node = tokio::task::spawn_blocking(move || whois(&sock, &peer))
        .await
        .map_err(|e| ApiError::unavailable(format!("whois task: {e}")))?
        .map_err(|e| refuse("whois_failed", "cannot identify the tailnet peer", json!(e)))?;
    let token = req
        .headers()
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .unwrap_or("")
        .trim()
        .to_string();
    let hash = sha256_hex(token.as_bytes());
    let Some(p) =
        st.cfg.peers.iter().find(|p| {
            ct_eq(&p.node_id, &node) && !token.is_empty() && ct_eq(&p.token_sha256, &hash)
        })
    else {
        let known_node = st.cfg.peers.iter().any(|p| p.node_id == node);
        return Err(refuse(
            if known_node {
                "bad_token"
            } else {
                "unknown_node"
            },
            "not an authorised peer",
            json!({"known_node": known_node}),
        ));
    };
    req.extensions_mut().insert(PeerName(p.name.clone()));
    Ok(CALLER.scope("remote", next.run(req)).await)
}

/// Switch file: `{"camera.capture": true, "audio.listen": false, ...}`; a missing entry is [`default_on`].
pub fn switch_on(path: &FsPath, id: &str) -> bool {
    std::fs::read(path)
        .ok()
        .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
        .and_then(|v| v.get(id).and_then(Value::as_bool))
        .unwrap_or_else(|| default_on(id))
}

fn allowed(cfg: &RemoteConfig, id: &str) -> Result<(), ApiError> {
    if !REMOTE_CAPS.contains(&id) {
        return Err(ApiError::denied(
            "not_remote",
            format!("{id} is not available to remote callers"),
        ));
    }
    if !switch_on(&cfg.switches, id) {
        return Err(ApiError::denied(
            "capability_off",
            format!("{id} is switched off on the phone; only the owner can switch it on there"),
        ));
    }
    Ok(())
}

async fn list(State(st): State<RemoteState>) -> Json<Value> {
    let Json(mut all) = crate::device::list(State(st.app.clone())).await;
    if let Some(caps) = all.get_mut("capabilities").and_then(Value::as_array_mut) {
        caps.retain(|c| c["id"].as_str().is_some_and(|id| REMOTE_CAPS.contains(&id)));
        for c in caps.iter_mut() {
            let id = c["id"].as_str().unwrap_or("").to_string();
            c["switched_on"] = json!(switch_on(&st.cfg.switches, &id));
        }
    }
    Json(all)
}

async fn invoke_get(
    State(st): State<RemoteState>,
    Path(id): Path<String>,
    req: Request,
) -> (StatusCode, Json<Value>) {
    dispatch(st, id, Vec::new(), true, req).await
}

async fn invoke(
    State(st): State<RemoteState>,
    Path(id): Path<String>,
    req: Request,
) -> (StatusCode, Json<Value>) {
    let (parts, body) = req.into_parts();
    let bytes = axum::body::to_bytes(body, 64 << 10)
        .await
        .unwrap_or_default()
        .to_vec();
    dispatch(
        st,
        id,
        bytes,
        false,
        Request::from_parts(parts, axum::body::Body::empty()),
    )
    .await
}

async fn dispatch(
    st: RemoteState,
    id: String,
    body: Vec<u8>,
    get: bool,
    req: Request,
) -> (StatusCode, Json<Value>) {
    let peer = req
        .extensions()
        .get::<PeerName>()
        .map(|p| p.0.clone())
        .unwrap_or_default();
    if let Err(e) = allowed(&st.cfg, &id) {
        st.app
            .audit
            .record(&id, "read", "denied", json!({"peer": peer, "code": e.code}));
        return (e.status, Json(e.body()));
    }
    // `save` is the remote layer's own argument; the device API never sees it.
    let mut args: Value = if body.is_empty() {
        json!({})
    } else {
        match serde_json::from_slice(&body) {
            Ok(v) => v,
            Err(e) => {
                let e = ApiError::bad_request(format!("body is not JSON: {e}"));
                return (e.status, Json(e.body()));
            }
        }
    };
    let save = args
        .as_object_mut()
        .and_then(|m| m.remove("save"))
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let raw = if body.is_empty() {
        Vec::new()
    } else {
        args.to_string().into_bytes()
    };
    let (status, Json(mut v)) =
        crate::device::call(st.app.clone(), id.clone(), &raw, None, get).await;
    if status == StatusCode::OK && CAPTURES.contains(&id.as_str()) {
        finish_capture(&st.cfg, &peer, &id, save, &mut v);
    }
    (status, Json(v))
}

/// Move the artifact into the response, delete it unless `save`, and append the capture audit line.
fn finish_capture(cfg: &RemoteConfig, peer: &str, id: &str, save: bool, v: &mut Value) {
    let path = v["state"]["path"].as_str().map(PathBuf::from);
    let mut line = json!({"ts": now_s(), "peer": peer, "cap": id, "kept": save});
    match path.as_deref().map(std::fs::read) {
        Some(Ok(bytes)) => {
            line["bytes"] = json!(bytes.len());
            line["sha256"] = json!(sha256_hex(&bytes));
            let mime = if path
                .as_deref()
                .and_then(|p| p.extension())
                .is_some_and(|e| e == "png")
            {
                "image/png"
            } else {
                "image/jpeg"
            };
            v["artifact"] = json!({"mime": mime, "base64": base64(&bytes), "bytes": bytes.len()});
            if !save {
                for k in ["path", "quick_png", "raw"] {
                    if let Some(p) = v["state"][k].as_str() {
                        let _ = std::fs::remove_file(p);
                    }
                }
                if let Some(m) = v["state"].as_object_mut() {
                    m.remove("path");
                    m.remove("quick_png");
                }
                v["kept"] = json!(false);
            } else {
                v["kept"] = json!(true);
            }
        }
        Some(Err(e)) => line["error"] = json!(format!("artifact unreadable: {e}")),
        None => line["error"] = json!("no artifact path"),
    }
    append_capture(&cfg.capture_audit, &line);
}

pub fn append_capture(path: &FsPath, line: &Value) {
    use std::os::unix::fs::OpenOptionsExt;
    if let Some(d) = path.parent() {
        let _ = std::fs::create_dir_all(d);
    }
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .mode(0o600)
        .open(path)
    {
        let _ = writeln!(f, "{line}");
    }
}

fn now_s() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

pub fn base64(b: &[u8]) -> String {
    const T: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(b.len().div_ceil(3) * 4);
    for c in b.chunks(3) {
        let n = (c[0] as u32) << 16
            | (*c.get(1).unwrap_or(&0) as u32) << 8
            | *c.get(2).unwrap_or(&0) as u32;
        out.push(T[(n >> 18 & 63) as usize] as char);
        out.push(T[(n >> 12 & 63) as usize] as char);
        out.push(if c.len() > 1 {
            T[(n >> 6 & 63) as usize] as char
        } else {
            '='
        });
        out.push(if c.len() > 2 {
            T[(n & 63) as usize] as char
        } else {
            '='
        });
    }
    out
}

pub fn router(st: RemoteState) -> Router {
    Router::new()
        .route("/v1/device", get(list))
        .route("/v1/device/{id}", get(invoke_get).post(invoke))
        .fallback(|| async {
            ApiError::new(
                StatusCode::NOT_FOUND,
                "not_found",
                "remote callers have /v1/device only",
            )
        })
        .layer(middleware::from_fn_with_state(st.clone(), guard))
        .with_state(st)
}
