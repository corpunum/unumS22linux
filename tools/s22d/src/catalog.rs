//! The capability table: the machine-readable half of `API.md`. `GET /v1/capabilities` serves it
//! (with live availability), and `capabilities.json` is a checked-in snapshot the plugin's
//! contract tests run against. Change a route and this table together, then regenerate with
//! `s22d --print-capabilities > capabilities.json` (a test fails until you do).

use serde_json::{json, Value};

pub const BT_REASON: &str = "HCI raw-socket kernel panic: disabled";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Risk {
    /// Observes only.
    Read,
    /// Changes state that can be undone; logged in the audit file.
    Reversible,
    /// Cannot be undone or costs money/privacy; the owner must tap yes on the phone first.
    Risky,
}

impl Risk {
    pub fn as_str(self) -> &'static str {
        match self {
            Risk::Read => "read",
            Risk::Reversible => "reversible",
            Risk::Risky => "risky",
        }
    }
}

pub struct CapDef {
    pub id: &'static str,
    /// Name of the OpenUnum tool the s22-device plugin generates for this endpoint.
    pub tool: &'static str,
    pub description: &'static str,
    pub method: &'static str,
    pub path: &'static str,
    pub risk: Risk,
    /// How long a client should wait for the answer (owner prompts and camera captures are slow).
    pub timeout_s: u64,
    pub schema: Value,
}

fn obj(props: Value, required: &[&str]) -> Value {
    json!({"type": "object", "properties": props, "required": required, "additionalProperties": false})
}

fn none() -> Value {
    obj(json!({}), &[])
}

#[allow(clippy::too_many_arguments)]
fn def(
    id: &'static str,
    tool: &'static str,
    method: &'static str,
    path: &'static str,
    risk: Risk,
    timeout_s: u64,
    description: &'static str,
    schema: Value,
) -> CapDef {
    CapDef {
        id,
        tool,
        description,
        method,
        path,
        risk,
        timeout_s,
        schema,
    }
}

pub fn defs() -> Vec<CapDef> {
    use Risk::*;
    vec![
        def("status", "device_status", "GET", "/v1/status", Read, 15,
            "Overall phone status: uptime, load, memory, disk, battery, thermal maximum, network interfaces, modem, display, audio.", none()),
        def("battery", "device_battery", "GET", "/v1/battery", Read, 15,
            "Battery level, charging state, temperature and voltage.", none()),
        def("thermal", "device_thermal", "GET", "/v1/thermal", Read, 15,
            "All thermal zones in degrees Celsius and the hottest one.", none()),
        def("processes.top", "processes_top", "GET", "/v1/processes/top", Read, 15,
            "Busiest processes by CPU or resident memory.",
            obj(json!({"sort": {"type": "string", "enum": ["cpu", "mem"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}), &[])),
        def("logs.dmesg", "dmesg_read", "GET", "/v1/logs/dmesg", Read, 15,
            "Kernel log records. Pass the previous last_seq as since to read only new records.",
            obj(json!({"since": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000}}), &[])),
        def("wifi.status", "wifi_status", "GET", "/v1/wifi/status", Read, 20,
            "Wi-Fi connection state, SSID, IP address, signal strength.", none()),
        def("wifi.scan", "wifi_scan", "POST", "/v1/wifi/scan", Read, 25,
            "Scan for nearby Wi-Fi networks (takes a few seconds).", none()),
        def("wifi.connect", "wifi_connect", "POST", "/v1/wifi/connect", Reversible, 60,
            "Join a Wi-Fi network until the supplicant restarts. On failure the previous network is restored. Omit password for an open network.",
            obj(json!({"ssid": {"type": "string", "minLength": 1, "maxLength": 32}, "password": {"type": "string", "minLength": 8, "maxLength": 63}}), &["ssid"])),
        def("wifi.disconnect", "wifi_disconnect", "POST", "/v1/wifi/disconnect", Reversible, 20,
            "Drop the Wi-Fi connection. Remote access over Wi-Fi ends until wifi_reconnect runs on the phone.", none()),
        def("wifi.reconnect", "wifi_reconnect", "POST", "/v1/wifi/reconnect", Reversible, 20,
            "Re-enable saved Wi-Fi profiles and reconnect.", none()),
        def("bt.status", "bt_status", "GET", "/v1/bt/status", Read, 15,
            "Bluetooth availability. Currently always unavailable (HCI kernel panic fix pending).", none()),
        def("bt.power", "bt_power", "POST", "/v1/bt/power", Reversible, 15,
            "Bluetooth power. Unavailable: raw HCI sockets panic the kernel.", obj(json!({"on": {"type": "boolean"}}), &["on"])),
        def("bt.scan", "bt_scan", "POST", "/v1/bt/scan", Read, 15,
            "Bluetooth scan. Unavailable: raw HCI sockets panic the kernel.", none()),
        def("modem.status", "modem_status", "GET", "/v1/modem/status", Read, 25,
            "Modem, SIM, registration and signal from s22-phoned.", none()),
        def("sms.list", "device_sms_inbox", "GET", "/v1/sms", Read, 25,
            "Received (inbox) or sent (outbox) text messages.",
            obj(json!({"box": {"type": "string", "enum": ["inbox", "outbox"]}, "since": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}), &[])),
        def("sms.send", "device_sms_send", "POST", "/v1/sms/send", Risky, 95,
            "Send a text message. The owner must confirm on the phone; s22-phoned still enforces its number allowlist and rate limits.",
            obj(json!({"number": {"type": "string", "description": "E.164 number such as +306900000000"}, "text": {"type": "string", "minLength": 1, "maxLength": 1000}}), &["number", "text"])),
        def("call.list", "device_call_list", "GET", "/v1/calls", Read, 25,
            "Current and recent calls.", none()),
        def("call.dial", "device_call_dial", "POST", "/v1/call/dial", Risky, 95,
            "Place a call. The owner must confirm on the phone; s22-phoned still enforces its allowlist. Call audio is not available yet.",
            obj(json!({"number": {"type": "string"}}), &["number"])),
        def("call.hangup", "device_call_hangup", "POST", "/v1/call/hangup", Reversible, 25,
            "End the current call.", none()),
        def("camera.status", "camera_status", "GET", "/v1/camera/status", Read, 15,
            "Camera availability, busy/cooldown state and the last capture.", none()),
        def("camera.capture", "camera_capture", "POST", "/v1/camera/capture", Reversible, 330,
            "Capture one still. One capture at a time; repeated or failed captures are rate limited because camera DMA faults can panic the kernel. Exposure is in microseconds, gain is analog 1-16.",
            obj(json!({"sensor": {"type": "string", "enum": ["rear", "front"]}, "exposure_us": {"type": "integer", "minimum": 100, "maximum": 30000}, "gain": {"type": "number", "minimum": 1, "maximum": 16}, "develop": {"type": "boolean"}}), &["sensor"])),
        def("display.status", "display_status", "GET", "/v1/display", Read, 15,
            "Screen state (on/off) and brightness.", none()),
        def("display.on", "display_on", "POST", "/v1/display/on", Reversible, 35,
            "Turn the screen on.", none()),
        def("display.off", "display_off", "POST", "/v1/display/off", Reversible, 35,
            "Turn the screen off (the touch UI locks first).", none()),
        def("display.brightness", "display_brightness", "POST", "/v1/display/brightness", Reversible, 15,
            "Set backlight brightness in percent (1-100).",
            obj(json!({"percent": {"type": "integer", "minimum": 1, "maximum": 100}}), &["percent"])),
        def("audio.volume.get", "audio_volume", "GET", "/v1/audio/volume", Read, 15,
            "Saved volume level and whether the owner allows sound.", none()),
        def("audio.volume.set", "audio_mute", "POST", "/v1/audio/volume", Reversible, 15,
            "Set the volume. Only 0 (mute) is accepted unless the owner created /etc/s22-audio-unmuted; a refusal is final.",
            obj(json!({"value": {"type": "integer", "minimum": 0, "maximum": 100}}), &["value"])),
        def("service.restart", "service_restart", "POST", "/v1/services/{name}/restart", Reversible, 40,
            "Restart an allowlisted service through s22-keepalive: openunum, unumsearch, modem, phoned or llama.",
            obj(json!({"name": {"type": "string", "enum": ["openunum", "unumsearch", "modem", "phoned", "llama"]}}), &["name"])),
        def("system.reboot-recovery", "recovery_mode", "POST", "/v1/system/reboot-recovery", Risky, 95,
            "Reboot the phone with s22-reboot recovery (about 40 s offline). The owner must confirm on the phone.", none()),
    ]
}

/// The table with live availability filled in by `avail(id)`: `None` means usable.
pub fn capabilities(avail: &dyn Fn(&str) -> Option<String>) -> Value {
    let rows: Vec<Value> = defs()
        .iter()
        .map(|d| {
            let reason = avail(d.id);
            json!({
                "id": d.id,
                "tool": d.tool,
                "description": d.description,
                "method": d.method,
                "path": d.path,
                "risk": d.risk.as_str(),
                "confirm": if d.risk == Risk::Risky { "owner" } else { "none" },
                "timeout_s": d.timeout_s,
                "available": reason.is_none(),
                "reason": reason,
                "input_schema": d.schema,
            })
        })
        .collect();
    json!({"ok": true, "api_version": 1, "capabilities": rows})
}

/// Availability with every backend present: only Bluetooth power and scan are permanently off.
pub fn static_availability(id: &str) -> Option<String> {
    matches!(id, "bt.power" | "bt.scan").then(|| BT_REASON.to_string())
}
