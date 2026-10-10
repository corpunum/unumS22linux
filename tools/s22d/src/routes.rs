//! HTTP surface. Handlers are thin: validate the body, apply s22d's own policy (risk tier, owner
//! confirmation, audio policy, allowlists, camera lock), call one backend, audit the outcome.
//! `API.md` documents every route here; `catalog.rs` is its machine-readable twin.

use crate::app::{tcp_guard, Shared};
use crate::backends::{Answer, CaptureReq, TopSort, WifiConnect};
use crate::catalog::{self, Risk, BT_REASON};
use crate::error::{ApiError, ApiResult};
use crate::real::camera::validate as validate_capture;
use crate::real::phoned::valid_number;
use crate::real::services::ALLOWLIST;
use axum::{
    extract::{rejection::QueryRejection, FromRequest, Path, Query, Request, State},
    http::StatusCode,
    middleware,
    routing::{get, post},
    Json, Router,
};
use serde::{de::DeserializeOwned, Deserialize};
use serde_json::{json, Value};

type Reply = Result<Json<Value>, ApiError>;

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Transport {
    /// 127.0.0.1 TCP: loopback peer, loopback Host, no Origin header.
    Tcp,
    /// The 0600 unix socket: filesystem permissions are the access control.
    Unix,
}

/// JSON body extractor that fails in s22d's own error format.
pub struct Body<T>(pub T);

impl<S: Send + Sync, T: DeserializeOwned> FromRequest<S> for Body<T> {
    type Rejection = ApiError;
    async fn from_request(req: Request, state: &S) -> Result<Self, ApiError> {
        match Json::<T>::from_request(req, state).await {
            Ok(Json(v)) => Ok(Body(v)),
            Err(e) => Err(ApiError::bad_request(e.body_text())),
        }
    }
}

fn query<T>(q: Result<Query<T>, QueryRejection>) -> Result<T, ApiError> {
    q.map(|Query(v)| v)
        .map_err(|e| ApiError::bad_request(e.body_text()))
}

async fn blocking<T: Send + 'static>(
    f: impl FnOnce() -> ApiResult<T> + Send + 'static,
) -> ApiResult<T> {
    tokio::task::spawn_blocking(f)
        .await
        .map_err(|e| ApiError::internal(format!("backend task failed: {e}")))?
}

/// Audit the result of a state-changing request and turn it into the HTTP reply.
fn finish(app: &Shared, id: &str, risk: Risk, detail: Value, r: ApiResult) -> Reply {
    match r {
        Ok(mut v) => {
            app.audit.record(id, risk.as_str(), "ok", detail);
            if let Some(o) = v.as_object_mut() {
                o.entry("ok").or_insert(Value::Bool(true));
            }
            Ok(Json(v))
        }
        Err(e) => {
            let mut d = detail;
            d["code"] = json!(e.code);
            d["error"] = json!(e.message);
            app.audit.record(id, risk.as_str(), e.outcome(), d);
            Err(e)
        }
    }
}

fn reading(r: ApiResult) -> Reply {
    r.map(|mut v| {
        if let Some(o) = v.as_object_mut() {
            o.entry("ok").or_insert(Value::Bool(true));
        }
        Json(v)
    })
}

/// Ask the owner on the phone's screen. Anything but an explicit yes is a refusal.
async fn owner_confirm(app: &Shared, question: String) -> Result<(), ApiError> {
    let _slot = app.confirm_lock.clone().try_lock_owned().map_err(|_| {
        ApiError::busy(
            "confirm_busy",
            "another question is already waiting for the owner",
        )
    })?;
    let c = app.b.confirmer.clone();
    let secs = app.cfg.confirm_timeout_s;
    let answer = blocking(move || Ok(c.ask(&question, secs))).await?;
    match answer {
        Answer::Yes => Ok(()),
        Answer::No => Err(ApiError::denied(
            "owner_denied",
            "the owner declined on the phone",
        )),
        Answer::Timeout => Err(ApiError::new(
            StatusCode::REQUEST_TIMEOUT,
            "owner_timeout",
            "the owner did not answer in time",
        )),
        Answer::Busy => Err(ApiError::busy(
            "confirm_busy",
            "the phone is already asking another question",
        )),
        Answer::Unavailable(why) => Err(ApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "confirm_unavailable",
            format!("cannot ask the owner (touch UI not reachable): {why}"),
        )),
    }
}

// ---------------------------------------------------------------- reads

async fn capabilities(State(app): State<Shared>) -> Reply {
    Ok(Json(catalog::capabilities(&|id| app.availability(id))))
}

async fn status(State(app): State<Shared>) -> Reply {
    let t = app.b.telemetry.clone();
    reading(blocking(move || t.status()).await)
}

async fn battery(State(app): State<Shared>) -> Reply {
    let t = app.b.telemetry.clone();
    reading(blocking(move || t.battery()).await)
}

async fn thermal(State(app): State<Shared>) -> Reply {
    let t = app.b.telemetry.clone();
    reading(blocking(move || t.thermal()).await)
}

#[derive(Deserialize)]
struct TopQ {
    sort: Option<TopSort>,
    limit: Option<usize>,
}

async fn top(State(app): State<Shared>, q: Result<Query<TopQ>, QueryRejection>) -> Reply {
    let q = query(q)?;
    let limit = q.limit.unwrap_or(10);
    if !(1..=50).contains(&limit) {
        return Err(ApiError::bad_request("limit must be 1-50"));
    }
    let t = app.b.telemetry.clone();
    let sort = q.sort.unwrap_or(TopSort::Cpu);
    reading(blocking(move || t.top(sort, limit)).await)
}

#[derive(Deserialize)]
struct DmesgQ {
    since: Option<u64>,
    limit: Option<usize>,
}

async fn dmesg(State(app): State<Shared>, q: Result<Query<DmesgQ>, QueryRejection>) -> Reply {
    let q = query(q)?;
    let limit = q.limit.unwrap_or(200);
    if !(1..=1000).contains(&limit) {
        return Err(ApiError::bad_request("limit must be 1-1000"));
    }
    let t = app.b.telemetry.clone();
    reading(blocking(move || t.dmesg(q.since, limit)).await)
}

async fn wifi_status(State(app): State<Shared>) -> Reply {
    let w = app.b.wifi.clone();
    reading(blocking(move || w.status()).await)
}

async fn bt_status() -> Reply {
    Ok(Json(
        json!({"ok": true, "available": false, "reason": BT_REASON}),
    ))
}

async fn bt_disabled() -> Reply {
    Err(ApiError::unavailable(BT_REASON))
}

async fn modem_status(State(app): State<Shared>) -> Reply {
    let p = app.b.phoned.clone();
    reading(blocking(move || p.modem_status()).await)
}

#[derive(Deserialize)]
struct SmsQ {
    #[serde(rename = "box")]
    mailbox: Option<String>,
    since: Option<String>,
    limit: Option<usize>,
}

async fn sms_list(State(app): State<Shared>, q: Result<Query<SmsQ>, QueryRejection>) -> Reply {
    let q = query(q)?;
    let outbox = match q.mailbox.as_deref() {
        None | Some("inbox") => false,
        Some("outbox") => true,
        Some(_) => return Err(ApiError::bad_request("box must be inbox or outbox")),
    };
    let limit = q.limit.unwrap_or(20);
    if !(1..=100).contains(&limit) {
        return Err(ApiError::bad_request("limit must be 1-100"));
    }
    let p = app.b.phoned.clone();
    reading(blocking(move || p.sms_list(outbox, q.since, limit)).await)
}

async fn call_list(State(app): State<Shared>) -> Reply {
    let p = app.b.phoned.clone();
    reading(blocking(move || p.calls()).await)
}

async fn camera_status(State(app): State<Shared>) -> Reply {
    let c = app.b.camera.clone();
    let mut v = blocking(move || c.status()).await?;
    v["busy"] = json!(app.camera.is_busy());
    v["cooldown_s"] = json!(app.camera.cooldown_s());
    reading(Ok(v))
}

async fn display_status(State(app): State<Shared>) -> Reply {
    let d = app.b.display.clone();
    reading(blocking(move || d.status()).await)
}

async fn audio_get(State(app): State<Shared>) -> Reply {
    let level = app.b.audio.level();
    Ok(Json(json!({
        "ok": true,
        "level": level,
        "muted": level.map(|l| l == 0),
        "unmute_allowed": app.cfg.audio_flag.exists(),
    })))
}

// ---------------------------------------------------------------- Wi-Fi

async fn wifi_scan(State(app): State<Shared>) -> Reply {
    let w = app.b.wifi.clone();
    let r = blocking(move || w.scan()).await;
    finish(&app, "wifi.scan", Risk::Read, json!({}), r)
}

async fn wifi_connect(State(app): State<Shared>, Body(req): Body<WifiConnect>) -> Reply {
    // The password never reaches the audit log.
    let detail = json!({"ssid": req.ssid, "secured": req.password.is_some()});
    let w = app.b.wifi.clone();
    let r = blocking(move || w.connect(&req)).await;
    finish(&app, "wifi.connect", Risk::Reversible, detail, r)
}

async fn wifi_disconnect(State(app): State<Shared>) -> Reply {
    let w = app.b.wifi.clone();
    let r = blocking(move || w.disconnect()).await;
    finish(&app, "wifi.disconnect", Risk::Reversible, json!({}), r)
}

async fn wifi_reconnect(State(app): State<Shared>) -> Reply {
    let w = app.b.wifi.clone();
    let r = blocking(move || w.reconnect()).await;
    finish(&app, "wifi.reconnect", Risk::Reversible, json!({}), r)
}

// ---------------------------------------------------------------- phone: SMS and calls (risky)

#[derive(Deserialize)]
struct SmsSend {
    number: String,
    text: String,
}

async fn sms_send(State(app): State<Shared>, Body(b): Body<SmsSend>) -> Reply {
    let detail = json!({"number": b.number, "text_chars": b.text.chars().count()});
    let r = async {
        if !valid_number(&b.number) {
            return Err(ApiError::bad_request("number must look like +306900000000"));
        }
        let n = b.text.chars().count();
        if n == 0 || n > 1000 {
            return Err(ApiError::bad_request("text must be 1-1000 characters"));
        }
        if let Some(why) = app.b.phoned.availability() {
            return Err(ApiError::unavailable(why));
        }
        let preview: String = b.text.chars().take(120).collect();
        owner_confirm(
            &app,
            format!("Send this text to {}?\n\"{preview}\"", b.number),
        )
        .await?;
        let p = app.b.phoned.clone();
        blocking(move || p.sms_send(&b.number, &b.text)).await
    }
    .await;
    finish(&app, "sms.send", Risk::Risky, detail, r)
}

#[derive(Deserialize)]
struct Dial {
    number: String,
}

async fn call_dial(State(app): State<Shared>, Body(b): Body<Dial>) -> Reply {
    let detail = json!({"number": b.number});
    let r = async {
        if !valid_number(&b.number) {
            return Err(ApiError::bad_request("number must look like +306900000000"));
        }
        if let Some(why) = app.b.phoned.availability() {
            return Err(ApiError::unavailable(why));
        }
        owner_confirm(&app, format!("Place a call to {}?", b.number)).await?;
        let p = app.b.phoned.clone();
        blocking(move || p.dial(&b.number)).await
    }
    .await;
    finish(&app, "call.dial", Risk::Risky, detail, r)
}

async fn call_hangup(State(app): State<Shared>) -> Reply {
    let p = app.b.phoned.clone();
    let r = blocking(move || p.hangup()).await;
    finish(&app, "call.hangup", Risk::Reversible, json!({}), r)
}

// ---------------------------------------------------------------- camera

async fn camera_capture(State(app): State<Shared>, Body(req): Body<CaptureReq>) -> Reply {
    let detail = json!({"sensor": req.sensor, "exposure_us": req.exposure_us, "gain": req.gain, "develop": req.develop});
    let r = async {
        validate_capture(&req)?;
        if let Some(why) = app.b.camera.availability() {
            return Err(ApiError::unavailable(why));
        }
        let permit = app.camera.acquire()?;
        let c = app.b.camera.clone();
        let out = blocking(move || c.capture(&req)).await;
        permit.finish(out.is_ok());
        out
    }
    .await;
    finish(&app, "camera.capture", Risk::Reversible, detail, r)
}

// ---------------------------------------------------------------- display

async fn display_on(State(app): State<Shared>) -> Reply {
    let d = app.b.display.clone();
    let r = blocking(move || d.set_power(true)).await;
    finish(&app, "display.on", Risk::Reversible, json!({}), r)
}

async fn display_off(State(app): State<Shared>) -> Reply {
    let d = app.b.display.clone();
    let r = blocking(move || d.set_power(false)).await;
    finish(&app, "display.off", Risk::Reversible, json!({}), r)
}

#[derive(Deserialize)]
struct Brightness {
    percent: u8,
}

async fn display_brightness(State(app): State<Shared>, Body(b): Body<Brightness>) -> Reply {
    let detail = json!({"percent": b.percent});
    let r = if (1..=100).contains(&b.percent) {
        let d = app.b.display.clone();
        blocking(move || d.set_brightness(b.percent)).await
    } else {
        Err(ApiError::bad_request(
            "percent must be 1-100; use /v1/display/off to switch the screen off",
        ))
    };
    finish(&app, "display.brightness", Risk::Reversible, detail, r)
}

// ---------------------------------------------------------------- audio

#[derive(Deserialize)]
struct Volume {
    value: u8,
}

async fn audio_set(State(app): State<Shared>, Body(b): Body<Volume>) -> Reply {
    let detail = json!({"value": b.value});
    let r = if b.value > 100 {
        Err(ApiError::bad_request("value must be 0-100"))
    } else if b.value != 0 && !app.cfg.audio_flag.exists() {
        // Owner policy (2026-10-08): the phone stays muted. The flag file is the owner's switch.
        Err(ApiError::denied(
            "policy_denied",
            "phone audio is muted by owner policy",
        ))
    } else {
        let a = app.b.audio.clone();
        blocking(move || a.set_level(b.value)).await
    };
    finish(&app, "audio.volume.set", Risk::Reversible, detail, r)
}

// ---------------------------------------------------------------- services and system

async fn service_restart(State(app): State<Shared>, Path(name): Path<String>) -> Reply {
    let detail = json!({"name": name});
    let r = if !ALLOWLIST.contains(&name.as_str()) {
        Err(ApiError::denied(
            "not_allowed",
            "service is not allowlisted",
        ))
    } else {
        let s = app.b.services.clone();
        blocking(move || s.restart(&name)).await
    };
    finish(&app, "service.restart", Risk::Reversible, detail, r)
}

async fn reboot_recovery(State(app): State<Shared>) -> Reply {
    let r = async {
        if let Some(why) = app.b.recovery.availability() {
            return Err(ApiError::unavailable(why));
        }
        if app.camera.is_busy() {
            return Err(ApiError::busy(
                "camera_busy",
                "a camera capture is running; rebooting now could fault the camera DMA",
            ));
        }
        owner_confirm(
            &app,
            "Reboot the phone into recovery mode? It is offline for about 40 seconds.".into(),
        )
        .await?;
        let rec = app.b.recovery.clone();
        blocking(move || rec.schedule()).await
    }
    .await;
    finish(&app, "system.reboot-recovery", Risk::Risky, json!({}), r)
}

async fn not_found() -> ApiError {
    ApiError::new(StatusCode::NOT_FOUND, "not_found", "no such endpoint")
}

async fn method_not_allowed() -> ApiError {
    ApiError::new(
        StatusCode::METHOD_NOT_ALLOWED,
        "method_not_allowed",
        "wrong method for this endpoint",
    )
}

pub fn router(app: Shared, transport: Transport) -> Router {
    let r = Router::new()
        .route("/v1/capabilities", get(capabilities))
        .route("/v1/status", get(status))
        .route("/v1/battery", get(battery))
        .route("/v1/thermal", get(thermal))
        .route("/v1/processes/top", get(top))
        .route("/v1/logs/dmesg", get(dmesg))
        .route("/v1/wifi/status", get(wifi_status))
        .route("/v1/wifi/scan", post(wifi_scan))
        .route("/v1/wifi/connect", post(wifi_connect))
        .route("/v1/wifi/disconnect", post(wifi_disconnect))
        .route("/v1/wifi/reconnect", post(wifi_reconnect))
        .route("/v1/bt/status", get(bt_status))
        .route("/v1/bt/power", post(bt_disabled))
        .route("/v1/bt/scan", post(bt_disabled))
        .route("/v1/modem/status", get(modem_status))
        .route("/v1/sms", get(sms_list))
        .route("/v1/sms/send", post(sms_send))
        .route("/v1/calls", get(call_list))
        .route("/v1/call/dial", post(call_dial))
        .route("/v1/call/hangup", post(call_hangup))
        .route("/v1/camera/status", get(camera_status))
        .route("/v1/camera/capture", post(camera_capture))
        .route("/v1/display", get(display_status))
        .route("/v1/display/on", post(display_on))
        .route("/v1/display/off", post(display_off))
        .route("/v1/display/brightness", post(display_brightness))
        .route("/v1/audio/volume", get(audio_get).post(audio_set))
        .route("/v1/services/{name}/restart", post(service_restart))
        .route("/v1/system/reboot-recovery", post(reboot_recovery))
        .fallback(not_found)
        .method_not_allowed_fallback(method_not_allowed)
        .with_state(app);
    match transport {
        Transport::Tcp => r.layer(middleware::from_fn(tcp_guard)),
        Transport::Unix => r,
    }
}
