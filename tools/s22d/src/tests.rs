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
        self.rec(&format!(
            "capture {} {:?} {:?} {}",
            r.sensor, r.exposure_us, r.gain, r.develop
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
        let tmp = Tmp::new();
        let mut cfg = Config::rooted(tmp.path());
        cfg.camera_min_interval = Duration::ZERO;
        cfg.camera_failure_cooldown = Duration::from_secs(60);
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
    assert_eq!(get("sms.send")["confirm"], "owner");
    assert_eq!(get("bt.scan")["available"], false);
    assert_eq!(
        get("bt.scan")["reason"],
        "HCI raw-socket kernel panic: disabled"
    );
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
    for peer in ["100.94.169.3:5000", "192.168.1.9:5000", "10.0.0.2:1"] {
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
async fn bluetooth_stays_disabled_with_the_exact_reason() {
    let h = H::new();
    let (s, v) = h.send("GET", "/v1/bt/status", None).await;
    assert_eq!(
        (s, v["available"].clone(), v["reason"].clone()),
        (
            StatusCode::OK,
            json!(false),
            json!("HCI raw-socket kernel panic: disabled")
        )
    );
    for p in ["/v1/bt/power", "/v1/bt/scan"] {
        let (s, v) = h.send("POST", p, Some(json!({"on": true}))).await;
        assert_eq!(
            (s, code(&v)),
            (StatusCode::SERVICE_UNAVAILABLE, "unavailable")
        );
        assert_eq!(v["error"], "HCI raw-socket kernel panic: disabled");
    }
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
