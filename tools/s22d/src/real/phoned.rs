//! Proxy to `s22-phoned`. phoned keeps the real policy (`tx_enabled`, the number allowlist,
//! emergency-number refusal, per-hour rate limits); s22d never second-guesses a refusal and never
//! loosens one. It only checks that a request is well formed before forwarding it.

use super::httpc::{over_tcp, over_unix, HttpError};
use crate::backends::Phoned;
use crate::config::Config;
use crate::error::{ApiError, ApiResult};
use axum::http::StatusCode;
use serde_json::{json, Value};
use std::sync::Arc;
use std::time::Duration;

pub struct PhonedProxy {
    cfg: Arc<Config>,
    pub timeout: Duration,
}

/// A number s22d is willing to forward: digits with an optional leading `+`, spaces, dashes, dots.
pub fn valid_number(n: &str) -> bool {
    let digits = n.chars().filter(|c| c.is_ascii_digit()).count();
    let body = n.strip_prefix('+').unwrap_or(n);
    (3..=16).contains(&digits)
        && body
            .chars()
            .all(|c| c.is_ascii_digit() || " -.".contains(c))
}

fn encode(s: &str) -> String {
    s.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' => (b as char).to_string(),
            _ => format!("%{b:02X}"),
        })
        .collect()
}

/// Map phoned's answer onto s22d's error vocabulary, keeping its own message.
pub fn map_reply(status: u16, body: Value) -> ApiResult {
    let msg = body["error"].as_str().unwrap_or("").to_string();
    match status {
        200..=299 => {
            let mut b = body;
            if let Some(o) = b.as_object_mut() {
                o.entry("ok").or_insert(Value::Bool(true));
            }
            // phoned reports a refused SMS with HTTP 200 and status "refused".
            if b["status"] == "refused" {
                return Err(ApiError::denied(
                    "refused_by_phoned",
                    b["error"]
                        .as_str()
                        .unwrap_or("refused by s22-phoned")
                        .to_string(),
                ));
            }
            Ok(b)
        }
        403 => Err(ApiError::denied("refused_by_phoned", msg)),
        429 => Err(ApiError::new(
            StatusCode::TOO_MANY_REQUESTS,
            "rate_limited",
            msg,
        )),
        503 => Err(ApiError::unavailable(format!("modem not ready: {msg}"))),
        400 | 404 | 422 => Err(ApiError::bad_request(format!("phoned: {msg}"))),
        s => Err(ApiError::upstream(format!("phoned answered {s}: {msg}"))),
    }
}

impl PhonedProxy {
    pub fn new(cfg: Arc<Config>) -> Self {
        Self {
            cfg,
            timeout: Duration::from_secs(20),
        }
    }

    fn call(&self, method: &str, target: &str, body: Option<Value>) -> ApiResult {
        let b = body.as_ref();
        let first = over_unix(&self.cfg.phoned_sock, method, target, b, self.timeout);
        let res = match first {
            Err(HttpError::Connect(_)) => {
                over_tcp(&self.cfg.phoned_tcp, method, target, b, self.timeout)
            }
            other => other,
        };
        match res {
            Ok((status, body)) => map_reply(status, body),
            Err(HttpError::Connect(e)) => Err(ApiError::unavailable(format!(
                "s22-phoned is not running ({e})"
            ))),
            Err(e) => Err(ApiError::upstream(format!("s22-phoned: {e}"))),
        }
    }
}

impl Phoned for PhonedProxy {
    fn availability(&self) -> Option<String> {
        if self.cfg.phoned_sock.exists() {
            None
        } else {
            Some(format!(
                "s22-phoned socket missing: {} (TCP fallback {})",
                self.cfg.phoned_sock.display(),
                self.cfg.phoned_tcp
            ))
        }
    }

    fn modem_status(&self) -> ApiResult {
        self.call("GET", "/status", None)
    }

    fn sms_list(&self, outbox: bool, since: Option<String>, limit: usize) -> ApiResult {
        let mut q = format!(
            "/sms/{}?limit={limit}",
            if outbox { "outbox" } else { "inbox" }
        );
        if let Some(s) = since {
            q += &format!("&since={}", encode(&s));
        }
        self.call("GET", &q, None)
    }

    fn sms_send(&self, number: &str, text: &str) -> ApiResult {
        self.call(
            "POST",
            "/sms/send",
            Some(json!({"to": number, "text": text})),
        )
    }

    fn calls(&self) -> ApiResult {
        self.call("GET", "/calls", None)
    }

    fn dial(&self, number: &str) -> ApiResult {
        self.call("POST", "/call/dial", Some(json!({"number": number})))
    }

    fn hangup(&self) -> ApiResult {
        self.call("POST", "/call/hangup", Some(json!({})))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::Tmp;
    use std::io::{Read, Write};
    use std::os::unix::net::UnixListener;

    /// One-shot fake phoned: answers every connection with (status, body) and records the request.
    fn fake_phoned(t: &Tmp, status: u16, body: &'static str) -> std::sync::mpsc::Receiver<String> {
        let p = t.path().join("run/s22-phoned.sock");
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        let l = UnixListener::bind(&p).unwrap();
        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            while let Ok((mut c, _)) = l.accept() {
                let mut buf = [0u8; 4096];
                let n = c.read(&mut buf).unwrap_or(0);
                let _ = tx.send(String::from_utf8_lossy(&buf[..n]).to_string());
                let _ = write!(
                    c,
                    "HTTP/1.1 {status} X\r\nContent-Length: {}\r\n\r\n{body}",
                    body.len()
                );
            }
        });
        rx
    }

    fn proxy(t: &Tmp) -> PhonedProxy {
        let mut c = Config::rooted(t.path());
        c.phoned_tcp = "127.0.0.1:1".into(); // nothing listens there
        PhonedProxy::new(Arc::new(c))
    }

    #[test]
    fn number_validation() {
        for ok in ["+306970000000", "6970000000", "+30 697-000.0000"] {
            assert!(valid_number(ok), "{ok}");
        }
        for bad in ["", "12", "abc", "+30;rm", "+1234567890123456789", "69 70 x"] {
            assert!(!valid_number(bad), "{bad}");
        }
    }

    #[test]
    fn forwards_sms_as_to_and_text() {
        let t = Tmp::new();
        let rx = fake_phoned(
            &t,
            200,
            r#"{"ok":true,"id":"m1","parts":1,"status":"sent"}"#,
        );
        let v = proxy(&t).sms_send("+3069", "hi").unwrap();
        assert_eq!(v["status"], "sent");
        let req = rx.recv().unwrap();
        assert!(req.starts_with("POST /sms/send"));
        assert!(req.contains(r#""to":"+3069""#));
    }

    #[test]
    fn phoned_refusals_stay_refusals() {
        let t = Tmp::new();
        let _rx = fake_phoned(
            &t,
            403,
            r#"{"ok":false,"error":"refused: number not in allowlist"}"#,
        );
        let e = proxy(&t).dial("+3069").unwrap_err();
        assert_eq!(
            (e.status, e.code),
            (StatusCode::FORBIDDEN, "refused_by_phoned")
        );
        assert!(e.message.contains("allowlist"));
    }

    #[test]
    fn phoned_refused_status_inside_a_200_is_still_a_refusal() {
        let e = map_reply(
            200,
            json!({"ok": true, "status": "refused", "error": "tx_enabled missing"}),
        )
        .unwrap_err();
        assert_eq!(e.code, "refused_by_phoned");
    }

    #[test]
    fn rate_limit_and_not_ready_map_through() {
        assert_eq!(
            map_reply(429, json!({"error": "10/hour"}))
                .unwrap_err()
                .status,
            StatusCode::TOO_MANY_REQUESTS
        );
        assert_eq!(
            map_reply(503, json!({"error": "no sim"})).unwrap_err().code,
            "unavailable"
        );
        assert_eq!(
            map_reply(500, json!({})).unwrap_err().code,
            "upstream_error"
        );
    }

    #[test]
    fn inbox_query_is_encoded() {
        let t = Tmp::new();
        let rx = fake_phoned(&t, 200, r#"{"ok":true,"messages":[]}"#);
        proxy(&t).sms_list(false, Some("a b&c".into()), 5).unwrap();
        assert!(rx
            .recv()
            .unwrap()
            .starts_with("GET /sms/inbox?limit=5&since=a%20b%26c "));
    }

    #[test]
    fn missing_daemon_is_unavailable() {
        let t = Tmp::new();
        let p = proxy(&t);
        assert!(p.availability().is_some());
        assert_eq!(p.modem_status().unwrap_err().code, "unavailable");
    }
}
