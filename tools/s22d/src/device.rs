//! The device API (`/v1/device`): one typed, capability-oriented interface for the shell's
//! Settings page, its Agent card and the OpenUnum agent. Every capability declares its effect
//! (`read`, `set`, `action`) and every answer carries `state`, read back from the source after a
//! change, so a caller sees what actually happened, not what it asked for.
//!
//! Policy (owner, 2026-10-22): no confirmations, except calls and SMS to numbers that are not on
//! the phone's private allowlist; those ask the owner on the screen and answer
//! `needs_confirmation` when nobody taps. Every call, reads included, is audited with its caller.
//! `DEVICE_API.md` is the contract; `capabilities()` below is its machine-readable twin.

use crate::app::Shared;
use crate::backends::CaptureReq;
use crate::error::{ApiError, ApiResult};
use crate::real::phoned::valid_number;
use crate::real::settings as st;
use crate::routes::{blocking, calls_gate, capture_still, phone_policy};
use axum::{
    extract::{Path, RawQuery, State},
    http::StatusCode,
    Json,
};
use serde_json::{json, Map, Value};
use std::time::Duration;

pub const API: &str = "device/1";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Effect {
    Read,
    Set,
    Action,
}

impl Effect {
    pub fn as_str(self) -> &'static str {
        match self {
            Effect::Read => "read",
            Effect::Set => "set",
            Effect::Action => "action",
        }
    }
}

pub struct DevCap {
    pub id: &'static str,
    pub effect: Effect,
    /// `none`, or `unlisted_number` for calls and SMS.
    pub confirm: &'static str,
    pub timeout_s: u64,
    pub description: &'static str,
    /// Argument names and their JSON Schema. All arguments share one flat namespace
    /// (`on`, `value`, `muted`, `locked`, `number`, `text`, `pattern`, `sensor`, `force`) so the
    /// agent tool can be a single small schema.
    pub args: Value,
    pub required: &'static [&'static str],
}

fn on() -> Value {
    json!({"on": {"type": "boolean"}})
}

pub fn defs() -> Vec<DevCap> {
    use Effect::*;
    let c = |id, effect, timeout_s, description, args: Value, required| DevCap {
        id,
        effect,
        confirm: if matches!(id, "call.dial" | "sms.send") {
            "unlisted_number"
        } else {
            "none"
        },
        timeout_s,
        description,
        args,
        required,
    };
    let force = json!({"type": "boolean", "description": "skip the would_strand guard"});
    let number = json!({"type": "string", "description": "E.164, e.g. +306900000000"});
    vec![
        c("device.status", Read, 30, "Battery, temperature, network (uplink, Wi-Fi, mobile data, 5G badge), Bluetooth and the main settings in one read.", json!({}), &[]),
        c("wifi.get", Read, 20, "Wi-Fi on/off, connection, SSID, IP, signal and the active uplink.", json!({}), &[]),
        c("wifi.set", Set, 40, "Turn Wi-Fi on or off. Off drops the connection until it is turned on (or the phone restarts); mobile data takes over when it is enabled. Refused with would_strand when no other uplink is enabled, unless force.", json!({"on": {"type": "boolean"}, "force": force}), &["on"]),
        c("bluetooth.get", Read, 15, "Bluetooth controller on/off (s22-bt) and whether hci0 exists.", json!({}), &[]),
        c("bluetooth.set", Set, 140, "Turn the Bluetooth controller on or off (s22-bt up/down; on needs Wi-Fi up and can take a minute). No pairing.", on(), &["on"]),
        c("mobile_data.get", Read, 25, "Mobile data enabled (netpolicy mode), data call state, IPv4 and the active uplink.", json!({}), &[]),
        c("mobile_data.set", Set, 80, "Enable or disable mobile data. On = used whenever Wi-Fi is not usable (brought up now if Wi-Fi is down). Off = data call released and never redialled. Refused with would_strand when nothing else is connected, unless force.", json!({"on": {"type": "boolean"}, "force": force}), &["on"]),
        c("calls.get", Read, 25, "Whether placing calls is enabled, current calls, and the call-audio caveat.", json!({}), &[]),
        c("calls.set", Set, 10, "Enable or disable placing calls (a gate for call.dial).", on(), &["on"]),
        c("call.dial", Action, 95, "Dial a number. Numbers not on the phone's allowlist need the owner's tap on the screen (else needs_confirmation). Two-way call audio does not work yet.", json!({"number": number}), &["number"]),
        c("call.hangup", Action, 25, "End the current call.", json!({}), &[]),
        c("sms.send", Action, 95, "Send a text message. Numbers not on the phone's allowlist need the owner's tap on the screen (else needs_confirmation).", json!({"number": number, "text": {"type": "string", "minLength": 1, "maxLength": 1000}}), &["number", "text"]),
        c("volume.get", Read, 10, "Speech volume level 0-10 and mute.", json!({}), &[]),
        c("volume.set", Set, 10, "Set the speech volume (value 0-10, 0 = mute) or mute/unmute (muted; unmute restores the previous level).", json!({"value": {"type": "integer", "minimum": 0, "maximum": 10}, "muted": {"type": "boolean"}}), &[]),
        c("brightness.get", Read, 10, "Screen state and backlight brightness in percent.", json!({}), &[]),
        c("brightness.set", Set, 15, "Set the backlight brightness in percent (value 1-100).", json!({"value": {"type": "integer", "minimum": 1, "maximum": 100}}), &["value"]),
        c("vibration.get", Read, 10, "Whether vibration/haptic feedback is enabled.", json!({}), &[]),
        c("vibration.set", Set, 10, "Enable or disable vibration and haptic feedback.", on(), &["on"]),
        c("haptic.play", Action, 10, "Vibrate once: pattern tick, buzz, stop, or ON,OFF,... milliseconds (e.g. 40,80,40).", json!({"pattern": {"type": "string", "maxLength": 100}}), &["pattern"]),
        c("autosleep.get", Read, 10, "Whether the phone suspends while locked and idle, and its current state.", json!({}), &[]),
        c("autosleep.set", Set, 10, "Enable or disable suspend while locked and idle (applies within 2 s).", on(), &["on"]),
        c("screen_timeout.get", Read, 10, "Screen-off timeouts in seconds (unlocked, locked; 0 = never).", json!({}), &[]),
        c("screen_timeout.set", Set, 10, "Set the screen-off timeout: value = seconds while unlocked, locked = seconds on the lock screen (0-3600, 0 = never).", json!({"value": {"type": "integer", "minimum": 0, "maximum": 3600}, "locked": {"type": "integer", "minimum": 0, "maximum": 3600}}), &[]),
        c("camera.capture", Action, 330, "Take a photo (sensor rear or front, default rear); it lands in the gallery.", json!({"sensor": {"type": "string", "enum": ["rear", "front"]}}), &[]),
        c("screenshot", Action, 15, "Save a screenshot of the shell to the gallery (as Power + Volume down). The screen must be on.", json!({}), &[]),
    ]
}

fn find(id: &str) -> Option<DevCap> {
    defs().into_iter().find(|d| d.id == id)
}

/// `None` when usable, else why not.
fn availability(app: &Shared, id: &str) -> Option<String> {
    let cfg = &app.cfg;
    match id.split('.').next().unwrap_or("") {
        "wifi" => app.b.wifi.availability(),
        "bluetooth" => {
            (!cfg.bt_tool.exists()).then(|| format!("s22-bt missing: {}", cfg.bt_tool.display()))
        }
        "mobile_data" | "call" | "sms" => app.b.phoned.availability(),
        "brightness" => app.b.display.availability(),
        "camera" => app.b.camera.availability(),
        "haptic" => {
            (!cfg.haptic_sock.exists()).then(|| "s22-haptic daemon not running".to_string())
        }
        "screenshot" => {
            (!cfg.shell_sock.exists()).then(|| "unum-fastshell is not running".to_string())
        }
        _ => None,
    }
}

pub fn capabilities(avail: &dyn Fn(&str) -> Option<String>) -> Value {
    let rows: Vec<Value> = defs()
        .iter()
        .map(|d| {
            let reason = avail(d.id);
            json!({
                "id": d.id,
                "effect": d.effect.as_str(),
                "confirm": d.confirm,
                "timeout_s": d.timeout_s,
                "description": d.description,
                "available": reason.is_none(),
                "reason": reason,
                "args": {"type": "object", "properties": d.args, "required": d.required, "additionalProperties": false},
            })
        })
        .collect();
    json!({"ok": true, "api": API, "capabilities": rows})
}

pub async fn list(State(app): State<Shared>) -> Json<Value> {
    Json(capabilities(&|id| availability(&app, id)))
}

// ---------------------------------------------------------------- arguments

struct Args(Map<String, Value>);

impl Args {
    fn bool(&self, k: &str) -> Result<Option<bool>, ApiError> {
        match self.0.get(k) {
            None | Some(Value::Null) => Ok(None),
            Some(Value::Bool(b)) => Ok(Some(*b)),
            // Local models like to send "true"/"off"; accept the obvious spellings.
            Some(Value::String(s)) => match s.to_ascii_lowercase().as_str() {
                "true" | "on" | "yes" | "1" => Ok(Some(true)),
                "false" | "off" | "no" | "0" => Ok(Some(false)),
                _ => Err(ApiError::bad_request(format!("{k} must be true or false"))),
            },
            Some(_) => Err(ApiError::bad_request(format!("{k} must be true or false"))),
        }
    }
    fn need_bool(&self, k: &str) -> Result<bool, ApiError> {
        self.bool(k)?
            .ok_or_else(|| ApiError::bad_request(format!("{k} is required (true or false)")))
    }
    fn int(&self, k: &str, lo: u64, hi: u64) -> Result<Option<u64>, ApiError> {
        let v = match self.0.get(k) {
            None | Some(Value::Null) => return Ok(None),
            Some(Value::Number(n)) => n.as_f64(),
            Some(Value::String(s)) => s.trim().trim_end_matches('%').parse::<f64>().ok(),
            Some(_) => None,
        };
        match v {
            Some(f) if f.fract() == 0.0 && f >= lo as f64 && f <= hi as f64 => Ok(Some(f as u64)),
            _ => Err(ApiError::bad_request(format!(
                "{k} must be an integer {lo}-{hi}"
            ))),
        }
    }
    fn str(&self, k: &str) -> Result<Option<String>, ApiError> {
        match self.0.get(k) {
            None | Some(Value::Null) => Ok(None),
            Some(Value::String(s)) => Ok(Some(s.clone())),
            Some(Value::Number(n)) if k == "number" => Ok(Some(n.to_string())),
            Some(_) => Err(ApiError::bad_request(format!("{k} must be a string"))),
        }
    }
    fn need_str(&self, k: &str) -> Result<String, ApiError> {
        self.str(k)?
            .ok_or_else(|| ApiError::bad_request(format!("{k} is required")))
    }
}

fn parse_args(cap: &DevCap, body: &[u8], query: Option<&str>) -> Result<Args, ApiError> {
    let mut m = if body.iter().all(u8::is_ascii_whitespace) {
        Map::new()
    } else {
        match serde_json::from_slice::<Value>(body) {
            Ok(Value::Object(m)) => m,
            Ok(Value::Null) => Map::new(),
            Ok(_) => return Err(ApiError::bad_request("body must be a JSON object")),
            Err(e) => return Err(ApiError::bad_request(format!("body must be JSON: {e}"))),
        }
    };
    for pair in query.unwrap_or("").split('&').filter(|p| !p.is_empty()) {
        let (k, v) = pair.split_once('=').unwrap_or((pair, ""));
        m.entry(k.to_string())
            .or_insert(Value::String(v.to_string()));
    }
    // Tolerate `{"args": {...}}` and the agent tool's own `action` field.
    if let Some(Value::Object(inner)) = m.remove("args") {
        m.extend(inner);
    }
    m.remove("action");
    let known = cap.args.as_object().cloned().unwrap_or_default();
    if let Some(k) = m.keys().find(|k| !known.contains_key(*k)) {
        let allowed: Vec<&String> = known.keys().collect();
        return Err(ApiError::bad_request(format!(
            "{} takes no argument {k:?} (arguments: {allowed:?})",
            cap.id
        )));
    }
    Ok(Args(m))
}

// ---------------------------------------------------------------- handlers

/// What a handler hands back: the state read back, plus an optional note for the caller.
struct Out {
    state: Value,
    note: Option<String>,
    extra: Option<(&'static str, Value)>,
}

fn out(state: Value) -> ApiResult<Out> {
    Ok(Out {
        state,
        note: None,
        extra: None,
    })
}

fn noted(state: Value, note: impl Into<String>) -> ApiResult<Out> {
    Ok(Out {
        state,
        note: Some(note.into()),
        extra: None,
    })
}

async fn wifi_state(app: &Shared) -> ApiResult {
    let w = app.b.wifi.clone();
    let mut v = blocking(move || w.status()).await?;
    let s = v["state"].as_str().unwrap_or("").to_string();
    let on = !matches!(
        s.as_str(),
        "DISCONNECTED" | "INACTIVE" | "INTERFACE_DISABLED" | ""
    );
    if let Some(o) = v.as_object_mut() {
        o.remove("ok");
        // `enabled` is the owner's switch (bind a Settings toggle to it); `on` is the radio as
        // wpa_supplicant reports it (netpolicy's saver may have parked an enabled Wi-Fi).
        o.insert("enabled".into(), json!(!st::wifi_user_off(&app.cfg)));
        o.insert("on".into(), json!(on));
        o.insert("saver_parked".into(), json!(st::saver_parked(&app.cfg)));
        o.insert("uplink".into(), st::netpolicy(&app.cfg)["uplink"].clone());
    }
    Ok(v)
}

async fn mobile_state(app: &Shared) -> ApiResult {
    let p = app.b.phoned.clone();
    let d = blocking(move || p.mobile_data_status()).await?;
    let np = st::netpolicy(&app.cfg);
    let mode = np["mobile_mode"].as_str().unwrap_or("failover").to_string();
    Ok(json!({
        "on": mode != "off",
        "mode": mode,
        "state": d["state"],
        "ipv4": d["ipv4"],
        "interface": d["interface"],
        "uplink": np["uplink"],
    }))
}

/// Poll `probe` until `done` or `wait` passes; returns the last state.
async fn settle<F, Fut>(wait: Duration, mut probe: F, done: impl Fn(&Value) -> bool) -> ApiResult
where
    F: FnMut() -> Fut,
    Fut: std::future::Future<Output = ApiResult>,
{
    let until = tokio::time::Instant::now() + wait;
    loop {
        let v = probe().await?;
        if done(&v) || tokio::time::Instant::now() >= until {
            return Ok(v);
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
}

const CALL_AUDIO: &str = "two-way call audio does not work yet (issue #63): the call connects but nobody can hear or talk";

async fn run(app: &Shared, cap: &DevCap, a: &Args) -> ApiResult<Out> {
    let cfg = app.cfg.clone();
    match cap.id {
        "device.status" => out(device_status(app).await),
        "wifi.get" => out(wifi_state(app).await?),
        "wifi.set" => {
            let on = a.need_bool("on")?;
            let w = app.b.wifi.clone();
            if on {
                st::wifi_off_end(&cfg);
                blocking(move || w.reconnect()).await?;
                let v = settle(
                    cfg.wifi_wait,
                    || wifi_state(app),
                    |v| v["connected"] == true,
                )
                .await?;
                if v["connected"] == true {
                    noted(v, "connected; the uplink moves to Wi-Fi within about 30 s")
                } else {
                    noted(v, "Wi-Fi is on but not connected yet (no known network in range, or still associating)")
                }
            } else {
                let mode = st::netpolicy(&cfg)["mobile_mode"]
                    .as_str()
                    .unwrap_or("failover")
                    .to_string();
                if mode == "off" && !st::usb_carrier(&cfg) && a.bool("force")? != Some(true) {
                    return Err(ApiError::busy("would_strand", "mobile data is disabled and no USB link is up: turning Wi-Fi off would leave the phone offline (enable mobile_data first, or pass force)"));
                }
                st::wifi_off_begin(&cfg)?;
                // A parked saver unparks (reconnects) on its next tick once the kill switch is
                // held; let that happen first so it cannot undo the disconnect.
                let c2 = cfg.clone();
                settle(
                    cfg.wifi_wait.min(Duration::from_secs(8)),
                    || async { Ok(json!(st::saver_parked(&c2))) },
                    |p| p == &json!(false),
                )
                .await?;
                blocking(move || w.disconnect()).await?;
                let v = settle(
                    cfg.wifi_wait.min(Duration::from_secs(5)),
                    || wifi_state(app),
                    |v| v["on"] == false,
                )
                .await?;
                noted(v, "Wi-Fi off until wifi.set on:true (or a restart); mobile data takes over within about 30 s when enabled")
            }
        }
        "bluetooth.get" => out(blocking(move || st::bt_status(&cfg)).await?),
        "bluetooth.set" => {
            let on = a.need_bool("on")?;
            out(blocking(move || st::bt_set(&cfg, on)).await?)
        }
        "mobile_data.get" => out(mobile_state(app).await?),
        "mobile_data.set" => {
            let on = a.need_bool("on")?;
            if on {
                let mode = st::netpolicy(&cfg)["mobile_mode"]
                    .as_str()
                    .unwrap_or("failover")
                    .to_string();
                if mode == "off" {
                    st::set_mobile_mode(&cfg, "failover")?;
                }
                let wifi_up = wifi_state(app)
                    .await
                    .map(|v| v["connected"] == true)
                    .unwrap_or(false);
                if !wifi_up {
                    let p = app.b.phoned.clone();
                    blocking(move || p.mobile_data_up(None)).await?;
                }
                let v = mobile_state(app).await?;
                if wifi_up {
                    noted(v, "enabled: used whenever Wi-Fi is not usable (Wi-Fi is up now, so the data call may stay down)")
                } else {
                    out(v)
                }
            } else {
                let wifi_up = wifi_state(app)
                    .await
                    .map(|v| v["connected"] == true)
                    .unwrap_or(false);
                if !wifi_up && !st::usb_carrier(&cfg) && a.bool("force")? != Some(true) {
                    return Err(ApiError::busy("would_strand", "Wi-Fi is not connected and no USB link is up: turning mobile data off would leave the phone offline (pass force to do it anyway)"));
                }
                st::set_mobile_mode(&cfg, "off")?;
                let p = app.b.phoned.clone();
                blocking(move || p.mobile_data_down()).await?;
                out(mobile_state(app).await?)
            }
        }
        "calls.get" => {
            let p = app.b.phoned.clone();
            let calls = match app.b.phoned.availability() {
                None => blocking(move || p.calls())
                    .await
                    .map(|v| v["calls"].clone())
                    .unwrap_or(Value::Null),
                Some(_) => Value::Null,
            };
            out(json!({"enabled": st::calls_enabled(&cfg), "calls": calls, "audio": CALL_AUDIO}))
        }
        "calls.set" => {
            let on = a.need_bool("on")?;
            out(json!({"enabled": st::set_calls(&cfg, on)?, "audio": CALL_AUDIO}))
        }
        "call.dial" => {
            let number = a.need_str("number")?;
            if !valid_number(&number) {
                return Err(ApiError::bad_request("number must look like +306900000000"));
            }
            calls_gate(app)?;
            if let Some(why) = app.b.phoned.availability() {
                return Err(ApiError::unavailable(why));
            }
            let asked =
                confirmed(phone_policy(app, "dial", &number, format!("Call {number}?")).await)?;
            let p = app.b.phoned.clone();
            let n = number.clone();
            let r = blocking(move || p.dial(&n)).await?;
            let p = app.b.phoned.clone();
            let calls = blocking(move || p.calls())
                .await
                .map(|v| v["calls"].clone())
                .unwrap_or(Value::Null);
            Ok(Out {
                state: json!({"calls": calls}),
                note: Some(format!("dialing; {CALL_AUDIO}")),
                extra: Some(("result", json!({"dial": r, "owner_confirmed": asked}))),
            })
        }
        "call.hangup" => {
            let p = app.b.phoned.clone();
            blocking(move || p.hangup()).await?;
            let p = app.b.phoned.clone();
            out(json!({"calls": blocking(move || p.calls()).await?["calls"]}))
        }
        "sms.send" => {
            let number = a.need_str("number")?;
            let text = a.need_str("text")?;
            if !valid_number(&number) {
                return Err(ApiError::bad_request("number must look like +306900000000"));
            }
            if !(1..=1000).contains(&text.chars().count()) {
                return Err(ApiError::bad_request("text must be 1-1000 characters"));
            }
            if let Some(why) = app.b.phoned.availability() {
                return Err(ApiError::unavailable(why));
            }
            let preview: String = text.chars().take(120).collect();
            let asked = confirmed(
                phone_policy(
                    app,
                    "sms",
                    &number,
                    format!("Send this text to {number}?\n\"{preview}\""),
                )
                .await,
            )?;
            let p = app.b.phoned.clone();
            let r = blocking(move || p.sms_send(&number, &text)).await?;
            Ok(Out {
                state: json!({"id": r["id"], "status": r["status"], "parts": r["parts"]}),
                note: None,
                extra: Some(("result", json!({"owner_confirmed": asked}))),
            })
        }
        "volume.get" => out(st::volume(&cfg)),
        "volume.set" => {
            let value = a.int("value", 0, 10)?.map(|v| v as u8);
            let muted = a.bool("muted")?;
            noted(
                st::set_volume(&cfg, value, muted)?,
                "applies to the next spoken output",
            )
        }
        "brightness.get" => {
            let d = app.b.display.clone();
            out(blocking(move || d.status()).await?)
        }
        "brightness.set" => {
            let v = a
                .int("value", 1, 100)?
                .ok_or_else(|| ApiError::bad_request("value (1-100) is required"))?
                as u8;
            let d = app.b.display.clone();
            let mut s = blocking(move || d.set_brightness(v)).await?;
            if let Some(o) = s.as_object_mut() {
                o.remove("ok");
            }
            out(s)
        }
        "vibration.get" => out(st::vibration(&cfg)),
        "vibration.set" => out(st::set_vibration(&cfg, a.need_bool("on")?)?),
        "haptic.play" => {
            let cmd = st::haptic_command(&a.need_str("pattern")?)?;
            let vib = st::vibration(&cfg);
            if vib["enabled"] == false {
                return noted(
                    json!({"played": false, "vibration": vib}),
                    "vibration is disabled (vibration.set on:true)",
                );
            }
            let reply = blocking(move || st::haptic_send(&cfg, &cmd)).await?;
            if reply.starts_with("error") {
                return Err(ApiError::bad_request(format!("s22-haptic: {reply}")));
            }
            out(json!({"played": reply == "ok", "daemon": reply}))
        }
        "autosleep.get" => out(st::autosleep(&cfg)),
        "autosleep.set" => out(st::set_autosleep(&cfg, a.need_bool("on")?)?),
        "screen_timeout.get" => out(st::screen_timeout(&cfg)),
        "screen_timeout.set" => noted(
            st::set_screen_timeout(&cfg, a.int("value", 0, 3600)?, a.int("locked", 0, 3600)?)?,
            "the shell applies it within 10 s",
        ),
        "camera.capture" => {
            let sensor = a.str("sensor")?.unwrap_or_else(|| "rear".into());
            let r = capture_still(
                app,
                CaptureReq {
                    sensor,
                    exposure_us: None,
                    gain: None,
                    develop: true,
                },
            )
            .await?;
            out(r)
        }
        "screenshot" => out(blocking(move || st::screenshot(&cfg, Duration::from_secs(4))).await?),
        _ => Err(ApiError::new(
            StatusCode::NOT_FOUND,
            "not_found",
            "no such capability",
        )),
    }
}

/// The device API's answer when the owner was asked: an explicit no is final (`owner_denied`);
/// no answer, no screen or another question on screen is `needs_confirmation`.
fn confirmed(r: Result<bool, ApiError>) -> Result<bool, ApiError> {
    r.map_err(|e| match e.code {
        "owner_timeout" | "confirm_unavailable" | "confirm_busy" => ApiError::new(
            StatusCode::CONFLICT,
            "needs_confirmation",
            format!(
                "the number is not on the allowlist and nobody confirmed on the phone ({})",
                e.code
            ),
        ),
        _ => e,
    })
}

/// One compact read for the agent and the shell's status views. A source that fails is `null`.
async fn device_status(app: &Shared) -> Value {
    let cfg = app.cfg.clone();
    let t = app.b.telemetry.clone();
    let battery = blocking(move || t.battery()).await.ok();
    let t = app.b.telemetry.clone();
    let thermal = blocking(move || t.thermal()).await.ok();
    let wifi = wifi_state(app).await.ok();
    let mobile = mobile_state(app).await.ok();
    let p = app.b.phoned.clone();
    let modem = match app.b.phoned.availability() {
        None => blocking(move || p.modem_status()).await.ok(),
        Some(_) => None,
    };
    let c2 = cfg.clone();
    let bt = blocking(move || st::bt_status(&c2)).await.ok();
    let d = app.b.display.clone();
    let display = blocking(move || d.status()).await.ok();
    let b = battery.unwrap_or(Value::Null);
    let m = modem.unwrap_or(Value::Null);
    json!({
        "battery": {"percent": b["percent"], "charging": b["charging"], "status": b["status"], "temp_c": b["temp_c"]},
        "thermal_max_c": thermal.map(|t| t["hottest"]["temp_c"].clone()).unwrap_or(Value::Null),
        "network": {
            "uplink": st::netpolicy(&cfg)["uplink"],
            "wifi": wifi.map(|w| json!({"enabled": w["enabled"], "on": w["on"], "connected": w["connected"], "ssid": w["ssid"], "ip": w["ip"], "rssi_dbm": w["rssi_dbm"]})),
            "mobile_data": mobile.map(|v| json!({"on": v["on"], "state": v["state"], "ipv4": v["ipv4"]})),
            "radio": st::radio_badge(&m, st::now_s()),
            "operator": m["operator"],
            "signal_bars": m["signal"]["icon"]["bars"],
            "usb": st::usb_carrier(&cfg),
        },
        "bluetooth": bt.map(|v| v["on"].clone()),
        "volume": st::volume(&cfg)["level"],
        "brightness_pct": display.map(|v| v["percent"].clone()),
        "vibration": st::vibration(&cfg)["enabled"],
        "autosleep": st::autosleep(&cfg)["enabled"],
        "calls_enabled": st::calls_enabled(&cfg),
    })
}

async fn call(
    app: Shared,
    id: String,
    body: &[u8],
    query: Option<String>,
    method_get: bool,
) -> (StatusCode, Json<Value>) {
    let Some(cap) = find(&id) else {
        let e = ApiError::new(
            StatusCode::NOT_FOUND,
            "not_found",
            format!("no capability {id:?}; GET /v1/device lists them"),
        );
        return (e.status, Json(e.body()));
    };
    let effect = cap.effect.as_str();
    let r = async {
        if method_get && cap.effect != Effect::Read {
            return Err(ApiError::new(
                StatusCode::METHOD_NOT_ALLOWED,
                "method_not_allowed",
                "use POST for set and action capabilities",
            ));
        }
        let args = parse_args(&cap, body, query.as_deref())?;
        if let Some(why) = availability(&app, cap.id) {
            return Err(ApiError::unavailable(why));
        }
        let audited = Value::Object(redact(&args.0));
        let res = run(&app, &cap, &args).await;
        Ok::<_, ApiError>((audited, res))
    }
    .await;
    let (audited, res) = match r {
        Ok((a, res)) => (a, res),
        Err(e) => (json!(null), Err(e)),
    };
    match res {
        Ok(o) => {
            app.audit
                .record(cap.id, effect, "ok", json!({"args": audited}));
            let mut state = o.state;
            if let Some(m) = state.as_object_mut() {
                m.remove("ok");
            }
            let mut v = json!({"ok": true, "cap": cap.id, "effect": effect, "state": state});
            if let Some(n) = o.note {
                v["note"] = json!(n);
            }
            if let Some((k, x)) = o.extra {
                v[k] = x;
            }
            (StatusCode::OK, Json(v))
        }
        Err(e) => {
            app.audit.record(
                cap.id,
                effect,
                e.outcome(),
                json!({"args": audited, "code": e.code, "error": e.message}),
            );
            let mut v = e.body();
            v["cap"] = json!(cap.id);
            v["effect"] = json!(effect);
            (e.status, Json(v))
        }
    }
}

/// Message text never reaches the audit log (numbers are masked by the audit itself).
fn redact(m: &Map<String, Value>) -> Map<String, Value> {
    let mut out = m.clone();
    if let Some(Value::String(t)) = out.get("text") {
        let n = t.chars().count();
        out.insert("text".into(), json!(format!("<{n} chars>")));
    }
    out
}

pub async fn invoke(
    State(app): State<Shared>,
    Path(id): Path<String>,
    RawQuery(q): RawQuery,
    body: axum::body::Bytes,
) -> (StatusCode, Json<Value>) {
    call(app, id, &body, q, false).await
}

pub async fn invoke_get(
    State(app): State<Shared>,
    Path(id): Path<String>,
    RawQuery(q): RawQuery,
) -> (StatusCode, Json<Value>) {
    call(app, id, b"", q, true).await
}
