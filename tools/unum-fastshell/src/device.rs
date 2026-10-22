//! Settings through s22d's typed device API (`device/1`, tools/s22d/DEVICE_API.md):
//! `POST /v1/device/<id>` over `/run/s22d.sock`, one request per connection.
//! `Dev` is the Settings page's state (pure, unit-tested): answers go in, the
//! rows read it. Every value shown is what s22d read back after the change.
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::time::Duration;

pub const SOCK: &str = "/run/s22d.sock";

/// POST /v1/device/<id>; (HTTP status, JSON body). Status 0 = no answer (socket error).
pub fn call(sock: &str, id: &str, args: &Value, timeout_s: u64) -> (u16, Value) {
    let r = (|| -> std::io::Result<(u16, Value)> {
        let mut s = UnixStream::connect(sock)?;
        s.set_read_timeout(Some(Duration::from_secs(timeout_s)))?;
        s.set_write_timeout(Some(Duration::from_secs(5)))?;
        let body = args.to_string();
        write!(
            s,
            "POST /v1/device/{id} HTTP/1.1\r\nHost: localhost\r\nX-S22-Caller: shell\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        )?;
        let mut raw = Vec::new();
        s.read_to_end(&mut raw)?;
        Ok(parse_response(&raw))
    })();
    r.unwrap_or_else(|e| (0, json!({"ok": false, "code": "no_s22d", "error": e.to_string()})))
}

pub fn parse_response(raw: &[u8]) -> (u16, Value) {
    let raw = String::from_utf8_lossy(raw);
    let (head, body) = raw.split_once("\r\n\r\n").unwrap_or((&raw, ""));
    let status = head.split(' ').nth(1).and_then(|c| c.parse().ok()).unwrap_or(0);
    (status, serde_json::from_str(body.trim()).unwrap_or(Value::Null))
}

/// How long a client waits for each capability (Bluetooth up and Wi-Fi association are slow).
pub fn timeout_for(id: &str) -> u64 {
    match id {
        "bluetooth.set" => 150,
        "wifi.set" => 45,
        "camera.capture" | "call.dial" | "sms.send" => 120,
        _ => 15,
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Toggle {
    Wifi,
    Bluetooth,
    Mobile,
    Calls,
    Vibration,
    Autosleep,
}

impl Toggle {
    pub const ALL: [Toggle; 6] = [Toggle::Wifi, Toggle::Bluetooth, Toggle::Mobile, Toggle::Calls, Toggle::Vibration, Toggle::Autosleep];
    pub fn label(self) -> &'static str {
        match self {
            Toggle::Wifi => "Wi-Fi",
            Toggle::Bluetooth => "Bluetooth",
            Toggle::Mobile => "Mobile data",
            Toggle::Calls => "Calls",
            Toggle::Vibration => "Vibration",
            Toggle::Autosleep => "Auto-sleep",
        }
    }
    pub fn set_cap(self) -> &'static str {
        match self {
            Toggle::Wifi => "wifi.set",
            Toggle::Bluetooth => "bluetooth.set",
            Toggle::Mobile => "mobile_data.set",
            Toggle::Calls => "calls.set",
            Toggle::Vibration => "vibration.set",
            Toggle::Autosleep => "autosleep.set",
        }
    }
    fn from_cap(cap: &str) -> Option<Toggle> {
        Toggle::ALL.into_iter().find(|t| t.set_cap() == cap || t.set_cap().replace(".set", ".get") == cap)
    }
}

#[derive(Default, Clone, Debug, PartialEq)]
pub struct Dev {
    pub on: HashMap<Toggle, bool>,
    pub detail: HashMap<Toggle, String>,
    pub volume: Option<i32>,
    pub muted: bool,
    pub brightness: Option<i32>,
    pub timeout: Option<(u64, u64)>,
    /// capability ids waiting for an answer
    pub pending: Vec<String>,
    /// last error per row (toggle label, "Volume", ...)
    pub error: HashMap<String, String>,
    pub available: Option<bool>,
    pub uplink: String,
}

impl Dev {
    /// The requests that refresh the page (on open, and the read-backs after a change).
    pub fn refresh_ids() -> [&'static str; 3] {
        ["device.status", "volume.get", "screen_timeout.get"]
    }

    pub fn begin(&mut self, id: &str) {
        if !self.pending.iter().any(|p| p == id) {
            self.pending.push(id.to_string());
        }
    }

    pub fn busy(&self, t: Toggle) -> bool {
        self.pending.iter().any(|p| p == t.set_cap())
    }

    /// The answer to `id` (HTTP status, body).
    pub fn answer(&mut self, id: &str, status: u16, v: &Value) {
        self.pending.retain(|p| p != id);
        let row = Toggle::from_cap(id).map(|t| t.label().to_string()).unwrap_or_else(|| match id {
            "volume.set" | "volume.get" => "Volume".into(),
            "brightness.set" | "brightness.get" => "Brightness".into(),
            "screen_timeout.set" | "screen_timeout.get" => "Screen timeout".into(),
            _ => id.to_string(),
        });
        if status == 0 || (status == 404 && id == "device.status") {
            self.available = Some(false);
        }
        if v["ok"] != true {
            let code = v["code"].as_str().unwrap_or("error");
            let msg = match code {
                "would_strand" => "would leave the phone offline (no other uplink)".to_string(),
                "unavailable" => format!("unavailable: {}", v["error"].as_str().unwrap_or("")),
                "no_s22d" => "s22d not reachable".to_string(),
                _ => v["error"].as_str().unwrap_or(code).to_string(),
            };
            self.error.insert(row, msg);
            return;
        }
        self.available = Some(true);
        self.error.remove(&row);
        let st = &v["state"];
        match id {
            "device.status" => {
                let b = |x: &Value| x.as_bool();
                let net = &st["network"];
                if let Some(x) = b(&net["wifi"]["enabled"]) {
                    self.on.insert(Toggle::Wifi, x);
                }
                self.detail.insert(Toggle::Wifi, wifi_detail(&net["wifi"]));
                if let Some(x) = b(&net["mobile_data"]["on"]) {
                    self.on.insert(Toggle::Mobile, x);
                }
                self.detail.insert(Toggle::Mobile, mobile_detail(&net["mobile_data"], net["operator"].as_str(), net["radio"].as_str()));
                for (t, k) in [(Toggle::Bluetooth, "bluetooth"), (Toggle::Vibration, "vibration"), (Toggle::Autosleep, "autosleep"), (Toggle::Calls, "calls_enabled")] {
                    if let Some(x) = b(&st[k]).or_else(|| b(&st[k]["on"])).or_else(|| b(&st[k]["enabled"])) {
                        self.on.insert(t, x);
                    }
                }
                if let Some(x) = st["volume"].as_i64().or(st["volume"]["level"].as_i64()) {
                    self.volume = Some(x as i32);
                }
                if let Some(x) = st["brightness_pct"].as_i64() {
                    self.brightness = Some(x as i32);
                }
                self.uplink = net["uplink"].as_str().unwrap_or("").to_string();
            }
            "wifi.set" | "wifi.get" => {
                if let Some(x) = st["enabled"].as_bool().or(st["on"].as_bool()) {
                    self.on.insert(Toggle::Wifi, x);
                }
                self.detail.insert(Toggle::Wifi, wifi_detail(st));
            }
            "mobile_data.set" | "mobile_data.get" => {
                if let Some(x) = st["on"].as_bool() {
                    self.on.insert(Toggle::Mobile, x);
                }
                self.detail.insert(Toggle::Mobile, mobile_detail(st, None, None));
            }
            "bluetooth.set" | "bluetooth.get" | "autosleep.set" | "autosleep.get" | "vibration.set" | "vibration.get" => {
                let t = Toggle::from_cap(id).unwrap();
                if let Some(x) = st["on"].as_bool().or(st["enabled"].as_bool()) {
                    self.on.insert(t, x);
                }
                let d = match t {
                    Toggle::Autosleep => st["state"].as_str().map(|s| format!("{s}{}", st["cycles"].as_u64().map(|c| format!(" · {c} sleeps")).unwrap_or_default())),
                    Toggle::Bluetooth => st["detail"].as_str().map(|s| s.chars().take(60).collect()),
                    _ => None,
                };
                if let Some(d) = d {
                    self.detail.insert(t, d);
                }
            }
            "calls.set" | "calls.get" => {
                if let Some(x) = st["enabled"].as_bool() {
                    self.on.insert(Toggle::Calls, x);
                }
                if let Some(a) = st["audio"].as_str() {
                    self.detail.insert(Toggle::Calls, a.chars().take(60).collect());
                }
            }
            "volume.set" | "volume.get" => {
                if let Some(x) = st["level"].as_i64() {
                    self.volume = Some(x as i32);
                }
                self.muted = st["muted"].as_bool().unwrap_or(false);
            }
            "brightness.set" | "brightness.get" => {
                if let Some(x) = st["percent"].as_i64() {
                    self.brightness = Some(x as i32);
                }
            }
            "screen_timeout.set" | "screen_timeout.get" => {
                if let (Some(u), Some(l)) = (st["unlocked_s"].as_u64(), st["locked_s"].as_u64()) {
                    self.timeout = Some((u, l));
                }
            }
            _ => {}
        }
    }

    /// The request a toggle tap makes (the opposite of what s22d last reported).
    pub fn toggle_request(&self, t: Toggle) -> (&'static str, Value) {
        let cur = self.on.get(&t).copied().unwrap_or(false);
        (t.set_cap(), json!({"on": !cur}))
    }
}

fn wifi_detail(w: &Value) -> String {
    match (w["connected"].as_bool(), w["ssid"].as_str()) {
        (Some(true), Some(s)) => format!("connected · {s}{}", w["rssi_dbm"].as_i64().map(|r| format!(" · {r} dBm")).unwrap_or_default()),
        _ if w["enabled"] == true && w["on"] == false => "on · parked by the Wi-Fi saver".into(),
        _ if w["enabled"] == true || w["on"] == true => "on · not connected".into(),
        _ => "off".into(),
    }
}

fn mobile_detail(m: &Value, operator: Option<&str>, radio: Option<&str>) -> String {
    let mut p = vec![];
    if let Some(o) = operator {
        p.push(o.to_string());
    }
    if let Some(r) = radio {
        p.push(r.to_string());
    }
    if let Some(s) = m["state"].as_str() {
        p.push(s.to_string());
    }
    if p.is_empty() {
        if m["on"] == true { "on".into() } else { "off".into() }
    } else {
        p.join(" · ")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn status() -> Value {
        json!({"cap":"device.status","effect":"read","ok":true,"state":{"autosleep":true,"battery":{"percent":55},"bluetooth":true,"brightness_pct":25,
            "calls_enabled":true,"network":{"mobile_data":{"ipv4":"10.0.0.1","on":true,"state":"up"},"operator":"Vodafone GR","radio":"4G","uplink":"mobile",
            "wifi":{"connected":false,"enabled":true,"on":true,"ssid":null}},"thermal_max_c":77.0,"vibration":true,"volume":4}})
    }

    #[test]
    fn status_fills_every_row() {
        let mut d = Dev::default();
        d.begin("device.status");
        d.answer("device.status", 200, &status());
        assert!(d.pending.is_empty());
        for t in Toggle::ALL {
            assert_eq!(d.on.get(&t), Some(&true), "{t:?}");
        }
        assert_eq!(d.volume, Some(4));
        assert_eq!(d.brightness, Some(25));
        assert_eq!(d.detail[&Toggle::Wifi], "on · not connected");
        assert_eq!(d.detail[&Toggle::Mobile], "Vodafone GR · 4G · up");
        assert_eq!(d.available, Some(true));
    }

    #[test]
    fn toggle_uses_the_read_back_state_and_errors_stay_on_the_row() {
        let mut d = Dev::default();
        d.answer("device.status", 200, &status());
        assert_eq!(d.toggle_request(Toggle::Wifi), ("wifi.set", json!({"on": false})));
        d.begin("wifi.set");
        assert!(d.busy(Toggle::Wifi));
        d.answer("wifi.set", 409, &json!({"ok": false, "cap": "wifi.set", "code": "would_strand", "error": "no other uplink"}));
        assert!(!d.busy(Toggle::Wifi));
        assert_eq!(d.on[&Toggle::Wifi], true, "a refused change keeps the old state");
        assert!(d.error["Wi-Fi"].contains("offline"));
        // success: the state is what s22d read back, not the request
        d.answer("wifi.set", 200, &json!({"ok": true, "state": {"enabled": false, "on": false, "connected": false}}));
        assert_eq!(d.on[&Toggle::Wifi], false);
        assert!(!d.error.contains_key("Wi-Fi"));
        d.answer("bluetooth.set", 200, &json!({"ok": true, "state": {"on": false, "hci0": false, "detail": "down"}}));
        assert_eq!(d.on[&Toggle::Bluetooth], false);
        d.answer("calls.set", 200, &json!({"ok": true, "state": {"enabled": false, "audio": "two-way audio not working yet"}}));
        assert_eq!(d.on[&Toggle::Calls], false);
        d.answer("vibration.set", 200, &json!({"ok": true, "state": {"enabled": false, "daemon": true}}));
        assert_eq!(d.on[&Toggle::Vibration], false);
    }

    #[test]
    fn volume_brightness_timeout_and_no_daemon() {
        let mut d = Dev::default();
        d.answer("volume.get", 200, &json!({"ok": true, "state": {"level": 0, "muted": true, "max": 10}}));
        assert_eq!((d.volume, d.muted), (Some(0), true));
        d.answer("brightness.set", 200, &json!({"ok": true, "state": {"percent": 40, "brightness": 1000}}));
        assert_eq!(d.brightness, Some(40));
        d.answer("screen_timeout.get", 200, &json!({"ok": true, "state": {"unlocked_s": 120, "locked_s": 15, "never": 0}}));
        assert_eq!(d.timeout, Some((120, 15)));
        d.answer("device.status", 0, &json!({"ok": false, "code": "no_s22d", "error": "connection refused"}));
        assert_eq!(d.available, Some(false));
        assert_eq!(d.error["device.status"], "s22d not reachable");
    }

    #[test]
    fn http_answer_parsing_and_a_real_socket() {
        let (st, v) = parse_response(b"HTTP/1.1 409 Conflict\r\nContent-Length: 12\r\n\r\n{\"ok\":false}");
        assert_eq!((st, v), (409, json!({"ok": false})));
        let dir = std::env::temp_dir().join(format!("fs-dev-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let p = dir.join("s.sock");
        let l = std::os::unix::net::UnixListener::bind(&p).unwrap();
        let h = std::thread::spawn(move || {
            let (mut c, _) = l.accept().unwrap();
            let mut buf = [0u8; 4096];
            let n = c.read(&mut buf).unwrap();
            let req = String::from_utf8_lossy(&buf[..n]).to_string();
            c.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"ok\":true,\"state\":{\"percent\":30}}").unwrap();
            req
        });
        let (st, v) = call(p.to_str().unwrap(), "brightness.set", &json!({"value": 30}), 5);
        let req = h.join().unwrap();
        assert!(req.starts_with("POST /v1/device/brightness.set HTTP/1.1\r\n"));
        assert!(req.contains("X-S22-Caller: shell") && req.ends_with("{\"value\":30}"));
        assert_eq!((st, v["state"]["percent"].as_i64()), (200, Some(30)));
        assert_eq!(call("/nonexistent/sock", "x", &json!({}), 1).0, 0);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
