use axum::{
    extract::{ConnectInfo, Path, Query, State},
    http::{Request, StatusCode},
    middleware::{self, Next},
    response::Response,
    routing::{get, post},
    Json, Router,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{net::SocketAddr, sync::Arc};
use tokio::{
    io::AsyncWriteExt,
    net::{TcpListener, UnixListener},
    sync::Mutex,
};
#[derive(Clone)]
struct App {
    audit: Arc<Mutex<tokio::fs::File>>,
}
#[derive(Serialize)]
struct Cap {
    id: &'static str,
    method: &'static str,
    path: &'static str,
    risk: &'static str,
    available: bool,
    reason: Option<&'static str>,
}
#[derive(Serialize)]
struct ErrBody {
    ok: bool,
    error: String,
    code: String,
}
#[derive(Deserialize)]
struct Wifi {
    ssid: String,
    password: Option<String>,
}
#[derive(Deserialize)]
struct Sms {
    number: String,
    text: String,
}
#[derive(Deserialize)]
struct Dial {
    number: String,
}
#[derive(Deserialize)]
struct Cam {
    sensor: String,
    exposure_us: Option<u32>,
    gain: Option<f32>,
}
#[derive(Deserialize)]
struct Bright {
    value: u8,
}
#[derive(Deserialize)]
struct Volume {
    value: u8,
}
#[derive(Deserialize)]
struct Since {
    since: Option<String>,
}
fn c(
    id: &'static str,
    method: &'static str,
    path: &'static str,
    risk: &'static str,
    available: bool,
    reason: Option<&'static str>,
) -> Cap {
    Cap {
        id,
        method,
        path,
        risk,
        available,
        reason,
    }
}
fn caps() -> Vec<Cap> {
    vec![
        c("status", "GET", "/v1/status", "read", true, None),
        c("thermal", "GET", "/v1/thermal", "read", true, None),
        c(
            "processes.top",
            "GET",
            "/v1/processes/top",
            "read",
            true,
            None,
        ),
        c("wifi.status", "GET", "/v1/wifi/status", "read", true, None),
        c("wifi.scan", "POST", "/v1/wifi/scan", "read", true, None),
        c(
            "wifi.connect",
            "POST",
            "/v1/wifi/connect",
            "reversible",
            true,
            None,
        ),
        c(
            "wifi.disconnect",
            "POST",
            "/v1/wifi/disconnect",
            "reversible",
            true,
            None,
        ),
        c("bt.status", "GET", "/v1/bt/status", "read", true, None),
        c(
            "bt.power",
            "POST",
            "/v1/bt/power",
            "reversible",
            false,
            Some("HCI raw-socket kernel panic: disabled"),
        ),
        c(
            "bt.scan",
            "POST",
            "/v1/bt/scan",
            "read",
            false,
            Some("HCI raw-socket kernel panic: disabled"),
        ),
        c(
            "modem.status",
            "GET",
            "/v1/modem/status",
            "read",
            true,
            None,
        ),
        c("sms.list", "GET", "/v1/sms", "read", true, None),
        c("sms.send", "POST", "/v1/sms/send", "risky", true, None),
        c("call.dial", "POST", "/v1/call/dial", "risky", true, None),
        c(
            "camera.capture",
            "POST",
            "/v1/camera/capture",
            "read",
            true,
            None,
        ),
        c(
            "camera.status",
            "GET",
            "/v1/camera/status",
            "read",
            true,
            None,
        ),
        c("display.get", "GET", "/v1/display", "read", true, None),
        c(
            "display.on",
            "POST",
            "/v1/display/on",
            "reversible",
            true,
            None,
        ),
        c(
            "display.off",
            "POST",
            "/v1/display/off",
            "reversible",
            true,
            None,
        ),
        c(
            "display.brightness",
            "POST",
            "/v1/display/brightness",
            "reversible",
            true,
            None,
        ),
        c(
            "audio.volume",
            "POST",
            "/v1/audio/volume",
            "reversible",
            true,
            None,
        ),
        c(
            "system.reboot-recovery",
            "POST",
            "/v1/system/reboot-recovery",
            "risky",
            true,
            None,
        ),
        c("logs.dmesg", "GET", "/v1/logs/dmesg", "read", true, None),
        c(
            "service.restart",
            "POST",
            "/v1/services/{name}/restart",
            "reversible",
            true,
            Some("allowlist checked at request time"),
        ),
    ]
}
fn now() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
        .to_string()
}
async fn audit(a: &App, action: &str, d: Value) {
    let mut f = a.audit.lock().await;
    let _ = f
        .write_all(format!("{}\n", json!({"ts":now(),"action":action,"detail":d})).as_bytes())
        .await;
    let _ = f.flush().await;
}
fn error(s: StatusCode, code: &str, msg: &str) -> (StatusCode, Json<ErrBody>) {
    (
        s,
        Json(ErrBody {
            ok: false,
            error: msg.into(),
            code: code.into(),
        }),
    )
}
async fn caps_get() -> Json<Value> {
    Json(json!({"capabilities":caps()}))
}
async fn status() -> Json<Value> {
    Json(
        json!({"ok":true,"battery":null,"charging":null,"thermal_zones":[],"cpu_load":null,"mem":null,"disk":null,"network":null,"modem":null,"display":null,"audio_muted":true,"available":false,"reason":"device probes not configured"}),
    )
}
async fn thermal() -> Json<Value> {
    Json(json!({"ok":true,"zones":[],"available":false,"reason":"thermal sysfs unavailable"}))
}
async fn top() -> Json<Value> {
    Json(json!({"ok":true,"processes":[],"available":false,"reason":"proc snapshot unavailable"}))
}
async fn wifi() -> Json<Value> {
    Json(
        json!({"ok":true,"state":"unknown","available":false,"reason":"wifi backend not configured"}),
    )
}
async fn scan(State(a): State<App>) -> Json<Value> {
    audit(&a, "wifi.scan", json!({})).await;
    Json(json!({"ok":true,"networks":[],"available":false,"reason":"wifi backend not configured"}))
}
async fn connect(State(a): State<App>, Json(p): Json<Wifi>) -> Json<Value> {
    let _ = p.password;
    audit(&a, "wifi.connect", json!({"ssid":p.ssid})).await;
    Json(json!({"ok":false,"error":"wifi backend not configured","code":"unavailable"}))
}
async fn disconnect(State(a): State<App>) -> Json<Value> {
    audit(&a, "wifi.disconnect", json!({})).await;
    Json(json!({"ok":false,"error":"wifi backend not configured","code":"unavailable"}))
}
async fn bt() -> Json<Value> {
    Json(json!({"ok":true,"available":false,"reason":"HCI raw-socket kernel panic: disabled"}))
}
async fn modem() -> Json<Value> {
    Json(json!({"ok":true,"available":false,"reason":"s22-phoned proxy not configured"}))
}
async fn sms_get() -> Json<Value> {
    Json(
        json!({"ok":true,"messages":[],"available":false,"reason":"s22-phoned proxy not configured"}),
    )
}
async fn sms_send(State(a): State<App>, Json(p): Json<Sms>) -> Json<Value> {
    audit(
        &a,
        "sms.send",
        json!({"number":p.number,"text_len":p.text.len()}),
    )
    .await;
    Json(json!({"ok":false,"error":"s22-phoned proxy not configured","code":"unavailable"}))
}
async fn dial(State(a): State<App>, Json(p): Json<Dial>) -> Json<Value> {
    audit(&a, "call.dial", json!({"number":p.number})).await;
    Json(json!({"ok":false,"error":"s22-phoned proxy not configured","code":"unavailable"}))
}
async fn camera_status() -> Json<Value> {
    Json(json!({"ok":true,"available":false,"reason":"camera client not configured"}))
}
async fn capture(State(a): State<App>, Json(p): Json<Cam>) -> Json<Value> {
    audit(
        &a,
        "camera.capture",
        json!({"sensor":p.sensor,"exposure_us":p.exposure_us,"gain":p.gain}),
    )
    .await;
    Json(json!({"ok":false,"error":"camera client not configured","code":"unavailable"}))
}
async fn display() -> Json<Value> {
    Json(json!({"ok":true,"available":false,"reason":"display backend not configured"}))
}
async fn disp_mut(
    State(a): State<App>,
    Path(op): Path<String>,
    body: Option<Json<Bright>>,
) -> Json<Value> {
    audit(
        &a,
        "display.change",
        json!({"operation":op,"value":body.map(|b|b.0.value)}),
    )
    .await;
    Json(json!({"ok":false,"error":"display backend not configured","code":"unavailable"}))
}
async fn audio(
    State(a): State<App>,
    Json(p): Json<Volume>,
) -> Result<Json<Value>, (StatusCode, Json<ErrBody>)> {
    audit(&a, "audio.volume", json!({"value":p.value})).await;
    if p.value != 0
        && !tokio::fs::try_exists("/etc/s22-audio-unmuted")
            .await
            .unwrap_or(false)
    {
        return Err(error(
            StatusCode::FORBIDDEN,
            "policy_denied",
            "phone audio is muted by owner policy",
        ));
    }
    Ok(Json(
        json!({"ok":false,"error":"audio backend not configured","code":"unavailable"}),
    ))
}
async fn restart(
    State(a): State<App>,
    Path(name): Path<String>,
) -> Result<Json<Value>, (StatusCode, Json<ErrBody>)> {
    audit(&a, "service.restart", json!({"name":name})).await;
    if !["openunum", "unumsearch", "modem", "phoned", "llama"].contains(&name.as_str()) {
        return Err(error(
            StatusCode::FORBIDDEN,
            "not_allowed",
            "service is not allowlisted",
        ));
    }
    Ok(Json(
        json!({"ok":false,"error":"keepalive backend not configured","code":"unavailable"}),
    ))
}
async fn recovery(State(a): State<App>) -> Json<Value> {
    audit(&a, "system.reboot-recovery", json!({})).await;
    Json(json!({"ok":false,"error":"recovery backend not configured","code":"unavailable"}))
}
async fn dmesg(Query(q): Query<Since>) -> Json<Value> {
    let _ = q.since;
    Json(json!({"ok":true,"lines":[],"available":false,"reason":"dmesg backend not configured"}))
}
async fn loopback(
    req: Request<axum::body::Body>,
    next: Next,
) -> Result<Response, (StatusCode, Json<ErrBody>)> {
    if req
        .extensions()
        .get::<ConnectInfo<SocketAddr>>()
        .is_some_and(|c| !c.0.ip().is_loopback())
    {
        return Err(error(
            StatusCode::FORBIDDEN,
            "non_loopback",
            "only loopback clients are accepted",
        ));
    }
    Ok(next.run(req).await)
}
fn router(a: App) -> Router {
    Router::new()
        .route("/v1/capabilities", get(caps_get))
        .route("/v1/status", get(status))
        .route("/v1/thermal", get(thermal))
        .route("/v1/processes/top", get(top))
        .route("/v1/wifi/status", get(wifi))
        .route("/v1/wifi/scan", post(scan))
        .route("/v1/wifi/connect", post(connect))
        .route("/v1/wifi/disconnect", post(disconnect))
        .route("/v1/bt/status", get(bt))
        .route("/v1/modem/status", get(modem))
        .route("/v1/sms", get(sms_get))
        .route("/v1/sms/send", post(sms_send))
        .route("/v1/call/dial", post(dial))
        .route("/v1/camera/status", get(camera_status))
        .route("/v1/camera/capture", post(capture))
        .route("/v1/display", get(display))
        .route("/v1/display/{op}", post(disp_mut))
        .route("/v1/audio/volume", post(audio))
        .route("/v1/services/{name}/restart", post(restart))
        .route("/v1/system/reboot-recovery", post(recovery))
        .route("/v1/logs/dmesg", get(dmesg))
        .layer(middleware::from_fn(loopback))
        .with_state(a)
}
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path =
        std::env::var("S22D_AUDIT").unwrap_or_else(|_| "/srv/s22/state/s22d/audit.jsonl".into());
    if let Some(p) = std::path::Path::new(&path).parent() {
        tokio::fs::create_dir_all(p).await?;
    }
    let f = tokio::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .await?;
    let a = App {
        audit: Arc::new(Mutex::new(f)),
    };
    let l = TcpListener::bind("127.0.0.1:8766").await?;
    let ta = a.clone();
    let tcp = tokio::spawn(async move {
        axum::serve(
            l,
            router(ta).into_make_service_with_connect_info::<SocketAddr>(),
        )
        .await
    });
    let socket = std::env::var("S22D_SOCKET").unwrap_or_else(|_| "/run/s22d.sock".into());
    let _ = tokio::fs::remove_file(&socket).await;
    let ul = UnixListener::bind(socket)?;
    let app = App {
        audit: a.audit.clone(),
    };
    axum::serve(ul, router(app)).await?;
    tcp.abort();
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn bt_disabled_reason_is_stable() {
        let c = caps();
        let x = c.iter().find(|c| c.id == "bt.scan").unwrap();
        assert!(!x.available);
        assert_eq!(x.reason, Some("HCI raw-socket kernel panic: disabled"));
    }
    #[test]
    fn dangerous_actions_are_risky() {
        let c = caps();
        assert_eq!(c.iter().find(|c| c.id == "sms.send").unwrap().risk, "risky");
        assert_eq!(
            c.iter().find(|c| c.id == "wifi.connect").unwrap().risk,
            "reversible"
        );
    }
    #[tokio::test]
    async fn status_route_works() {
        use tower::ServiceExt;
        let f = tokio::fs::File::create(std::env::temp_dir().join(format!("s22d-{}.jsonl", now())))
            .await
            .unwrap();
        let r = router(App {
            audit: Arc::new(Mutex::new(f)),
        })
        .oneshot(
            axum::http::Request::builder()
                .uri("/v1/status")
                .body(axum::body::Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
        assert_eq!(r.status(), StatusCode::OK);
    }
}
