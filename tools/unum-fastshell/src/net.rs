//! Loopback HTTP (OpenUnum :18880, s22d :8766, s22-phoned :8095) and the
//! touchd control socket, on plain blocking threads. Results go back to the
//! main loop as `Msg` through a channel plus a wake-up pipe.
//!
//! Requests are HTTP/1.0 with `Connection: close`, so Node answers without
//! chunked encoding and a body ends at EOF; chunked bodies are still decoded
//! in case a server sends them anyway.
//!
//! Chat streaming follows tools/unum-shell (src/net.rs, src/sse.rs): open
//! `GET /api/chat/stream?sessionId=..&since=..` first, then `POST /api/chat`.
use serde_json::{json, Value};
use std::io::{Read, Write};
use std::net::TcpStream;
use std::os::fd::RawFd;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::Sender;
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

#[derive(Debug, Clone, PartialEq)]
pub struct Prompt {
    pub id: String,
    pub header: String,
    pub question: String,
    pub options: Vec<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum ChatEvent {
    Delta { token: String, replace: bool },
    Thinking,
    ToolStarted(String),
    ToolFinished { name: String, ok: bool },
    Prompt(Prompt),
    Snapshot { done: bool, pending: bool, reply: Option<String>, live: Option<String> },
    TurnEnd { reply: Option<String> },
}

pub enum Msg {
    Agent { model: Option<String>, health: Option<String>, sessions: Option<(usize, usize)>, running: Option<usize> },
    Device(Value),
    Chat(u64, ChatEvent),
    ChatReply(u64, Result<Option<String>, String>),
    ChatStreamClosed(u64, Option<String>),
    History(Vec<(bool, String)>),
    Phone(Value, Value, Value),
    Touchd(&'static str, Value),
    Ctl(crate::ctl::Call, Sender<String>),
    Assistant(Value),
    Radio(Value),
    /// s22-campreview event line (or exit / develop result) for camera process generation n.
    Camera(u64, Value),
    /// The photo thumbnail, decoded off the UI thread.
    CamThumb(Vec<u32>, i32, i32),
    /// A device-API answer: capability id, HTTP status (0 = s22d not reachable), body.
    DeviceCap(&'static str, u16, Value),
    /// s22-assistant card mode line: (turn?, generation, event).
    Card(bool, u64, Value),
}

#[derive(Clone)]
pub struct Net {
    pub tx: Sender<Msg>,
    pub wake_fd: RawFd,
    pub api: String,
    pub s22d: String,
    pub phoned: String,
    pub session: String,
    pub touchd_sock: String,
    pub turn: Arc<AtomicU64>,
}

impl Net {
    pub fn send(&self, m: Msg) {
        let _ = self.tx.send(m);
        let b = [1u8];
        unsafe { libc::write(self.wake_fd, b.as_ptr() as *const _, 1) };
    }

    pub fn spawn<F: FnOnce(Net) + Send + 'static>(&self, f: F) {
        let n = self.clone();
        std::thread::spawn(move || f(n));
    }
}

fn split_url(url: &str) -> Option<(String, String)> {
    let rest = url.strip_prefix("http://")?;
    let (host, path) = match rest.find('/') {
        Some(i) => (&rest[..i], &rest[i..]),
        None => (rest, "/"),
    };
    Some((host.to_string(), path.to_string()))
}

fn connect(host: &str, timeout: Duration) -> Result<TcpStream, String> {
    use std::net::ToSocketAddrs;
    let addr = host.to_socket_addrs().map_err(|e| e.to_string())?.next().ok_or("no address")?;
    let s = TcpStream::connect_timeout(&addr, timeout.min(Duration::from_secs(5))).map_err(|e| e.to_string())?;
    s.set_read_timeout(Some(timeout)).ok();
    s.set_write_timeout(Some(Duration::from_secs(10))).ok();
    s.set_nodelay(true).ok();
    Ok(s)
}

fn request_head(method: &str, host: &str, path: &str, body: Option<&str>) -> String {
    let mut h = format!("{method} {path} HTTP/1.0\r\nHost: {host}\r\nConnection: close\r\nAccept: */*\r\n");
    if let Some(b) = body {
        h.push_str(&format!("Content-Type: application/json\r\nContent-Length: {}\r\n", b.len()));
    }
    h.push_str("\r\n");
    if let Some(b) = body {
        h.push_str(b);
    }
    h
}

fn find_head_end(b: &[u8]) -> Option<usize> {
    b.windows(4).position(|w| w == b"\r\n\r\n").map(|i| i + 4)
}

fn parse_head(h: &str) -> (u16, bool) {
    let status = h.split_whitespace().nth(1).and_then(|s| s.parse().ok()).unwrap_or(0);
    let chunked = h.lines().any(|l| {
        let l = l.to_ascii_lowercase();
        l.starts_with("transfer-encoding:") && l.contains("chunked")
    });
    (status, chunked)
}

/// Incremental chunked-transfer decoder.
#[derive(Default)]
struct Dechunk {
    buf: Vec<u8>,
    left: usize,
    done: bool,
}

impl Dechunk {
    fn feed(&mut self, data: &[u8], out: &mut Vec<u8>) {
        self.buf.extend_from_slice(data);
        loop {
            if self.done {
                return;
            }
            if self.left > 0 {
                let n = self.left.min(self.buf.len());
                out.extend_from_slice(&self.buf[..n]);
                self.buf.drain(..n);
                self.left -= n;
                if self.left > 0 {
                    return;
                }
            }
            // strip a leading CRLF left from the previous chunk
            while self.buf.first() == Some(&b'\r') || self.buf.first() == Some(&b'\n') {
                self.buf.remove(0);
            }
            let Some(i) = self.buf.windows(2).position(|w| w == b"\r\n") else { return };
            let line = String::from_utf8_lossy(&self.buf[..i]).to_string();
            let size = usize::from_str_radix(line.split(';').next().unwrap_or("").trim(), 16).unwrap_or(0);
            self.buf.drain(..i + 2);
            if size == 0 {
                self.done = true;
                return;
            }
            self.left = size;
        }
    }
}

/// One request, whole body.
pub fn http(method: &str, url: &str, body: Option<&Value>, timeout: Duration) -> Result<(u16, Value), String> {
    let (host, path) = split_url(url).ok_or("bad url")?;
    let mut s = connect(&host, timeout)?;
    let b = body.map(|v| v.to_string());
    s.write_all(request_head(method, &host, &path, b.as_deref()).as_bytes()).map_err(|e| e.to_string())?;
    let mut raw = Vec::new();
    s.read_to_end(&mut raw).map_err(|e| e.to_string())?;
    let he = find_head_end(&raw).ok_or("no http header")?;
    let (status, chunked) = parse_head(&String::from_utf8_lossy(&raw[..he]));
    let mut bodyv = raw[he..].to_vec();
    if chunked {
        let mut d = Dechunk::default();
        let mut out = Vec::new();
        d.feed(&bodyv, &mut out);
        bodyv = out;
    }
    let v: Value = serde_json::from_slice(&bodyv).unwrap_or(Value::Null);
    Ok((status, v))
}

pub fn get(url: &str, timeout_s: u64) -> Result<Value, String> {
    let (st, v) = http("GET", url, None, Duration::from_secs(timeout_s))?;
    if (200..300).contains(&st) { Ok(v) } else { Err(error_text(st, &v)) }
}

pub fn error_text(status: u16, body: &Value) -> String {
    let msg = body["error"].as_str().or(body["message"].as_str()).unwrap_or("");
    match body["code"].as_str() {
        Some(code) => format!("{code}: {msg}"),
        None if msg.is_empty() => format!("HTTP {status}"),
        None => msg.to_string(),
    }
}

// ------------------------------------------------------------------ SSE

#[derive(Debug, Clone, PartialEq)]
pub struct RawEvent {
    pub event: Option<String>,
    pub data: String,
}

#[derive(Default)]
pub struct SseParser {
    buf: Vec<u8>,
}

impl SseParser {
    pub fn feed(&mut self, bytes: &[u8]) -> Vec<RawEvent> {
        self.buf.extend(bytes.iter().copied().filter(|b| *b != b'\r'));
        let mut out = vec![];
        while let Some(end) = self.buf.windows(2).position(|w| w == b"\n\n") {
            let block: Vec<u8> = self.buf.drain(..end + 2).collect();
            let s = String::from_utf8_lossy(&block).to_string();
            let mut event = None;
            let mut data: Vec<&str> = vec![];
            for line in s.lines() {
                if line.starts_with(':') || line.is_empty() {
                    continue;
                }
                let (field, value) = line.split_once(':').unwrap_or((line, ""));
                let value = value.strip_prefix(' ').unwrap_or(value);
                match field {
                    "event" => event = Some(value.to_string()),
                    "data" => data.push(value),
                    _ => {}
                }
            }
            if !data.is_empty() {
                out.push(RawEvent { event, data: data.join("\n") });
            }
        }
        out
    }
}

fn text(v: &Value) -> Option<String> {
    v.as_str().map(String::from)
}

/// Same mapping as tools/unum-shell/src/sse.rs.
pub fn decode(raw: &RawEvent) -> Option<ChatEvent> {
    let v: Value = serde_json::from_str(&raw.data).ok()?;
    Some(match raw.event.as_deref() {
        Some("content_delta") => ChatEvent::Delta { token: text(&v["token"])?, replace: v["final"] == true },
        Some("reasoning_start") | Some("reasoning_delta") | Some("agent_thinking") => ChatEvent::Thinking,
        Some("tool_call_started") => ChatEvent::ToolStarted(text(&v["tool"])?),
        Some("tool_call_completed") => ChatEvent::ToolFinished { name: text(&v["tool"])?, ok: v["resultOk"] != false },
        Some("tool_call_failed") => ChatEvent::ToolFinished { name: text(&v["tool"])?, ok: false },
        Some("user_prompt_requested") => ChatEvent::Prompt(Prompt {
            id: text(&v["promptId"])?,
            header: text(&v["header"]).unwrap_or_default(),
            question: text(&v["question"])?,
            options: v["options"]
                .as_array()
                .map(|a| a.iter().filter_map(|o| text(o).or_else(|| text(&o["label"]))).take(6).collect())
                .unwrap_or_default(),
        }),
        Some("turn_end") => ChatEvent::TurnEnd { reply: text(&v["reply"]) },
        Some("turn_cancelled") => ChatEvent::TurnEnd { reply: None },
        Some(_) => return None,
        None => ChatEvent::Snapshot { done: v["done"] == true, pending: v["pending"] == true, reply: text(&v["completed"]["reply"]), live: text(&v["liveContent"]) },
    })
}

pub fn iso_utc(t: SystemTime) -> String {
    let d = t.duration_since(UNIX_EPOCH).unwrap_or_default();
    let (secs, ms) = (d.as_secs() as i64, d.subsec_millis());
    let (days, rem) = (secs.div_euclid(86_400), secs.rem_euclid(86_400));
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!("{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}.{ms:03}Z", rem / 3600, rem % 3600 / 60, rem % 60)
}

fn urlenc(s: &str) -> String {
    s.bytes()
        .map(|b| if b.is_ascii_alphanumeric() || b"-_.~".contains(&b) { (b as char).to_string() } else { format!("%{b:02X}") })
        .collect()
}

/// Start one chat turn. The POST goes first: a stream opened before the turn
/// exists only replays the previous, completed turn and the server closes it
/// (seen on the phone 2026-10-21). The stream is opened ~300 ms later and
/// reopened while the turn runs.
pub fn chat_turn(net: &Net, id: u64, prompt: String) {
    net.turn.store(id, Ordering::SeqCst);
    net.spawn(move |n| {
        let mut tries = 0;
        std::thread::sleep(Duration::from_millis(300));
        loop {
            if n.turn.load(Ordering::SeqCst) != id || tries > 600 {
                return;
            }
            tries += 1;
            // no overlap with what an earlier stream delivered: every stream starts with a
            // snapshot whose liveContent is the whole answer so far, which resyncs the text
            let since = iso_utc(SystemTime::now());
            let r = stream_once(&n, id, &since);
            if n.turn.load(Ordering::SeqCst) != id {
                return;
            }
            if let Err(e) = &r {
                n.send(Msg::ChatStreamClosed(id, Some(e.clone())));
            }
            std::thread::sleep(Duration::from_millis(if r.is_ok() { 300 } else { 1500 }));
        }
    });
    net.spawn(move |n| {
        let r = http("POST", &format!("{}/api/chat", n.api), Some(&json!({"sessionId": n.session, "message": prompt})), Duration::from_secs(900));
        let out = match r {
            Err(e) => Err(e),
            Ok((st, body)) => {
                if st == 202 || body["pending"] == true {
                    Ok(None)
                } else if !(200..300).contains(&st) {
                    Err(error_text(st, &body))
                } else {
                    Ok(body["reply"].as_str().or(body["content"].as_str()).map(String::from))
                }
            }
        };
        n.send(Msg::ChatReply(id, out));
    });
}

fn stream_once(n: &Net, id: u64, since: &str) -> Result<(), String> {
    let url = format!("{}/api/chat/stream?sessionId={}&since={}", n.api, urlenc(&n.session), urlenc(since));
    let (host, path) = split_url(&url).ok_or("bad url")?;
    let mut s = connect(&host, Duration::from_secs(1))?;
    s.write_all(request_head("GET", &host, &path, None).as_bytes()).map_err(|e| e.to_string())?;
    let mut head = Vec::new();
    let mut started = false;
    let mut parser = SseParser::default();
    let mut dechunk: Option<Dechunk> = None;
    let mut buf = [0u8; 8192];
    let t0 = std::time::Instant::now();
    loop {
        if n.turn.load(Ordering::SeqCst) != id {
            return Ok(());
        }
        if t0.elapsed() > Duration::from_secs(1800) {
            return Err("stream too long".into());
        }
        let k = match s.read(&mut buf) {
            Ok(0) => return Ok(()),
            Ok(k) => k,
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock || e.kind() == std::io::ErrorKind::TimedOut => continue,
            Err(e) => return Err(e.to_string()),
        };
        let mut data: Vec<u8>;
        if !started {
            head.extend_from_slice(&buf[..k]);
            let Some(he) = find_head_end(&head) else { continue };
            let (st, chunked) = parse_head(&String::from_utf8_lossy(&head[..he]));
            if !(200..300).contains(&st) {
                return Err(format!("stream HTTP {st}"));
            }
            if chunked {
                dechunk = Some(Dechunk::default());
            }
            started = true;
            data = head[he..].to_vec();
        } else {
            data = buf[..k].to_vec();
        }
        if let Some(d) = dechunk.as_mut() {
            let mut out = Vec::new();
            d.feed(&data, &mut out);
            data = out;
        }
        for raw in parser.feed(&data) {
            if let Some(ev) = decode(&raw) {
                n.send(Msg::Chat(id, ev));
            }
        }
    }
}

pub fn model_label(raw: &str) -> String {
    if let Some(rest) = raw.strip_prefix("openai/") {
        return rest.to_string();
    }
    if let Some(rest) = raw.strip_prefix("llama-cpp-local/") {
        return format!("local {}", rest.trim_start_matches('/').trim_start_matches("models/"));
    }
    raw.to_string()
}

pub fn refresh_agent(net: &Net) {
    net.spawn(|n| {
        let model = get(&format!("{}/api/model/current", n.api), 6).ok().and_then(|m| m["model"].as_str().map(model_label));
        let health = match get(&format!("{}/api/health", n.api), 6) {
            Ok(h) => Some(if h["healthy"] == true { "healthy".to_string() } else { h["health"]["status"].as_str().unwrap_or("degraded").to_string() }),
            Err(_) => Some("down".to_string()),
        };
        let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs() as i64;
        let sessions = get(&format!("{}/api/sessions?limit=20", n.api), 6).ok().map(|v| {
            let ss = v["sessions"].as_array().cloned().unwrap_or_default();
            let recent = ss.iter().filter(|s| s["lastMessageAt"].as_str().and_then(parse_iso).is_some_and(|t| now - t < 3600)).count();
            (recent, ss.len())
        });
        let running = get(&format!("{}/api/missions", n.api), 6)
            .ok()
            .map(|v| v["missions"].as_array().map(|a| a.iter().filter(|m| m["status"] == "running").count()).unwrap_or(0));
        n.send(Msg::Agent { model, health, sessions, running });
    });
    net.spawn(|n| {
        if let Ok(v) = get(&format!("{}/v1/status", n.s22d), 8) {
            n.send(Msg::Device(v));
        }
    });
    // s22-phoned: registration + NR (5G NSA) state for the status-bar badge
    net.spawn(|n| {
        let v = get(&format!("{}/status", n.phoned), 6).unwrap_or(Value::Null);
        n.send(Msg::Radio(v));
    });
}

/// Status-bar radio badge from s22-phoned's /status: "5G" while the CP reports an NR
/// leg (NSA: act stays "lte", so `radio.nr_nsa` or an `nr_last_seen` within 120 s),
/// "4G" when registered on LTE, "3G"/"2G" for older RATs, None when not registered.
pub fn radio_badge(v: &Value, now: f64) -> Option<&'static str> {
    if v["ok"] != true {
        return None;
    }
    let reg = &v["registration"];
    let rs = reg["reg_status"].as_str().unwrap_or("");
    if !(rs.starts_with("home") || rs.starts_with("roam")) {
        return None;
    }
    let nr_recent = v["nr_last_seen"].as_f64().is_some_and(|t| (now - t).abs() < 120.0);
    match reg["act"].as_str().unwrap_or("") {
        "nr" | "5g" | "nr5g" => Some("5G"),
        "lte" | "lte_ca" | "4g" => Some(if v["radio"]["nr_nsa"] == true || nr_recent { "5G" } else { "4G" }),
        "umts" | "wcdma" | "hspa" | "3g" => Some("3G"),
        "gsm" | "gprs" | "edge" | "2g" => Some("2G"),
        _ => None,
    }
}

/// "2026-10-21T19:16:21.517Z" -> unix seconds.
pub fn parse_iso(s: &str) -> Option<i64> {
    let b = s.as_bytes();
    if b.len() < 19 {
        return None;
    }
    let n = |a: usize, l: usize| s.get(a..a + l)?.parse::<i64>().ok();
    let (y, mo, d, h, mi, se) = (n(0, 4)?, n(5, 2)?, n(8, 2)?, n(11, 2)?, n(14, 2)?, n(17, 2)?);
    let (y2, m2) = if mo <= 2 { (y - 1, mo + 9) } else { (y, mo - 3) };
    let era = y2.div_euclid(400);
    let yoe = y2 - era * 400;
    let doy = (153 * m2 + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    Some(days * 86_400 + h * 3600 + mi * 60 + se)
}

pub fn fetch_history(net: &Net) {
    net.spawn(|n| {
        if let Ok(v) = get(&format!("{}/api/sessions/{}?limit=30", n.api, urlenc(&n.session)), 10) {
            let lines: Vec<(bool, String)> = v["messages"]
                .as_array()
                .map(|a| {
                    a.iter()
                        .filter_map(|m| {
                            let you = match m["role"].as_str()? {
                                "user" => true,
                                "assistant" => false,
                                _ => return None,
                            };
                            let t = m["content"].as_str()?.trim();
                            (!t.is_empty()).then(|| (you, t.chars().take(3000).collect()))
                        })
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default();
            let k = lines.len().saturating_sub(30);
            n.send(Msg::History(lines[k..].to_vec()));
        }
    });
}

pub fn fetch_phone(net: &Net) {
    net.spawn(|n| {
        let st = get(&format!("{}/status", n.phoned), 20).unwrap_or_else(|e| json!({"ok": false, "error": e}));
        let sms = get(&format!("{}/sms/inbox?limit=20", n.phoned), 20).unwrap_or(Value::Null);
        let calls = get(&format!("{}/calls", n.phoned), 20).unwrap_or(Value::Null);
        n.send(Msg::Phone(st, sms, calls));
    });
}

pub fn answer_prompt(net: &Net, id: String, value: String) {
    net.spawn(move |n| {
        let _ = http("POST", &format!("{}/api/chat/answer", n.api), Some(&json!({"promptId": id, "value": value, "selected": [value]})), Duration::from_secs(10));
    });
}

pub fn cancel_turn(net: &Net) {
    net.spawn(|n| {
        let _ = http("POST", &format!("{}/api/chat/cancel", n.api), Some(&json!({"sessionId": n.session})), Duration::from_secs(10));
    });
}

/// One request to s22-touchd's ctl.sock (JSON line in, JSON line out).
pub fn touchd(sock: &str, req: &Value, timeout_s: u64) -> Value {
    use std::os::unix::net::UnixStream;
    let r = (|| -> std::io::Result<Value> {
        let mut s = UnixStream::connect(sock)?;
        s.set_read_timeout(Some(Duration::from_secs(timeout_s)))?;
        s.write_all(format!("{req}\n").as_bytes())?;
        let mut out = Vec::new();
        let mut buf = [0u8; 4096];
        loop {
            let k = s.read(&mut buf)?;
            if k == 0 {
                break;
            }
            out.extend_from_slice(&buf[..k]);
            if out.ends_with(b"\n") {
                break;
            }
        }
        Ok(serde_json::from_slice(&out).unwrap_or(Value::Null))
    })();
    r.unwrap_or_else(|e| json!({"ok": false, "error": e.to_string()}))
}

pub fn touchd_async(net: &Net, tag: &'static str, req: Value, timeout_s: u64) {
    net.spawn(move |n| {
        let v = touchd(&n.touchd_sock, &req, timeout_s);
        n.send(Msg::Touchd(tag, v));
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sse_blocks_split_across_chunks() {
        let mut p = SseParser::default();
        assert!(p.feed(b"event: content_delta\r\ndata: {\"tok").is_empty());
        let v = p.feed("en\":\"Γειά\"}\r\n\r\n".as_bytes());
        assert_eq!(v.len(), 1);
        assert_eq!(decode(&v[0]), Some(ChatEvent::Delta { token: "Γειά".into(), replace: false }));
    }

    #[test]
    fn snapshot_and_prompt_decode() {
        let s = RawEvent { event: None, data: r#"{"done":true,"completed":{"reply":"ok"}}"#.into() };
        assert_eq!(decode(&s), Some(ChatEvent::Snapshot { done: true, pending: false, reply: Some("ok".into()), live: None }));
        let p = RawEvent { event: Some("user_prompt_requested".into()), data: r#"{"promptId":"p1","question":"Q?","options":["a",{"label":"b"}]}"#.into() };
        match decode(&p) {
            Some(ChatEvent::Prompt(pr)) => assert_eq!(pr.options, vec!["a", "b"]),
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn dechunk_handles_split_chunks() {
        let mut d = Dechunk::default();
        let mut out = Vec::new();
        d.feed(b"5\r\nhel", &mut out);
        d.feed(b"lo\r\n6\r\n world\r\n0\r\n\r\n", &mut out);
        assert_eq!(out, b"hello world");
    }

    #[test]
    fn iso_roundtrip() {
        let t = UNIX_EPOCH + Duration::from_secs(1_792_608_748);
        let s = iso_utc(t);
        assert_eq!(parse_iso(&s), Some(1_792_608_748));
    }
}
