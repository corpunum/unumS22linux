//! Route-level tests: the whole router, real policy code, fake hardware.

use crate::app::{App, Audit, Shared};
use crate::backends::*;
use crate::catalog;
use crate::config::Config;
use crate::error::{ApiError, ApiResult};
use crate::routes::{router, Transport};
use crate::testutil::Tmp;
use axum::{
    body::Body,
    extract::ConnectInfo,
    http::{Request, StatusCode},
};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tower::ServiceExt;

/// One fake that implements every backend trait, records calls and fails on demand.
#[derive(Default)]
struct Fake {
    calls: Mutex<Vec<String>>,
    fail: Mutex<HashMap<String, ApiError>>,
    unavailable: Mutex<HashMap<String, String>>,
    answer: Mutex<Option<Answer>>,
    gate: Mutex<Option<Receiver<()>>>,
    confirm_gate: Mutex<Option<Receiver<()>>>,
    audio: Mutex<Option<u8>>,
    /// When set, a capture answers with this file as its `path` (the remote listener reads it).
    capture_file: Mutex<Option<String>>,
}

impl Fake {
    fn rec(&self, name: &str) -> ApiResult {
        self.calls.lock().unwrap().push(name.to_string());
        if let Some(e) = self
            .fail
            .lock()
            .unwrap()
            .get(name.split(' ').next().unwrap())
        {
            return Err(e.clone());
        }
        Ok(json!({"fake": name}))
    }
    fn called(&self, name: &str) -> usize {
        self.calls
            .lock()
            .unwrap()
            .iter()
            .filter(|c| c.starts_with(name))
            .count()
    }
    fn avail(&self, k: &str) -> Option<String> {
        self.unavailable.lock().unwrap().get(k).cloned()
    }
}

impl Telemetry for Fake {
    fn status(&self) -> ApiResult {
        self.rec("status")
    }
    fn battery(&self) -> ApiResult {
        self.rec("battery")
    }
    fn thermal(&self) -> ApiResult {
        self.rec("thermal")
    }
    fn top(&self, sort: TopSort, limit: usize) -> ApiResult {
        self.rec(&format!("top {:?} {limit}", sort))
    }
    fn dmesg(&self, since: Option<u64>, limit: usize) -> ApiResult {
        self.rec(&format!("dmesg {since:?} {limit}"))
    }
}
impl Wifi for Fake {
    fn availability(&self) -> Option<String> {
        self.avail("wifi")
    }
    fn status(&self) -> ApiResult {
        self.rec("wifi_status")
    }
    fn scan(&self) -> ApiResult {
        self.rec("wifi_scan")
    }
    fn connect(&self, r: &WifiConnect) -> ApiResult {
        self.rec(&format!("wifi_connect {} {:?}", r.ssid, r.password))
    }
    fn disconnect(&self) -> ApiResult {
        self.rec("wifi_disconnect")
    }
    fn reconnect(&self) -> ApiResult {
        self.rec("wifi_reconnect")
    }
}
impl Phoned for Fake {
    fn availability(&self) -> Option<String> {
        self.avail("phoned")
    }
    fn modem_status(&self) -> ApiResult {
        self.rec("modem_status")
    }
    fn sms_list(&self, outbox: bool, since: Option<String>, limit: usize) -> ApiResult {
        self.rec(&format!(
            "sms_list outbox={outbox} since={since:?} limit={limit}"
        ))
    }
    fn sms_send(&self, n: &str, t: &str) -> ApiResult {
        self.rec(&format!("sms_send {n} {t}"))
    }
    fn calls(&self) -> ApiResult {
        self.rec("calls")
    }
    fn dial(&self, n: &str) -> ApiResult {
        self.rec(&format!("dial {n}"))
    }
    fn hangup(&self) -> ApiResult {
        self.rec("hangup")
    }
    fn mobile_data_status(&self) -> ApiResult {
        self.rec("mobile_data_status")
    }
    fn mobile_data_up(&self, apn: Option<&str>) -> ApiResult {
        self.rec(&format!("mobile_data_up {apn:?}"))
    }
    fn mobile_data_down(&self) -> ApiResult {
        self.rec("mobile_data_down")
    }
}
impl Camera for Fake {
    fn availability(&self) -> Option<String> {
        self.avail("camera")
    }
    fn status(&self) -> ApiResult {
        self.rec("camera_status")
    }
    fn capture(&self, r: &CaptureReq) -> ApiResult {
        if let Some(rx) = self.gate.lock().unwrap().take() {
            let _ = rx.recv_timeout(Duration::from_secs(5));
        }
        let out = self.rec(&format!(
            "capture {} {:?} {:?} {}",
            r.sensor, r.exposure_us, r.gain, r.develop
        ))?;
        match self.capture_file.lock().unwrap().clone() {
            Some(p) => {
                // Like the real camera: the work file plus a copy in the gallery.
                let g = format!("{p}.gallery.jpg");
                let _ = std::fs::copy(&p, &g);
                Ok(json!({"path": p, "gallery_path": g, "developed": true}))
            }
            None => Ok(out),
        }
    }
    fn record(&self, r: &RecordReq) -> ApiResult {
        self.rec(&format!(
            "record {} {} {:?} {:?} {:?}",
            r.sensor, r.seconds, r.fps, r.exposure_us, r.gain
        ))
    }
}
impl Display for Fake {
    fn availability(&self) -> Option<String> {
        self.avail("display")
    }
    fn status(&self) -> ApiResult {
        self.rec("display_status")
    }
    fn set_power(&self, on: bool) -> ApiResult {
        self.rec(&format!("display_power {on}"))
    }
    fn set_brightness(&self, p: u8) -> ApiResult {
        self.rec(&format!("display_brightness {p}"))
    }
}
impl Services for Fake {
    fn availability(&self) -> Option<String> {
        self.avail("service")
    }
    fn restart(&self, name: &str) -> ApiResult {
        self.rec(&format!("restart {name}"))
    }
}
impl Recovery for Fake {
    fn availability(&self) -> Option<String> {
        self.avail("system")
    }
    fn schedule(&self) -> ApiResult {
        self.rec("reboot")
    }
}
impl Audio for Fake {
    fn level(&self) -> Option<u8> {
        *self.audio.lock().unwrap()
    }
    fn set_level(&self, l: u8) -> ApiResult {
        *self.audio.lock().unwrap() = Some(l);
        self.rec(&format!("set_level {l}"))
    }
}
impl Confirmer for Fake {
    fn ask(&self, q: &str, _: u64) -> Answer {
        self.calls.lock().unwrap().push(format!("ask {q}"));
        if let Some(rx) = self.confirm_gate.lock().unwrap().take() {
            let _ = rx.recv_timeout(Duration::from_secs(5));
        }
        self.answer.lock().unwrap().clone().unwrap_or(Answer::No)
    }
}

struct H {
    app: Shared,
    fake: Arc<Fake>,
    tmp: Tmp,
}

impl H {
    fn new() -> Self {
        Self::with(|_| {})
    }

    fn with(f: impl FnOnce(&mut Config)) -> Self {
        let tmp = Tmp::new();
        let mut cfg = Config::rooted(tmp.path());
        f(&mut cfg);
        cfg.camera_min_interval = Duration::ZERO;
        cfg.camera_failure_cooldown = Duration::from_secs(60);
        cfg.wifi_wait = Duration::ZERO;
        let cfg = Arc::new(cfg);
        let fake = Arc::new(Fake::default());
        let b = Backends {
            telemetry: fake.clone(),
            wifi: fake.clone(),
            phoned: fake.clone(),
            camera: fake.clone(),
            display: fake.clone(),
            services: fake.clone(),
            recovery: fake.clone(),
            audio: fake.clone(),
            confirmer: fake.clone(),
        };
        let audit = Audit::open(&cfg.audit_path).unwrap();
        Self {
            app: App::new(cfg, b, audit),
            fake,
            tmp,
        }
    }

    fn answer(&self, a: Answer) {
        *self.fake.answer.lock().unwrap() = Some(a);
    }

    fn fail(&self, op: &str, e: ApiError) {
        self.fake.fail.lock().unwrap().insert(op.to_string(), e);
    }

    fn unavailable(&self, k: &str, why: &str) {
        self.fake
            .unavailable
            .lock()
            .unwrap()
            .insert(k.to_string(), why.to_string());
    }

    async fn send(&self, method: &str, uri: &str, body: Option<Value>) -> (StatusCode, Value) {
        let mut b = Request::builder().method(method).uri(uri);
        if body.is_some() {
            b = b.header("content-type", "application/json");
        }
        let req = b
            .body(
                body.map(|v| Body::from(v.to_string()))
                    .unwrap_or_else(Body::empty),
            )
            .unwrap();
        self.run(req, Transport::Unix).await
    }

    async fn run(&self, req: Request<Body>, t: Transport) -> (StatusCode, Value) {
        let r = router(self.app.clone(), t).oneshot(req).await.unwrap();
        let status = r.status();
        let bytes = axum::body::to_bytes(r.into_body(), 1 << 20).await.unwrap();
        (
            status,
            serde_json::from_slice(&bytes).unwrap_or(Value::Null),
        )
    }

    fn audit_lines(&self) -> Vec<Value> {
        std::fs::read_to_string(&self.app.cfg.audit_path)
            .unwrap()
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect()
    }
}

fn code(v: &Value) -> &str {
    v["code"].as_str().unwrap_or("")
}

// ------------------------------------------------------------ contract

#[test]
fn catalog_snapshot_is_current() {
    let want = serde_json::to_string_pretty(&catalog::capabilities(&catalog::static_availability))
        .unwrap();
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/capabilities.json");
    if std::env::var("UPDATE_CAPABILITIES").is_ok() {
        std::fs::write(path, format!("{want}\n")).unwrap();
    }
    let have = std::fs::read_to_string(path)
        .expect("capabilities.json missing; run UPDATE_CAPABILITIES=1 cargo test");
    assert_eq!(
        have.trim(),
        want.trim(),
        "capabilities.json is stale: UPDATE_CAPABILITIES=1 cargo test"
    );
}

#[test]
fn device_catalog_snapshot_is_current() {
    let want = serde_json::to_string_pretty(&crate::device::capabilities(&|_| None)).unwrap();
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/device-capabilities.json");
    if std::env::var("UPDATE_CAPABILITIES").is_ok() {
        std::fs::write(path, format!("{want}\n")).unwrap();
    }
    let have = std::fs::read_to_string(path)
        .expect("device-capabilities.json missing; run UPDATE_CAPABILITIES=1 cargo test");
    assert_eq!(
        have.trim(),
        want.trim(),
        "device-capabilities.json is stale: UPDATE_CAPABILITIES=1 cargo test"
    );
}

#[test]
fn api_md_documents_every_capability() {
    let md = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/API.md")).unwrap();
    for d in catalog::defs() {
        assert!(
            md.contains(&format!("`{}`", d.id)),
            "API.md does not mention capability {}",
            d.id
        );
        assert!(md.contains(d.path), "API.md does not mention {}", d.path);
        assert!(
            md.contains(d.tool),
            "API.md does not mention tool {}",
            d.tool
        );
    }
}

#[test]
fn catalog_ids_tools_and_routes_are_unique_and_risky_ones_confirm() {
    let defs = catalog::defs();
    let mut ids: Vec<_> = defs.iter().map(|d| d.id).collect();
    ids.sort();
    ids.dedup();
    assert_eq!(ids.len(), defs.len());
    let mut tools: Vec<_> = defs.iter().map(|d| d.tool).collect();
    tools.sort();
    tools.dedup();
    assert_eq!(tools.len(), defs.len());
    let risky: Vec<_> = defs
        .iter()
        .filter(|d| d.risk == catalog::Risk::Risky)
        .map(|d| d.id)
        .collect();
    assert_eq!(risky, ["sms.send", "call.dial", "system.reboot-recovery"]);
}

#[tokio::test]
async fn every_catalog_route_exists() {
    let h = H::new();
    for d in catalog::defs() {
        let path = d.path.replace("{name}", "openunum");
        let (s, v) = h.send(d.method, &path, None).await;
        assert_ne!(
            s,
            StatusCode::NOT_FOUND,
            "{} {} is not routed",
            d.method,
            d.path
        );
        assert_ne!(s, StatusCode::METHOD_NOT_ALLOWED, "{} {}", d.method, d.path);
        assert!(v.is_object(), "{} {} did not answer JSON", d.method, d.path);
    }
}

#[tokio::test]
async fn capabilities_report_live_availability() {
    let h = H::new();
    h.unavailable("phoned", "s22-phoned socket missing");
    let (s, v) = h.send("GET", "/v1/capabilities", None).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["api_version"], 1);
    let caps = v["capabilities"].as_array().unwrap();
    let get = |id: &str| caps.iter().find(|c| c["id"] == id).unwrap().clone();
    assert_eq!(get("status")["available"], true);
    assert_eq!(get("sms.send")["available"], false);
    assert_eq!(get("sms.send")["reason"], "s22-phoned socket missing");
    assert_eq!(get("sms.send")["confirm"], "unlisted_number");
    assert_eq!(get("system.reboot-recovery")["confirm"], "owner");
    assert_eq!(get("sms.send")["superseded_by"], "phone_device");
    assert!(get("wifi.scan")["superseded_by"].is_null());
    assert_eq!(get("bt.scan")["available"], false);
    assert_eq!(get("bt.scan")["reason"], catalog::BT_REASON);
    assert_eq!(get("wifi.connect")["input_schema"]["required"][0], "ssid");
}

// ------------------------------------------------------------ transport guards

fn tcp(method: &str, uri: &str, peer: Option<&str>, headers: &[(&str, &str)]) -> Request<Body> {
    let mut b = Request::builder().method(method).uri(uri);
    for (k, v) in headers {
        b = b.header(*k, *v);
    }
    let mut r = b.body(Body::empty()).unwrap();
    if let Some(p) = peer {
        r.extensions_mut()
            .insert(ConnectInfo(p.parse::<SocketAddr>().unwrap()));
    }
    r
}

#[tokio::test]
async fn tcp_listener_accepts_only_loopback_peers() {
    let h = H::new();
    let (s, v) = h
        .run(
            tcp(
                "GET",
                "/v1/status",
                Some("127.0.0.1:5000"),
                &[("host", "127.0.0.1:8766")],
            ),
            Transport::Tcp,
        )
        .await;
    assert_eq!(s, StatusCode::OK, "{v}");
    let (s, _) = h
        .run(
            tcp("GET", "/v1/status", Some("[::1]:5000"), &[]),
            Transport::Tcp,
        )
        .await;
    assert_eq!(s, StatusCode::OK);
    for peer in ["203.0.113.50:5000", "192.0.2.16:5000", "10.0.0.2:1"] {
        let (s, v) = h
            .run(tcp("GET", "/v1/status", Some(peer), &[]), Transport::Tcp)
            .await;
        assert_eq!(
            (s, code(&v)),
            (StatusCode::FORBIDDEN, "non_loopback"),
            "{peer}"
        );
    }
    assert_eq!(h.fake.called("status"), 2);
}

#[tokio::test]
async fn tcp_without_peer_info_fails_closed() {
    let h = H::new();
    let (s, v) = h
        .run(tcp("GET", "/v1/status", None, &[]), Transport::Tcp)
        .await;
    assert_eq!((s, code(&v)), (StatusCode::FORBIDDEN, "non_loopback"));
}

#[tokio::test]
async fn browser_requests_and_dns_rebinding_are_refused() {
    let h = H::new();
    let (s, v) = h
        .run(
            tcp(
                "GET",
                "/v1/status",
                Some("127.0.0.1:1"),
                &[("origin", "http://evil.example")],
            ),
            Transport::Tcp,
        )
        .await;
    assert_eq!((s, code(&v)), (StatusCode::FORBIDDEN, "browser_origin"));
    let (s, v) = h
        .run(
            tcp(
                "GET",
                "/v1/status",
                Some("127.0.0.1:1"),
                &[("host", "evil.example:8766")],
            ),
            Transport::Tcp,
        )
        .await;
    assert_eq!((s, code(&v)), (StatusCode::FORBIDDEN, "bad_host"));
    let (s, _) = h
        .run(
            tcp(
                "GET",
                "/v1/status",
                Some("127.0.0.1:1"),
                &[("host", "localhost:8766")],
            ),
            Transport::Tcp,
        )
        .await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(h.fake.called("status"), 1);
}

#[tokio::test]
async fn errors_use_one_json_format() {
    let h = H::new();
    let (s, v) = h.send("GET", "/v1/nope", None).await;
    assert_eq!(
        (s, code(&v), v["ok"].clone()),
        (StatusCode::NOT_FOUND, "not_found", json!(false))
    );
    let (s, v) = h.send("DELETE", "/v1/status", None).await;
    assert_eq!(
        (s, code(&v)),
        (StatusCode::METHOD_NOT_ALLOWED, "method_not_allowed")
    );
    let req = Request::builder()
        .method("POST")
        .uri("/v1/wifi/connect")
        .header("content-type", "application/json")
        .body(Body::from("{not json"))
        .unwrap();
    let (s, v) = h.run(req, Transport::Unix).await;
    assert_eq!((s, code(&v)), (StatusCode::BAD_REQUEST, "bad_request"));
    let (s, v) = h
        .send("POST", "/v1/wifi/connect", Some(json!({"password": "x"})))
        .await;
    assert_eq!(
        (s, code(&v)),
        (StatusCode::BAD_REQUEST, "bad_request"),
        "missing ssid"
    );
    let (s, _) = h.send("GET", "/v1/top?limit=abc", None).await;
    assert_eq!(s, StatusCode::NOT_FOUND);
    let (s, v) = h.send("GET", "/v1/processes/top?limit=abc", None).await;
    assert_eq!((s, code(&v)), (StatusCode::BAD_REQUEST, "bad_request"));
}

// ------------------------------------------------------------ reads

#[tokio::test]
async fn read_endpoints_return_backend_data_and_are_not_audited() {
    let h = H::new();
    for (uri, key) in [
        ("/v1/status", "status"),
        ("/v1/battery", "battery"),
        ("/v1/thermal", "thermal"),
        ("/v1/wifi/status", "wifi_status"),
        ("/v1/modem/status", "modem_status"),
        ("/v1/calls", "calls"),
        ("/v1/camera/status", "camera_status"),
        ("/v1/display", "display_status"),
    ] {
        let (s, v) = h.send("GET", uri, None).await;
        assert_eq!(s, StatusCode::OK, "{uri}: {v}");
        assert_eq!(v["ok"], true);
        assert!(h.fake.called(key) >= 1, "{uri}");
    }
    assert_eq!(h.audit_lines().len(), 0);
}

#[tokio::test]
async fn query_parameters_are_validated_and_forwarded() {
    let h = H::new();
    let (_, v) = h
        .send("GET", "/v1/processes/top?sort=mem&limit=3", None)
        .await;
    assert_eq!(v["fake"], "top Mem 3");
    let (_, v) = h.send("GET", "/v1/processes/top", None).await;
    assert_eq!(v["fake"], "top Cpu 10");
    for bad in ["limit=0", "limit=51", "sort=disk"] {
        let (s, _) = h
            .send("GET", &format!("/v1/processes/top?{bad}"), None)
            .await;
        assert_eq!(s, StatusCode::BAD_REQUEST, "{bad}");
    }
    let (_, v) = h.send("GET", "/v1/logs/dmesg?since=42&limit=5", None).await;
    assert_eq!(v["fake"], "dmesg Some(42) 5");
    let (s, _) = h.send("GET", "/v1/logs/dmesg?limit=1001", None).await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
    let (_, v) = h
        .send("GET", "/v1/sms?box=outbox&since=m9&limit=7", None)
        .await;
    assert_eq!(v["fake"], "sms_list outbox=true since=Some(\"m9\") limit=7");
    let (_, v) = h.send("GET", "/v1/sms", None).await;
    assert_eq!(v["fake"], "sms_list outbox=false since=None limit=20");
    let (s, _) = h.send("GET", "/v1/sms?box=trash", None).await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn backend_failures_become_json_errors_with_their_status() {
    let h = H::new();
    h.fail("status", ApiError::unavailable("/proc unreadable"));
    let (s, v) = h.send("GET", "/v1/status", None).await;
    assert_eq!(
        (s, code(&v), v["error"].clone()),
        (
            StatusCode::SERVICE_UNAVAILABLE,
            "unavailable",
            json!("/proc unreadable")
        )
    );
    h.fail("modem_status", ApiError::upstream("phoned answered 500"));
    let (s, v) = h.send("GET", "/v1/modem/status", None).await;
    assert_eq!((s, code(&v)), (StatusCode::BAD_GATEWAY, "upstream_error"));
}

#[tokio::test]
async fn bluetooth_goes_through_s22_bt_and_scan_stays_disabled() {
    let h = H::new();
    let (s, v) = h.send("GET", "/v1/bt/status", None).await;
    assert_eq!(
        (s, code(&v)),
        (StatusCode::SERVICE_UNAVAILABLE, "unavailable")
    );
    let state = h.tmp.path().join("bt-state");
    h.tmp.script(
        "usr/local/bin/s22-bt",
        &format!(
            "case \"$1\" in up) echo up > {s};; down) echo down > {s};; status) cat {s} 2>/dev/null || echo down;; esac",
            s = state.display()
        ),
    );
    let (s, v) = h
        .send("POST", "/v1/bt/power", Some(json!({"on": true})))
        .await;
    assert_eq!((s, v["on"].clone()), (StatusCode::OK, json!(true)), "{v}");
    let (_, v) = h.dev("bluetooth.set", json!({"on": false})).await;
    assert_eq!(v["state"]["on"], false, "{v}");
    let (s, v) = h.send("POST", "/v1/bt/scan", None).await;
    assert_eq!(
        (s, code(&v)),
        (StatusCode::SERVICE_UNAVAILABLE, "unavailable")
    );
    assert_eq!(v["error"], catalog::BT_REASON);
}

// ------------------------------------------------------------ Wi-Fi

#[tokio::test]
async fn wifi_actions_run_and_are_audited_without_the_password() {
    let h = H::new();
    let (s, v) = h
        .send(
            "POST",
            "/v1/wifi/connect",
            Some(json!({"ssid": "Lab", "password": "hunter2hunter2"})),
        )
        .await;
    assert_eq!(s, StatusCode::OK, "{v}");
    assert_eq!(
        h.fake.called("wifi_connect Lab Some(\"hunter2hunter2\")"),
        1
    );
    for p in ["/v1/wifi/scan", "/v1/wifi/disconnect", "/v1/wifi/reconnect"] {
        assert_eq!(h.send("POST", p, None).await.0, StatusCode::OK, "{p}");
    }
    let log = std::fs::read_to_string(&h.app.cfg.audit_path).unwrap();
    assert!(
        !log.contains("hunter2"),
        "password leaked into the audit log"
    );
    let lines = h.audit_lines();
    assert_eq!(lines.len(), 4);
    assert_eq!(lines[0]["action"], "wifi.connect");
    assert_eq!(lines[0]["detail"]["ssid"], "Lab");
    assert_eq!(lines[0]["detail"]["secured"], true);
    assert_eq!(lines[0]["risk"], "reversible");
    assert_eq!(lines[1]["risk"], "read");
}

#[tokio::test]
async fn wifi_failure_is_audited_as_failed() {
    let h = H::new();
    h.fail(
        "wifi_connect",
        ApiError::new(
            StatusCode::BAD_GATEWAY,
            "connect_failed",
            "did not associate",
        ),
    );
    let (s, v) = h
        .send("POST", "/v1/wifi/connect", Some(json!({"ssid": "Nope"})))
        .await;
    assert_eq!((s, code(&v)), (StatusCode::BAD_GATEWAY, "connect_failed"));
    let l = &h.audit_lines()[0];
    assert_eq!(l["outcome"], "failed");
    assert_eq!(l["detail"]["code"], "connect_failed");
}

// ------------------------------------------------------------ risky: SMS, calls, recovery

#[tokio::test]
async fn sms_needs_an_explicit_owner_yes() {
    let h = H::new();
    h.answer(Answer::Yes);
    let (s, v) = h
        .send(
            "POST",
            "/v1/sms/send",
            Some(json!({"number": "+306900000000", "text": "hello"})),
        )
        .await;
    assert_eq!(s, StatusCode::OK, "{v}");
    assert_eq!(h.fake.called("sms_send +306900000000 hello"), 1);
    assert_eq!(h.fake.called("ask Send this text to +306900000000?"), 1);
    let l = &h.audit_lines()[0];
    assert_eq!(
        (l["action"].clone(), l["risk"].clone(), l["outcome"].clone()),
        (json!("sms.send"), json!("risky"), json!("ok"))
    );
    assert_eq!(l["detail"]["text_chars"], 5);
    assert!(
        !std::fs::read_to_string(&h.app.cfg.audit_path)
            .unwrap()
            .contains("hello"),
        "message text must not be audited"
    );
}

#[tokio::test]
async fn every_non_yes_answer_blocks_every_risky_endpoint() {
    for (answer, status, want) in [
        (Answer::No, StatusCode::FORBIDDEN, "owner_denied"),
        (
            Answer::Timeout,
            StatusCode::REQUEST_TIMEOUT,
            "owner_timeout",
        ),
        (Answer::Busy, StatusCode::CONFLICT, "confirm_busy"),
        (
            Answer::Unavailable("no socket".into()),
            StatusCode::SERVICE_UNAVAILABLE,
            "confirm_unavailable",
        ),
    ] {
        let h = H::new();
        h.answer(answer.clone());
        for (path, body) in [
            (
                "/v1/sms/send",
                json!({"number": "+306900000000", "text": "x"}),
            ),
            ("/v1/call/dial", json!({"number": "+306900000000"})),
            ("/v1/system/reboot-recovery", json!({})),
        ] {
            let (s, v) = h.send("POST", path, Some(body)).await;
            assert_eq!((s, code(&v)), (status, want), "{path} with {answer:?}");
        }
        assert_eq!(h.fake.called("sms_send"), 0);
        assert_eq!(h.fake.called("dial"), 0);
        assert_eq!(h.fake.called("reboot"), 0);
        assert!(h
            .audit_lines()
            .iter()
            .all(|l| l["outcome"] == "refused" || l["outcome"] == "failed"));
    }
}

#[tokio::test]
async fn risky_requests_are_validated_before_the_owner_is_bothered() {
    let h = H::new();
    h.answer(Answer::Yes);
    for body in [
        json!({"number": "abc", "text": "x"}),
        json!({"number": "+306900000000", "text": ""}),
        json!({"number": "112;rm", "text": "x"}),
    ] {
        let (s, v) = h.send("POST", "/v1/sms/send", Some(body)).await;
        assert_eq!((s, code(&v)), (StatusCode::BAD_REQUEST, "bad_request"));
    }
    let long = "x".repeat(1001);
    let (s, _) = h
        .send(
            "POST",
            "/v1/sms/send",
            Some(json!({"number": "+306900000000", "text": long})),
        )
        .await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
    let (s, _) = h
        .send("POST", "/v1/call/dial", Some(json!({"number": "x"})))
        .await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
    assert_eq!(h.fake.called("ask"), 0);
}

#[tokio::test]
async fn unreachable_phoned_fails_before_asking_the_owner() {
    let h = H::new();
    h.answer(Answer::Yes);
    h.unavailable("phoned", "s22-phoned is not running");
    let (s, v) = h
        .send(
            "POST",
            "/v1/sms/send",
            Some(json!({"number": "+306900000000", "text": "x"})),
        )
        .await;
    assert_eq!(
        (s, code(&v)),
        (StatusCode::SERVICE_UNAVAILABLE, "unavailable")
    );
    let (s, _) = h
        .send(
            "POST",
            "/v1/call/dial",
            Some(json!({"number": "+306900000000"})),
        )
        .await;
    assert_eq!(s, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(h.fake.called("ask"), 0);
}

#[tokio::test]
async fn phoned_refusals_are_final_and_passed_through() {
    let h = H::new();
    h.answer(Answer::Yes);
    h.fail(
        "sms_send",
        ApiError::denied("refused_by_phoned", "refused: number not in allowlist"),
    );
    let (s, v) = h
        .send(
            "POST",
            "/v1/sms/send",
            Some(json!({"number": "+306900000000", "text": "x"})),
        )
        .await;
    assert_eq!((s, code(&v)), (StatusCode::FORBIDDEN, "refused_by_phoned"));
    assert!(v["error"].as_str().unwrap().contains("allowlist"));
    assert_eq!(h.fake.called("sms_send"), 1, "no retry");
}

#[tokio::test]
async fn only_one_owner_question_at_a_time() {
    let h = Arc::new(H::new());
    h.answer(Answer::Yes);
    let (tx, rx): (Sender<()>, Receiver<()>) = channel();
    *h.fake.confirm_gate.lock().unwrap() = Some(rx);
    let h2 = h.clone();
    let first = tokio::spawn(async move {
        h2.send(
            "POST",
            "/v1/call/dial",
            Some(json!({"number": "+306900000000"})),
        )
        .await
    });
    for _ in 0..200 {
        if h.fake.called("ask") == 1 {
            break;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    let (s, v) = h
        .send(
            "POST",
            "/v1/sms/send",
            Some(json!({"number": "+306900000000", "text": "x"})),
        )
        .await;
    assert_eq!((s, code(&v)), (StatusCode::CONFLICT, "confirm_busy"));
    tx.send(()).unwrap();
    assert_eq!(first.await.unwrap().0, StatusCode::OK);
    assert_eq!(h.fake.called("sms_send"), 0);
}

#[tokio::test]
async fn dial_and_hangup() {
    let h = H::new();
    h.answer(Answer::Yes);
    assert_eq!(
        h.send(
            "POST",
            "/v1/call/dial",
            Some(json!({"number": "+30 690 000 0000"}))
        )
        .await
        .0,
        StatusCode::OK
    );
    assert_eq!(h.fake.called("dial +30 690 000 0000"), 1);
    let before = h.fake.called("ask");
    assert_eq!(
        h.send("POST", "/v1/call/hangup", None).await.0,
        StatusCode::OK
    );
    assert_eq!(
        h.fake.called("ask"),
        before,
        "hanging up needs no confirmation"
    );
}

#[tokio::test]
async fn mobile_data_up_down_status_are_reversible_and_audited() {
    let h = H::new();
    let (s, v) = h.send("GET", "/v1/mobile-data/status", None).await;
    assert_eq!(s, StatusCode::OK, "{v}");
    assert_eq!(h.fake.called("mobile_data_status"), 1);
    let before = h.fake.called("ask");
    let (s, v) = h.send("POST", "/v1/mobile-data/up", None).await;
    assert_eq!(s, StatusCode::OK, "{v}");
    assert_eq!(h.fake.called("mobile_data_up None"), 1);
    let (s, _) = h
        .send(
            "POST",
            "/v1/mobile-data/up",
            Some(json!({"apn": "internet.vodafone.gr"})),
        )
        .await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(
        h.fake
            .called("mobile_data_up Some(\"internet.vodafone.gr\")"),
        1
    );
    assert_eq!(
        h.send("POST", "/v1/mobile-data/down", None).await.0,
        StatusCode::OK
    );
    assert_eq!(h.fake.called("mobile_data_down"), 1);
    assert_eq!(
        h.fake.called("ask"),
        before,
        "mobile data needs no owner confirmation"
    );
    let actions: Vec<Value> = h
        .audit_lines()
        .iter()
        .map(|l| l["action"].clone())
        .collect();
    assert_eq!(
        actions,
        vec![
            json!("mobile_data.up"),
            json!("mobile_data.up"),
            json!("mobile_data.down")
        ]
    );
}

#[tokio::test]
async fn mobile_data_rejects_bad_apn_and_missing_phoned() {
    let h = H::new();
    for bad in [
        json!({"apn": "a b"}),
        json!({"apn": 7}),
        json!({"apn": "x\"; rm"}),
    ] {
        let (s, v) = h.send("POST", "/v1/mobile-data/up", Some(bad)).await;
        assert_eq!((s, code(&v)), (StatusCode::BAD_REQUEST, "bad_request"));
    }
    assert_eq!(h.fake.called("mobile_data_up"), 0);
    h.unavailable("phoned", "s22-phoned socket missing");
    let (s, v) = h.send("POST", "/v1/mobile-data/up", None).await;
    assert_eq!(
        (s, code(&v)),
        (StatusCode::SERVICE_UNAVAILABLE, "unavailable")
    );
    let (s, _) = h.send("POST", "/v1/mobile-data/down", None).await;
    assert_eq!(s, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(h.fake.called("mobile_data_down"), 0);
}

#[tokio::test]
async fn reboot_recovery_confirms_then_schedules() {
    let h = H::new();
    h.answer(Answer::Yes);
    let (s, v) = h.send("POST", "/v1/system/reboot-recovery", None).await;
    assert_eq!(s, StatusCode::OK, "{v}");
    assert_eq!(h.fake.called("reboot"), 1);
    assert_eq!(h.audit_lines()[0]["action"], "system.reboot-recovery");
}

#[tokio::test]
async fn reboot_recovery_is_refused_during_a_capture_or_without_the_tool() {
    let h = H::new();
    h.answer(Answer::Yes);
    let permit = h.app.camera.acquire().unwrap();
    let (s, v) = h.send("POST", "/v1/system/reboot-recovery", None).await;
    assert_eq!((s, code(&v)), (StatusCode::CONFLICT, "camera_busy"));
    permit.finish(true);
    h.unavailable("system", "s22-reboot missing");
    let (s, _) = h.send("POST", "/v1/system/reboot-recovery", None).await;
    assert_eq!(s, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(h.fake.called("ask"), 0);
    assert_eq!(h.fake.called("reboot"), 0);
}

// ------------------------------------------------------------ audio policy

#[tokio::test]
async fn nonzero_volume_is_refused_unless_the_owner_flag_exists() {
    let h = H::new();
    for v in [1, 50, 100] {
        let (s, b) = h
            .send("POST", "/v1/audio/volume", Some(json!({"value": v})))
            .await;
        assert_eq!(
            (s, code(&b)),
            (StatusCode::FORBIDDEN, "policy_denied"),
            "value {v}"
        );
    }
    assert_eq!(h.fake.called("set_level"), 0);
    let (s, b) = h
        .send("POST", "/v1/audio/volume", Some(json!({"value": 0})))
        .await;
    assert_eq!(s, StatusCode::OK, "{b}");
    assert_eq!(h.fake.called("set_level 0"), 1);
    assert!(
        h.audit_lines()
            .iter()
            .filter(|l| l["outcome"] == "refused")
            .count()
            == 3
    );
}

#[tokio::test]
async fn owner_flag_unlocks_volume_but_not_nonsense() {
    let h = H::new();
    h.tmp.write("etc/s22-audio-unmuted", "");
    assert_eq!(
        h.send("POST", "/v1/audio/volume", Some(json!({"value": 30})))
            .await
            .0,
        StatusCode::OK
    );
    assert_eq!(h.fake.called("set_level 30"), 1);
    let (s, _) = h
        .send("POST", "/v1/audio/volume", Some(json!({"value": 101})))
        .await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
    let (s, _) = h
        .send("POST", "/v1/audio/volume", Some(json!({"value": 300})))
        .await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
    let (_, v) = h.send("GET", "/v1/audio/volume", None).await;
    assert_eq!(
        (
            v["level"].clone(),
            v["muted"].clone(),
            v["unmute_allowed"].clone()
        ),
        (json!(30), json!(false), json!(true))
    );
}

#[tokio::test]
async fn audio_get_reports_muted_when_level_is_zero() {
    let h = H::new();
    *h.fake.audio.lock().unwrap() = Some(0);
    let (_, v) = h.send("GET", "/v1/audio/volume", None).await;
    assert_eq!(
        (v["muted"].clone(), v["unmute_allowed"].clone()),
        (json!(true), json!(false))
    );
}

// ------------------------------------------------------------ services

#[tokio::test]
async fn only_allowlisted_services_reach_the_backend() {
    let h = H::new();
    for name in ["s22d", "sshd", "keepalive", "..%2Fetc"] {
        let (s, v) = h
            .send("POST", &format!("/v1/services/{name}/restart"), None)
            .await;
        assert_eq!(
            (s, code(&v)),
            (StatusCode::FORBIDDEN, "not_allowed"),
            "{name}"
        );
    }
    assert_eq!(h.fake.called("restart"), 0);
    for name in ["openunum", "unumsearch", "modem", "phoned", "llama"] {
        let (s, v) = h
            .send("POST", &format!("/v1/services/{name}/restart"), None)
            .await;
        assert_eq!(s, StatusCode::OK, "{name}: {v}");
        assert_eq!(h.fake.called(&format!("restart {name}")), 1);
    }
    let l = h.audit_lines();
    assert_eq!(l.len(), 9);
    assert_eq!(l[0]["outcome"], "refused");
    assert_eq!(l[4]["outcome"], "ok");
}

#[tokio::test]
async fn keepalive_disabled_is_reported() {
    let h = H::new();
    h.fail(
        "restart",
        ApiError::busy("keepalive_disabled", "s22-keepalive is not enabled"),
    );
    let (s, v) = h.send("POST", "/v1/services/llama/restart", None).await;
    assert_eq!((s, code(&v)), (StatusCode::CONFLICT, "keepalive_disabled"));
}

// ------------------------------------------------------------ camera

#[tokio::test]
async fn record_defaults_and_shares_camera_policy() {
    let h = H::new();
    let (status, value) = h
        .send("POST", "/v1/camera/record", Some(json!({"sensor":"rear"})))
        .await;
    assert_eq!(status, StatusCode::OK, "{value}");
    assert_eq!(h.fake.called("record rear 5 None None None"), 1);
    assert_eq!(h.audit_lines()[0]["action"], "camera.record");
}

#[tokio::test]
async fn record_rejects_bad_seconds_before_lock() {
    let h = H::new();
    for seconds in [0, 31] {
        let (status, value) = h
            .send(
                "POST",
                "/v1/camera/record",
                Some(json!({"sensor":"rear", "seconds":seconds})),
            )
            .await;
        assert_eq!(
            (status, code(&value)),
            (StatusCode::BAD_REQUEST, "bad_request")
        );
    }
    assert_eq!(h.fake.called("record"), 0);
}

#[tokio::test]
async fn capture_validates_before_taking_the_lock() {
    let h = H::new();
    for body in [
        json!({"sensor": "top"}),
        json!({"sensor": "rear", "exposure_us": 5}),
        json!({"sensor": "rear", "exposure_us": 99999}),
        json!({"sensor": "front", "gain": 99.0}),
        json!({}),
    ] {
        let (s, v) = h
            .send("POST", "/v1/camera/capture", Some(body.clone()))
            .await;
        assert_eq!(
            (s, code(&v)),
            (StatusCode::BAD_REQUEST, "bad_request"),
            "{body}"
        );
    }
    assert_eq!(h.fake.called("capture"), 0);
    assert_eq!(
        h.app.camera.cooldown_s(),
        0,
        "bad input must not start a cooldown"
    );
}

#[tokio::test]
async fn capture_passes_parameters_and_defaults_develop_on() {
    let h = H::new();
    let (s, v) = h
        .send(
            "POST",
            "/v1/camera/capture",
            Some(json!({"sensor": "front", "exposure_us": 12000, "gain": 2.0})),
        )
        .await;
    assert_eq!(s, StatusCode::OK, "{v}");
    assert_eq!(h.fake.called("capture front Some(12000) Some(2.0) true"), 1);
    let (_, _) = h
        .send(
            "POST",
            "/v1/camera/capture",
            Some(json!({"sensor": "rear", "develop": false})),
        )
        .await;
    assert_eq!(h.fake.called("capture rear None None false"), 1);
    assert_eq!(h.audit_lines()[0]["action"], "camera.capture");
}

#[tokio::test]
async fn a_second_capture_during_the_first_is_refused_not_queued() {
    let h = Arc::new(H::new());
    let (tx, rx) = channel();
    *h.fake.gate.lock().unwrap() = Some(rx);
    let h2 = h.clone();
    let first = tokio::spawn(async move {
        h2.send(
            "POST",
            "/v1/camera/capture",
            Some(json!({"sensor": "rear"})),
        )
        .await
    });
    for _ in 0..200 {
        if h.app.camera.is_busy() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    let (s, v) = h
        .send(
            "POST",
            "/v1/camera/capture",
            Some(json!({"sensor": "front"})),
        )
        .await;
    assert_eq!((s, code(&v)), (StatusCode::CONFLICT, "camera_busy"));
    let (_, st) = h.send("GET", "/v1/camera/status", None).await;
    assert_eq!(st["busy"], true);
    tx.send(()).unwrap();
    assert_eq!(first.await.unwrap().0, StatusCode::OK);
    assert_eq!(h.fake.called("capture"), 1);
    assert!(!h.app.camera.is_busy());
}

#[tokio::test]
async fn a_failed_capture_starts_a_cooldown() {
    let h = H::new();
    h.fail(
        "capture",
        ApiError::new(StatusCode::BAD_GATEWAY, "camera_failed", "S_FMT failed"),
    );
    let (s, v) = h
        .send(
            "POST",
            "/v1/camera/capture",
            Some(json!({"sensor": "rear"})),
        )
        .await;
    assert_eq!((s, code(&v)), (StatusCode::BAD_GATEWAY, "camera_failed"));
    let (s, v) = h
        .send(
            "POST",
            "/v1/camera/capture",
            Some(json!({"sensor": "rear"})),
        )
        .await;
    assert_eq!(
        (s, code(&v)),
        (StatusCode::TOO_MANY_REQUESTS, "camera_cooldown")
    );
    assert_eq!(
        h.fake.called("capture"),
        1,
        "no second attempt while cooling down"
    );
    let (_, st) = h.send("GET", "/v1/camera/status", None).await;
    assert!(st["cooldown_s"].as_u64().unwrap() > 0);
}

#[tokio::test]
async fn missing_camera_client_is_unavailable_without_a_cooldown() {
    let h = H::new();
    h.unavailable("camera", "camera client missing");
    let (s, v) = h
        .send(
            "POST",
            "/v1/camera/capture",
            Some(json!({"sensor": "rear"})),
        )
        .await;
    assert_eq!(
        (s, code(&v)),
        (StatusCode::SERVICE_UNAVAILABLE, "unavailable")
    );
    assert_eq!(h.app.camera.cooldown_s(), 0);
}

// ------------------------------------------------------------ display

#[tokio::test]
async fn display_power_and_brightness() {
    let h = H::new();
    assert_eq!(
        h.send("POST", "/v1/display/on", None).await.0,
        StatusCode::OK
    );
    assert_eq!(
        h.send("POST", "/v1/display/off", None).await.0,
        StatusCode::OK
    );
    assert_eq!(
        (
            h.fake.called("display_power true"),
            h.fake.called("display_power false")
        ),
        (1, 1)
    );
    let (s, _) = h
        .send(
            "POST",
            "/v1/display/brightness",
            Some(json!({"percent": 40})),
        )
        .await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(h.fake.called("display_brightness 40"), 1);
    for p in [0, 101, 255] {
        let (s, _) = h
            .send(
                "POST",
                "/v1/display/brightness",
                Some(json!({"percent": p})),
            )
            .await;
        assert_eq!(s, StatusCode::BAD_REQUEST, "{p}");
    }
    let (s, _) = h
        .send(
            "POST",
            "/v1/display/brightness",
            Some(json!({"percent": 300})),
        )
        .await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
    assert_eq!(h.fake.called("display_brightness"), 1);
}

#[tokio::test]
async fn display_backend_failure_is_reported_and_audited() {
    let h = H::new();
    h.fail(
        "display_power",
        ApiError::upstream("s22-display off failed"),
    );
    let (s, v) = h.send("POST", "/v1/display/off", None).await;
    assert_eq!((s, code(&v)), (StatusCode::BAD_GATEWAY, "upstream_error"));
    assert_eq!(h.audit_lines()[0]["outcome"], "failed");
}

#[tokio::test]
async fn startup_audit_file_is_append_only_json_lines() {
    let h = H::new();
    h.send("POST", "/v1/wifi/scan", None).await;
    h.send("POST", "/v1/wifi/scan", None).await;
    let before = h.audit_lines().len();
    let again = Audit::open(&h.app.cfg.audit_path).unwrap();
    again.record("x", "read", "ok", json!({}));
    assert_eq!(
        h.audit_lines().len(),
        before + 1,
        "reopening must append, not truncate"
    );
}

// ------------------------------------------------------------ device API (/v1/device)

impl H {
    async fn dev(&self, id: &str, body: Value) -> (StatusCode, Value) {
        let req = Request::builder()
            .method("POST")
            .uri(format!("/v1/device/{id}"))
            .header("content-type", "application/json")
            .header("x-s22-caller", "agent")
            .body(Body::from(body.to_string()))
            .unwrap();
        self.run(req, Transport::Unix).await
    }
}

#[tokio::test]
async fn device_catalog_lists_every_capability_with_effect_and_schema() {
    let h = H::new();
    let (s, v) = h.send("GET", "/v1/device", None).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["api"], "device/1");
    let caps = v["capabilities"].as_array().unwrap();
    assert_eq!(caps.len(), crate::device::defs().len());
    for c in caps {
        assert!(
            ["read", "set", "action"].contains(&c["effect"].as_str().unwrap()),
            "{c}"
        );
        assert_eq!(c["args"]["type"], "object");
        let confirm = if c["id"] == "sms.send" || c["id"] == "call.dial" {
            "unlisted_number"
        } else {
            "none"
        };
        assert_eq!(c["confirm"], confirm, "{c}");
    }
    let md =
        std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/DEVICE_API.md")).unwrap();
    for d in crate::device::defs() {
        assert!(
            md.contains(&format!("`{}`", d.id)),
            "DEVICE_API.md misses {}",
            d.id
        );
    }
}

#[tokio::test]
async fn device_set_returns_state_read_back_and_audits_the_caller() {
    let h = H::new();
    let (s, v) = h.dev("volume.set", json!({"value": 7})).await;
    assert_eq!(s, StatusCode::OK, "{v}");
    assert_eq!(
        (v["cap"].clone(), v["effect"].clone()),
        (json!("volume.set"), json!("set"))
    );
    assert_eq!(v["state"]["level"], 7);
    assert_eq!(
        std::fs::read_to_string(&h.app.cfg.say_level).unwrap(),
        "7\n"
    );
    let (_, v) = h.dev("volume.set", json!({"muted": "true"})).await;
    assert_eq!(
        v["state"]["muted"], true,
        "string booleans from small models are accepted"
    );
    let (s, v) = h.dev("brightness.set", json!({"value": 40})).await;
    assert_eq!(s, StatusCode::OK, "{v}");
    assert_eq!(h.fake.called("display_brightness 40"), 1);
    let l = h.audit_lines();
    assert_eq!(l[0]["caller"], "agent");
    assert_eq!(l[0]["action"], "volume.set");
    assert_eq!(l[0]["detail"]["args"]["value"], 7);
    assert_eq!(l[2]["outcome"], "ok");
}

#[tokio::test]
async fn device_rejects_unknown_args_and_bad_values_before_acting() {
    let h = H::new();
    for (id, body) in [
        ("brightness.set", json!({"value": 0})),
        ("brightness.set", json!({"percent": 40})),
        ("wifi.set", json!({})),
        ("wifi.set", json!({"on": "maybe"})),
        ("screen_timeout.set", json!({"value": 99999})),
    ] {
        let (s, v) = h.dev(id, body.clone()).await;
        assert_eq!(
            (s, code(&v)),
            (StatusCode::BAD_REQUEST, "bad_request"),
            "{id} {body}"
        );
    }
    assert_eq!(h.fake.called("display_brightness"), 0);
    assert_eq!(h.fake.called("wifi_"), 0);
    let (s, v) = h.dev("nope.set", json!({})).await;
    assert_eq!((s, code(&v)), (StatusCode::NOT_FOUND, "not_found"));
    let (s, _) = h.send("GET", "/v1/device/wifi.set", None).await;
    assert_eq!(s, StatusCode::METHOD_NOT_ALLOWED);
    let (s, v) = h.send("GET", "/v1/device/volume.get", None).await;
    assert_eq!(s, StatusCode::OK, "{v}");
}

#[tokio::test]
async fn sms_to_an_allowlisted_number_needs_no_question_and_leaves_a_grant() {
    let h = H::new();
    h.tmp.write(
        "srv/s22/state/phone/allowlist.json",
        r#"{"numbers": ["+306900000000"]}"#,
    );
    let (s, v) = h
        .dev(
            "sms.send",
            json!({"number": "+30 690 000 0000", "text": "hello there"}),
        )
        .await;
    assert_eq!(s, StatusCode::OK, "{v}");
    assert_eq!(h.fake.called("ask"), 0);
    assert_eq!(h.fake.called("sms_send"), 1);
    assert_eq!(v["result"]["owner_confirmed"], false);
    assert!(h.app.cfg.phoned_grants.join("+306900000000.sms").exists());
    let log = std::fs::read_to_string(&h.app.cfg.audit_path).unwrap();
    assert!(!log.contains("hello there"), "text is not audited");
    assert!(!log.contains("6900000000"), "numbers are masked: {log}");
    // The legacy route follows the same policy.
    let (s, _) = h
        .send(
            "POST",
            "/v1/sms/send",
            Some(json!({"number": "+306900000000", "text": "x"})),
        )
        .await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(h.fake.called("ask"), 0);
}

#[tokio::test]
async fn unlisted_numbers_need_the_owner_and_silence_is_needs_confirmation() {
    for (answer, status, want) in [
        (Answer::No, StatusCode::FORBIDDEN, "owner_denied"),
        (Answer::Timeout, StatusCode::CONFLICT, "needs_confirmation"),
        (Answer::Busy, StatusCode::CONFLICT, "needs_confirmation"),
        (
            Answer::Unavailable("x".into()),
            StatusCode::CONFLICT,
            "needs_confirmation",
        ),
    ] {
        let h = H::new();
        h.answer(answer);
        let (s, v) = h
            .dev("sms.send", json!({"number": "+306911111111", "text": "x"}))
            .await;
        assert_eq!((s, code(&v)), (status, want));
        let (s, v) = h.dev("call.dial", json!({"number": "+306911111111"})).await;
        assert_eq!((s, code(&v)), (status, want));
        assert_eq!(h.fake.called("sms_send") + h.fake.called("dial"), 0);
        assert!(!h.app.cfg.phoned_grants.exists(), "no grant without a yes");
    }
    let h = H::new();
    h.answer(Answer::Yes);
    let (s, v) = h.dev("call.dial", json!({"number": "+306911111111"})).await;
    assert_eq!(s, StatusCode::OK, "{v}");
    assert_eq!(v["result"]["owner_confirmed"], true);
    assert!(v["note"].as_str().unwrap().contains("audio"));
    assert!(h.app.cfg.phoned_grants.join("+306911111111.dial").exists());
}

#[tokio::test]
async fn calls_gate_blocks_dial_on_both_apis() {
    let h = H::new();
    h.answer(Answer::Yes);
    let (s, v) = h.dev("calls.set", json!({"on": false})).await;
    assert_eq!(
        (s, v["state"]["enabled"].clone()),
        (StatusCode::OK, json!(false))
    );
    let (s, v) = h.dev("call.dial", json!({"number": "+306900000000"})).await;
    assert_eq!((s, code(&v)), (StatusCode::FORBIDDEN, "calls_disabled"));
    let (s, v) = h
        .send(
            "POST",
            "/v1/call/dial",
            Some(json!({"number": "+306900000000"})),
        )
        .await;
    assert_eq!((s, code(&v)), (StatusCode::FORBIDDEN, "calls_disabled"));
    assert_eq!(h.fake.called("ask") + h.fake.called("dial"), 0);
}

#[tokio::test]
async fn wifi_and_mobile_off_refuse_to_strand_the_phone() {
    let h = H::new();
    // Wi-Fi not connected (fake status), no USB: mobile data off would strand.
    let (s, v) = h.dev("mobile_data.set", json!({"on": false})).await;
    assert_eq!((s, code(&v)), (StatusCode::CONFLICT, "would_strand"));
    assert_eq!(h.fake.called("mobile_data_down"), 0);
    // With USB carrier it goes through and pins netpolicy's mode.
    h.tmp.write("sys/class/net/ecm0/carrier", "1\n");
    let (s, v) = h.dev("mobile_data.set", json!({"on": false})).await;
    assert_eq!(s, StatusCode::OK, "{v}");
    assert_eq!(v["state"]["mode"], "off");
    assert_eq!(h.fake.called("mobile_data_down"), 1);
    // Mobile off and no USB: Wi-Fi off would strand.
    std::fs::remove_file(h.tmp.path().join("sys/class/net/ecm0/carrier")).unwrap();
    let (s, v) = h.dev("wifi.set", json!({"on": false})).await;
    assert_eq!((s, code(&v)), (StatusCode::CONFLICT, "would_strand"));
    let (s, v) = h.dev("wifi.set", json!({"on": false, "force": true})).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(h.fake.called("wifi_disconnect"), 1);
    assert_eq!(v["state"]["enabled"], false);
    assert!(
        h.app.cfg.saver_off.exists(),
        "netpolicy's saver must not reconnect it"
    );
    let (_, v) = h.dev("wifi.set", json!({"on": true})).await;
    assert_eq!(v["state"]["enabled"], true);
    assert!(!h.app.cfg.saver_off.exists());
    assert_eq!(h.fake.called("wifi_reconnect"), 1);
    // Mobile on again: back to failover, and brought up now because Wi-Fi is down.
    let (s, v) = h.dev("mobile_data.set", json!({"on": true})).await;
    assert_eq!(s, StatusCode::OK, "{v}");
    assert_eq!(v["state"]["mode"], "failover");
    assert_eq!(h.fake.called("mobile_data_up None"), 1);
}

#[tokio::test]
async fn device_status_survives_failing_sources() {
    let h = H::new();
    h.unavailable("phoned", "s22-phoned is not running");
    h.fail("battery", ApiError::unavailable("no battery"));
    let (s, v) = h.dev("device.status", json!({})).await;
    assert_eq!(s, StatusCode::OK, "{v}");
    assert!(v["state"]["battery"]["percent"].is_null());
    assert!(v["state"]["network"]["radio"].is_null());
    assert_eq!(v["state"]["calls_enabled"], true);
    // A capability whose backend is missing says why instead of failing inside.
    let (s, v) = h.dev("mobile_data.get", json!({})).await;
    assert_eq!(
        (s, code(&v)),
        (StatusCode::SERVICE_UNAVAILABLE, "unavailable")
    );
}

#[tokio::test]
async fn vibration_off_turns_haptic_play_into_a_reported_no_op() {
    let h = H::new();
    std::fs::create_dir_all(h.app.cfg.haptic_sock.parent().unwrap()).unwrap();
    let _d = std::os::unix::net::UnixDatagram::bind(&h.app.cfg.haptic_sock).unwrap();
    let (_, v) = h.dev("vibration.set", json!({"on": false})).await;
    assert_eq!(v["state"]["enabled"], false);
    let (s, v) = h.dev("haptic.play", json!({"pattern": "tick"})).await;
    assert_eq!(s, StatusCode::OK, "{v}");
    assert_eq!(v["state"]["played"], false);
}

#[tokio::test]
async fn torch_set_writes_the_flash_led_and_get_reports_it() {
    let h = H::new();
    let dir = h.app.cfg.torch_dir.clone();
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("max_brightness"), "15\n").unwrap();
    std::fs::write(dir.join("brightness"), "0\n").unwrap();
    let read = || std::fs::read_to_string(dir.join("brightness")).unwrap();
    let (s, v) = h.dev("torch.get", json!({})).await;
    assert_eq!(s, StatusCode::OK, "{v}");
    assert_eq!(v["state"]["on"], false);
    let (s, v) = h.dev("torch.set", json!({"on": true})).await;
    assert_eq!(s, StatusCode::OK, "{v}");
    assert_eq!(
        (read(), v["state"]["on"].clone()),
        ("15\n".to_string(), json!(true))
    );
    let (_, v) = h.dev("torch.set", json!({"on": "true", "value": 4})).await;
    assert_eq!(
        (read(), v["state"]["brightness"].clone()),
        ("4\n".to_string(), json!(4))
    );
    let (s, _) = h.dev("torch.set", json!({"on": true, "value": 16})).await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
    assert_eq!(read(), "4\n", "a refused level changes nothing");
    let (_, v) = h.dev("torch.set", json!({"on": false, "value": 9})).await;
    assert_eq!(
        (read(), v["state"]["on"].clone()),
        ("0\n".to_string(), json!(false))
    );
    let (s, _) = h.dev("torch.set", json!({})).await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn torch_is_unavailable_without_the_led() {
    let h = H::new();
    let (s, v) = h.dev("torch.set", json!({"on": true})).await;
    assert_eq!(s, StatusCode::SERVICE_UNAVAILABLE, "{v}");
}

// ------------------------------------------------------------ remote listener (remote.rs)

mod remote_tests {
    use super::*;
    use crate::remote::{self, RemoteConfig, RemoteState};

    const RIG: &str = "nRIGSTABLE0001";
    const TOKEN: &str = "s3cr3t-token-for-tests";

    fn fake_whois(_: &std::path::Path, peer: &SocketAddr) -> Result<String, String> {
        match peer.ip().to_string().as_str() {
            "100.64.0.1" => Ok(RIG.into()),
            "100.64.0.2" => Ok("nSOMEOTHERNODE".into()),
            _ => Err("no such peer".into()),
        }
    }

    fn state(h: &H) -> RemoteState {
        let cfg = RemoteConfig::from_json(&json!({
            "bind": "100.64.0.99:8767",
            "peers": [{"name": "rig", "node_id": RIG, "token_sha256": remote::sha256_hex(TOKEN.as_bytes())}],
            "switches": h.tmp.path().join("caps.json").to_str().unwrap(),
            "capture_audit": h.tmp.path().join("captures.jsonl").to_str().unwrap(),
        }))
        .unwrap();
        RemoteState {
            app: h.app.clone(),
            cfg: Arc::new(cfg),
            whois: fake_whois,
        }
    }

    async fn call(
        h: &H,
        peer: &str,
        token: Option<&str>,
        method: &str,
        uri: &str,
        body: Option<Value>,
    ) -> (StatusCode, Value) {
        let mut b = Request::builder().method(method).uri(uri);
        if let Some(t) = token {
            b = b.header("authorization", format!("Bearer {t}"));
        }
        let mut req = b
            .body(
                body.map(|v| Body::from(v.to_string()))
                    .unwrap_or_else(Body::empty),
            )
            .unwrap();
        req.extensions_mut()
            .insert(ConnectInfo(peer.parse::<SocketAddr>().unwrap()));
        let r = remote::router(state(h)).oneshot(req).await.unwrap();
        let s = r.status();
        let bytes = axum::body::to_bytes(r.into_body(), 4 << 20).await.unwrap();
        (s, serde_json::from_slice(&bytes).unwrap_or(Value::Null))
    }

    #[tokio::test]
    async fn both_factors_are_required() {
        let h = H::new();
        let cases = [
            ("192.168.1.5:4000", Some(TOKEN), "not_tailnet"),
            ("100.64.0.9:4000", Some(TOKEN), "whois_failed"),
            ("100.64.0.2:4000", Some(TOKEN), "unknown_node"), // right token, wrong node
            ("100.64.0.1:4000", Some("wrong"), "bad_token"),  // right node, wrong token
            ("100.64.0.1:4000", None, "bad_token"),
        ];
        for (peer, tok, want) in cases {
            let (s, v) = call(&h, peer, tok, "GET", "/v1/device/volume.get", None).await;
            assert_eq!(
                (s, code(&v)),
                (StatusCode::FORBIDDEN, want),
                "{peer} {tok:?}"
            );
        }
        let (s, v) = call(
            &h,
            "100.64.0.1:4000",
            Some(TOKEN),
            "GET",
            "/v1/device/volume.get",
            None,
        )
        .await;
        assert_eq!(s, StatusCode::OK, "{v}");
        let denied = h
            .audit_lines()
            .iter()
            .filter(|l| l["action"] == "remote.auth")
            .count();
        assert_eq!(denied, 5);
        assert_eq!(h.audit_lines().last().unwrap()["caller"], "remote");
    }

    #[tokio::test]
    async fn an_origin_header_is_refused_even_when_authenticated() {
        let h = H::new();
        let mut req = Request::builder()
            .uri("/v1/device/volume.get")
            .header("authorization", format!("Bearer {TOKEN}"))
            .header("origin", "http://evil.example")
            .body(Body::empty())
            .unwrap();
        req.extensions_mut().insert(ConnectInfo(
            "100.64.0.1:4000".parse::<SocketAddr>().unwrap(),
        ));
        let r = remote::router(state(&h)).oneshot(req).await.unwrap();
        assert_eq!(r.status(), StatusCode::FORBIDDEN);
    }

    #[tokio::test]
    async fn only_remote_caps_and_only_when_switched_on() {
        let h = H::new();
        let ok = |s: StatusCode| s == StatusCode::OK;
        for id in ["wifi.set", "mobile_data.set", "autosleep.set", "calls.set"] {
            let (s, v) = call(
                &h,
                "100.64.0.1:1",
                Some(TOKEN),
                "POST",
                &format!("/v1/device/{id}"),
                Some(json!({"on": false})),
            )
            .await;
            assert_eq!((s, code(&v)), (StatusCode::FORBIDDEN, "not_remote"), "{id}");
        }
        let (s, _) = call(&h, "100.64.0.1:1", Some(TOKEN), "GET", "/v1/status", None).await;
        assert_eq!(
            s,
            StatusCode::NOT_FOUND,
            "only /v1/device is served remotely"
        );
        h.tmp.write("caps.json", r#"{"volume.get": false}"#);
        let (s, v) = call(
            &h,
            "100.64.0.1:1",
            Some(TOKEN),
            "GET",
            "/v1/device/volume.get",
            None,
        )
        .await;
        assert_eq!((s, code(&v)), (StatusCode::FORBIDDEN, "capability_off"));
        h.tmp.write("caps.json", r#"{"volume.get": true}"#);
        assert!(ok(call(
            &h,
            "100.64.0.1:1",
            Some(TOKEN),
            "GET",
            "/v1/device/volume.get",
            None
        )
        .await
        .0));
        assert!(!remote::default_on("audio.listen"), "listening ships off");
        assert!(remote::default_on("camera.capture"));
        let (_, v) = call(&h, "100.64.0.1:1", Some(TOKEN), "GET", "/v1/device", None).await;
        let ids: Vec<&str> = v["capabilities"]
            .as_array()
            .unwrap()
            .iter()
            .map(|c| c["id"].as_str().unwrap())
            .collect();
        assert!(
            ids.contains(&"camera.capture") && !ids.contains(&"wifi.set"),
            "{ids:?}"
        );
    }

    #[tokio::test]
    async fn a_capture_comes_back_in_the_response_and_is_not_kept() {
        let h = H::new();
        let img = h.tmp.write("gallery/p1.jpg", "JPEGDATA");
        *h.fake.capture_file.lock().unwrap() = Some(img.to_str().unwrap().into());
        let (s, v) = call(
            &h,
            "100.64.0.1:1",
            Some(TOKEN),
            "POST",
            "/v1/device/camera.capture",
            Some(json!({})),
        )
        .await;
        assert_eq!(s, StatusCode::OK, "{v}");
        assert_eq!(v["artifact"]["base64"], remote::base64(b"JPEGDATA"));
        assert_eq!(v["artifact"]["mime"], "image/jpeg");
        assert_eq!(v["kept"], false);
        assert!(v["state"].get("path").is_none());
        assert!(!img.exists(), "deleted after the task");
        assert!(
            !std::path::Path::new(&format!("{}.gallery.jpg", img.display())).exists(),
            "the gallery copy too"
        );
        assert!(v["state"].get("gallery_path").is_none());
        let line: Value = serde_json::from_str(
            std::fs::read_to_string(h.tmp.path().join("captures.jsonl"))
                .unwrap()
                .lines()
                .next()
                .unwrap(),
        )
        .unwrap();
        assert_eq!(
            (
                line["peer"].as_str(),
                line["cap"].as_str(),
                line["kept"].as_bool()
            ),
            (Some("rig"), Some("camera.capture"), Some(false))
        );
        assert_eq!(line["sha256"], remote::sha256_hex(b"JPEGDATA"));
        assert!(
            line.get("base64").is_none() && !line.to_string().contains("JPEGDATA"),
            "no content in the audit"
        );
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            std::fs::metadata(h.tmp.path().join("captures.jsonl"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
    }

    #[tokio::test]
    async fn save_true_keeps_the_photo() {
        let h = H::new();
        let img = h.tmp.write("gallery/p2.jpg", "X");
        *h.fake.capture_file.lock().unwrap() = Some(img.to_str().unwrap().into());
        let (s, v) = call(
            &h,
            "100.64.0.1:1",
            Some(TOKEN),
            "POST",
            "/v1/device/camera.capture",
            Some(json!({"save": true, "sensor": "front"})),
        )
        .await;
        assert_eq!(s, StatusCode::OK, "{v}");
        assert_eq!(v["kept"], true);
        assert!(img.exists());
        assert!(std::path::Path::new(&format!("{}.gallery.jpg", img.display())).exists());
        assert!(
            h.fake
                .calls
                .lock()
                .unwrap()
                .iter()
                .any(|c| c.starts_with("capture front")),
            "save is stripped, sensor passes"
        );
    }

    #[tokio::test]
    async fn get_query_arguments_reach_the_device_api() {
        let h = H::new();
        let (s, v) = call(
            &h,
            "100.64.0.1:1",
            Some(TOKEN),
            "GET",
            "/v1/device/task.get?session=owner-chat",
            None,
        )
        .await;
        assert_eq!((s, code(&v)), (StatusCode::BAD_REQUEST, "bad_request"));
        assert!(
            v["error"].as_str().unwrap().contains("device:task:"),
            "the session argument arrived: {v}"
        );
    }

    #[test]
    fn config_rejects_non_tailnet_binds_and_bad_hashes() {
        let good = remote::sha256_hex(b"x");
        assert!(RemoteConfig::from_json(&json!({"bind": "0.0.0.0:8767", "peers": [{"name": "r", "node_id": "n", "token_sha256": good}]})).is_err());
        assert!(RemoteConfig::from_json(&json!({"bind": "100.64.0.99:8767", "peers": [{"name": "r", "node_id": "n", "token_sha256": "abc"}]})).is_err());
        assert!(
            RemoteConfig::from_json(&json!({"bind": "100.64.0.99:8767", "peers": []})).is_err()
        );
        assert!(RemoteConfig::from_json(&json!({"bind": "100.64.0.99:8767", "peers": [{"name": "r", "node_id": "n", "token_sha256": good}]})).is_ok());
    }

    #[test]
    fn base64_matches_rfc4648_vectors() {
        for (i, o) in [
            ("", ""),
            ("f", "Zg=="),
            ("fo", "Zm8="),
            ("foo", "Zm9v"),
            ("foobar", "Zm9vYmFy"),
        ] {
            assert_eq!(remote::base64(i.as_bytes()), o);
        }
    }
}

// ------------------------------------------------------------ speak / task.submit / task.get

mod p2_tests {
    use super::*;
    use std::io::{Read, Write};

    /// One-shot HTTP server: records the request head+body, answers `status` with `body`.
    fn http_once(status: u16, body: Value) -> (String, std::sync::mpsc::Receiver<String>) {
        let l = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = l.local_addr().unwrap().to_string();
        let (tx, rx) = channel();
        std::thread::spawn(move || {
            if let Ok((mut c, _)) = l.accept() {
                let _ = c.set_read_timeout(Some(Duration::from_millis(300)));
                let mut data = Vec::new();
                let mut b = [0u8; 4096];
                while let Ok(n) = c.read(&mut b) {
                    if n == 0 {
                        break;
                    }
                    data.extend_from_slice(&b[..n]);
                }
                let payload = body.to_string();
                let _ = write!(c, "HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{payload}", payload.len());
                let _ = tx.send(String::from_utf8_lossy(&data).to_string());
            }
        });
        (addr, rx)
    }

    #[tokio::test]
    async fn speak_goes_through_touchd_tts_with_play() {
        let h = H::new();
        let seen = crate::real::touch::fake::serve(
            &h.app.cfg.touch_sock,
            |_| json!({"ok": true, "played": true, "engine": "antonis", "ms": 900}),
        );
        let (s, v) = h
            .send(
                "POST",
                "/v1/device/speak",
                Some(json!({"text": "Dinner is ready"})),
            )
            .await;
        assert_eq!(s, StatusCode::OK, "{v}");
        assert_eq!(v["state"]["spoken"], true);
        assert_eq!(v["state"]["level"], 4, "a sensible default level");
        let req = seen.recv_timeout(Duration::from_secs(2)).unwrap();
        assert_eq!(
            (
                req["cmd"].as_str(),
                req["text"].as_str(),
                req["play"].as_bool(),
                req["level"].as_u64()
            ),
            (Some("tts"), Some("Dinner is ready"), Some(true), Some(4))
        );
        let (_, v6) = h
            .send(
                "POST",
                "/v1/device/speak",
                Some(json!({"text": "x", "level": 6})),
            )
            .await;
        assert_eq!(v6["state"]["level"], 6);
        assert_eq!(
            seen.recv_timeout(Duration::from_secs(2)).unwrap()["level"],
            6
        );
        let (s, v) = h
            .send(
                "POST",
                "/v1/device/speak",
                Some(json!({"text": "x", "level": 10})),
            )
            .await;
        assert_eq!(
            (s, code(&v)),
            (StatusCode::BAD_REQUEST, "bad_request"),
            "capped at 8"
        );
        let (s, v) = h
            .send("POST", "/v1/device/speak", Some(json!({"text": ""})))
            .await;
        assert_eq!((s, code(&v)), (StatusCode::BAD_REQUEST, "bad_request"));
    }

    #[tokio::test]
    async fn task_submit_posts_an_agent_marked_framed_turn() {
        let (addr, rx) = http_once(200, json!({"ok": true, "reply": "done: 3 files"}));
        let h = H::with(|c| c.openunum_addr = addr);
        let (s, v) = h
            .send(
                "POST",
                "/v1/device/task.submit",
                Some(json!({"text": "count the files in /tmp"})),
            )
            .await;
        assert_eq!(s, StatusCode::OK, "{v}");
        assert!(v["state"]["session"]
            .as_str()
            .unwrap()
            .starts_with("device:task:"));
        assert_eq!(v["state"]["done"], true);
        assert_eq!(v["state"]["reply"], "done: 3 files");
        let raw = rx.recv_timeout(Duration::from_secs(2)).unwrap();
        assert!(raw.starts_with("POST /api/chat "), "{raw}");
        assert!(
            raw.contains("x-openunum-agent-session: s22d-device-task"),
            "agent-marked: {raw}"
        );
        assert!(raw.contains("not from the owner"), "framed: {raw}");
        for owner_claim in [
            "X-OpenUnum-Request",
            "x-unum-owner-token",
            "Origin:",
            "x-openunum-owner-session",
        ] {
            assert!(
                !raw.to_ascii_lowercase()
                    .contains(&owner_claim.to_ascii_lowercase()),
                "never owner-claiming: {owner_claim}"
            );
        }
    }

    #[tokio::test]
    async fn task_get_reads_only_device_task_sessions() {
        let (addr, rx) = http_once(
            200,
            json!({"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "the answer"}]}),
        );
        let h = H::with(|c| c.openunum_addr = addr);
        for bad in [
            "owner-chat",
            "device:task:..%2Fx",
            "device:task:12a",
            "device:task:",
        ] {
            let (s, v) = h
                .send("GET", &format!("/v1/device/task.get?session={bad}"), None)
                .await;
            assert_eq!(
                (s, code(&v)),
                (StatusCode::BAD_REQUEST, "bad_request"),
                "{bad}: {v}"
            );
        }
        let (s, v) = h
            .send("GET", "/v1/device/task.get?session=device:task:123", None)
            .await;
        assert_eq!(s, StatusCode::OK, "{v}");
        assert_eq!(v["state"]["reply"], "the answer");
        assert!(rx
            .recv_timeout(Duration::from_secs(2))
            .unwrap()
            .starts_with("GET /api/sessions/device:task:123 "));
    }
}

// ------------------------------------------------------------ audio.listen (P3)

mod listen_tests {
    use super::*;

    fn h_with_fakes() -> H {
        H::new()
    }

    fn arm(h: &H) -> (std::path::PathBuf, std::path::PathBuf) {
        let marker = h.tmp.path().join("recorded");
        let rec = h.tmp.script(
            "bin/s22-rec",
            &format!("echo $1 > {m}; printf RIFF > \"$2\"", m = marker.display()),
        );
        let asr = h.tmp.script(
            "bin/asr",
            r#"echo 'log line'; echo '{"text": " hello room ", "tokens": []}'"#,
        );
        let _ = (rec, asr);
        (marker, h.tmp.path().join("bin/asr"))
    }

    fn cfg_listen(h: &H) -> H {
        let root = h.tmp.path().to_path_buf();
        H::with(move |c| {
            c.rec_tool = root.join("bin/s22-rec");
            c.asr_cmd = vec![root.join("bin/asr").display().to_string(), "{wav}".into()];
            c.listen_dir = root.join("listen");
            c.listen_dir_in_chroot = root.join("listen").display().to_string();
            c.switches_path = root.join("switches.json");
            c.capture_audit = root.join("captures.jsonl");
        })
    }

    #[tokio::test]
    async fn off_by_default_for_every_caller() {
        let base = h_with_fakes();
        let (marker, _) = arm(&base);
        let h = cfg_listen(&base);
        let (s, v) = h
            .send(
                "POST",
                "/v1/device/audio.listen",
                Some(json!({"seconds": 5})),
            )
            .await;
        assert_eq!(
            (s, code(&v)),
            (StatusCode::FORBIDDEN, "capability_off"),
            "{v}"
        );
        assert!(!marker.exists(), "nothing recorded");
    }

    #[tokio::test]
    async fn no_banner_no_recording() {
        let base = h_with_fakes();
        let (marker, _) = arm(&base);
        base.tmp.write("switches.json", r#"{"audio.listen": true}"#);
        let h = cfg_listen(&base);
        let (s, v) = h
            .send(
                "POST",
                "/v1/device/audio.listen",
                Some(json!({"seconds": 5})),
            )
            .await;
        assert_eq!(s, StatusCode::SERVICE_UNAVAILABLE, "{v}");
        assert!(!marker.exists(), "nothing recorded without the banner");
    }

    #[tokio::test]
    async fn listens_transcribes_deletes_and_audits() {
        let base = h_with_fakes();
        let (marker, _) = arm(&base);
        base.tmp.write("switches.json", r#"{"audio.listen": true}"#);
        let h = cfg_listen(&base);
        let banners = crate::real::touch::fake::serve(
            &h.app.cfg.touch_sock,
            |_| json!({"ok": true, "result": "ok"}),
        );
        let (s, v) = h
            .send(
                "POST",
                "/v1/device/audio.listen",
                Some(json!({"seconds": 7})),
            )
            .await;
        assert_eq!(s, StatusCode::OK, "{v}");
        assert_eq!(v["state"]["transcript"], "hello room");
        assert_eq!(v["state"]["kept"], false);
        assert_eq!(std::fs::read_to_string(&marker).unwrap().trim(), "7");
        let b = banners.recv_timeout(Duration::from_secs(2)).unwrap();
        assert_eq!(
            (b["cmd"].as_str(), b["args"][0].as_str()),
            (Some("ui"), Some("card"))
        );
        let left: Vec<_> = std::fs::read_dir(&h.app.cfg.listen_dir).unwrap().collect();
        assert!(left.is_empty(), "the audio is deleted");
        let audit = std::fs::read_to_string(&h.app.cfg.capture_audit).unwrap();
        let line: Value = serde_json::from_str(audit.lines().last().unwrap()).unwrap();
        assert_eq!(
            (
                line["cap"].as_str(),
                line["seconds"].as_u64(),
                line["chars"].as_u64()
            ),
            (Some("audio.listen"), Some(7), Some(10))
        );
        assert!(!audit.contains("hello room"), "no content in the audit");
        let (s, v) = h
            .send(
                "POST",
                "/v1/device/audio.listen",
                Some(json!({"seconds": 500})),
            )
            .await;
        assert_eq!((s, code(&v)), (StatusCode::BAD_REQUEST, "bad_request"));
    }
}

// ------------------------------------------------------------ uplink.share (P4)

mod uplink_tests {
    use super::*;

    fn with_tool(body: &str) -> H {
        let tmp = Tmp::new();
        let tool = tmp.write("bin/share.py", body);
        let p = tool.clone();
        let h = H::with(move |c| c.uplink_tool = p);
        std::mem::forget(tmp);
        h
    }

    #[tokio::test]
    async fn share_on_off_and_status_go_through_the_tool() {
        let h = with_tool("import json,sys\nprint('noise')\nprint(json.dumps({'ok': True, 'on': sys.argv[1] == 'on', 'cmd': sys.argv[1]}))\n");
        let (s, v) = h
            .send("POST", "/v1/device/uplink.share", Some(json!({"on": true})))
            .await;
        assert_eq!(s, StatusCode::OK, "{v}");
        assert_eq!(
            (v["state"]["cmd"].as_str(), v["state"]["on"].as_bool()),
            (Some("on"), Some(true))
        );
        assert!(v["state"].get("ok").is_none());
        let (_, v) = h
            .send(
                "POST",
                "/v1/device/uplink.share",
                Some(json!({"on": false})),
            )
            .await;
        assert_eq!(v["state"]["cmd"], "off");
        let (_, v) = h.send("GET", "/v1/device/uplink.status", None).await;
        assert_eq!(v["state"]["cmd"], "status");
        let (s, v) = h
            .send("POST", "/v1/device/uplink.share", Some(json!({})))
            .await;
        assert_eq!((s, code(&v)), (StatusCode::BAD_REQUEST, "bad_request"));
    }

    #[tokio::test]
    async fn a_guard_refusal_is_final() {
        let h = with_tool("import json\nprint(json.dumps({'ok': False, 'error': 'battery 38 % (sharing needs more than 40 %)'}))\n");
        let (s, v) = h
            .send("POST", "/v1/device/uplink.share", Some(json!({"on": true})))
            .await;
        assert_eq!(
            (s, code(&v)),
            (StatusCode::CONFLICT, "share_refused"),
            "{v}"
        );
        assert!(v["error"].as_str().unwrap().contains("battery"));
    }
}
