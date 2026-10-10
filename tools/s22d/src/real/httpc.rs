//! A very small blocking HTTP/1.1 client for `s22-phoned` (unix socket first, loopback TCP
//! second). One request per connection, JSON in and out, `Content-Length` bodies only: that is
//! all phoned's Python `http.server` ever sends.

use serde_json::Value;
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::path::Path;
use std::time::Duration;

#[derive(Debug)]
pub enum HttpError {
    Connect(String),
    Io(String),
    Protocol(String),
}

impl std::fmt::Display for HttpError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            HttpError::Connect(s) => write!(f, "cannot connect: {s}"),
            HttpError::Io(s) => write!(f, "i/o error: {s}"),
            HttpError::Protocol(s) => write!(f, "bad response: {s}"),
        }
    }
}

pub fn request_on<S: Read + Write>(
    s: &mut S,
    method: &str,
    target: &str,
    body: Option<&Value>,
) -> Result<(u16, Value), HttpError> {
    let payload = body.map(|b| b.to_string()).unwrap_or_default();
    let mut head = format!("{method} {target} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\nAccept: application/json\r\n");
    if body.is_some() {
        head += &format!(
            "Content-Type: application/json\r\nContent-Length: {}\r\n",
            payload.len()
        );
    }
    head += "\r\n";
    s.write_all(head.as_bytes())
        .and_then(|_| s.write_all(payload.as_bytes()))
        .map_err(|e| HttpError::Io(e.to_string()))?;
    let mut raw = Vec::new();
    let mut buf = [0u8; 4096];
    loop {
        match s.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => raw.extend_from_slice(&buf[..n]),
            Err(e) => return Err(HttpError::Io(e.to_string())),
        }
        if raw.len() > 4 << 20 {
            return Err(HttpError::Protocol("response too large".into()));
        }
    }
    parse_response(&raw)
}

pub fn parse_response(raw: &[u8]) -> Result<(u16, Value), HttpError> {
    let split = raw
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .ok_or_else(|| HttpError::Protocol("no header terminator".into()))?;
    let head = String::from_utf8_lossy(&raw[..split]);
    let status: u16 = head
        .lines()
        .next()
        .and_then(|l| l.split_whitespace().nth(1))
        .and_then(|c| c.parse().ok())
        .ok_or_else(|| HttpError::Protocol("bad status line".into()))?;
    let body = &raw[split + 4..];
    let len = head
        .lines()
        .find_map(|l| {
            l.to_ascii_lowercase()
                .strip_prefix("content-length:")
                .map(|v| v.trim().parse::<usize>().ok())
        })
        .flatten();
    let body = match len {
        Some(n) if n <= body.len() => &body[..n],
        _ => body,
    };
    if body.iter().all(|b| b.is_ascii_whitespace()) {
        return Ok((status, Value::Null));
    }
    let v =
        serde_json::from_slice(body).map_err(|e| HttpError::Protocol(format!("not JSON: {e}")))?;
    Ok((status, v))
}

pub fn over_unix(
    path: &Path,
    method: &str,
    target: &str,
    body: Option<&Value>,
    timeout: Duration,
) -> Result<(u16, Value), HttpError> {
    let mut s = UnixStream::connect(path)
        .map_err(|e| HttpError::Connect(format!("{}: {e}", path.display())))?;
    let _ = s.set_read_timeout(Some(timeout));
    let _ = s.set_write_timeout(Some(timeout));
    request_on(&mut s, method, target, body)
}

pub fn over_tcp(
    addr: &str,
    method: &str,
    target: &str,
    body: Option<&Value>,
    timeout: Duration,
) -> Result<(u16, Value), HttpError> {
    use std::net::{TcpStream, ToSocketAddrs};
    let a = addr
        .to_socket_addrs()
        .map_err(|e| HttpError::Connect(format!("{addr}: {e}")))?
        .next()
        .ok_or_else(|| HttpError::Connect(format!("{addr}: no address")))?;
    let mut s = TcpStream::connect_timeout(&a, Duration::from_secs(2))
        .map_err(|e| HttpError::Connect(format!("{addr}: {e}")))?;
    let _ = s.set_read_timeout(Some(timeout));
    let _ = s.set_write_timeout(Some(timeout));
    request_on(&mut s, method, target, body)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_status_and_json_body() {
        let raw = b"HTTP/1.1 403 Forbidden\r\nContent-Type: application/json\r\ncontent-length: 27\r\n\r\n{\"ok\":false,\"error\":\"nope\"}";
        let (s, v) = parse_response(raw).unwrap();
        assert_eq!(s, 403);
        assert_eq!(v["error"], "nope");
    }

    #[test]
    fn empty_body_is_null_and_garbage_is_an_error() {
        assert_eq!(
            parse_response(b"HTTP/1.1 204 No Content\r\n\r\n").unwrap(),
            (204, Value::Null)
        );
        assert!(parse_response(b"nonsense").is_err());
        assert!(parse_response(b"HTTP/1.1 200 OK\r\n\r\nnot json").is_err());
    }

    #[test]
    fn round_trip_over_a_unix_socket() {
        use std::os::unix::net::UnixListener;
        let t = crate::testutil::Tmp::new();
        let p = t.path().join("s.sock");
        let l = UnixListener::bind(&p).unwrap();
        let h = std::thread::spawn(move || {
            let (mut c, _) = l.accept().unwrap();
            let mut buf = [0u8; 2048];
            let n = c.read(&mut buf).unwrap();
            let req = String::from_utf8_lossy(&buf[..n]).to_string();
            let body = r#"{"ok":true}"#;
            write!(
                c,
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
            req
        });
        let (s, v) = over_unix(
            &p,
            "POST",
            "/x?a=1",
            Some(&serde_json::json!({"k": 1})),
            Duration::from_secs(2),
        )
        .unwrap();
        assert_eq!((s, v["ok"].clone()), (200, Value::Bool(true)));
        let req = h.join().unwrap();
        assert!(req.starts_with("POST /x?a=1 HTTP/1.1"));
        assert!(req.contains("Content-Length: 7"));
        assert!(over_unix(
            &t.path().join("none"),
            "GET",
            "/",
            None,
            Duration::from_secs(1)
        )
        .is_err());
    }
}
