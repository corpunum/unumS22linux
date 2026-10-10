//! Client for `s22-touchd`'s control socket (`ctl.sock`): one JSON request per connection,
//! one JSON line back. The same socket the `s22-ui` CLI and the `s22-ui` OpenUnum plugin use.

use serde_json::Value;
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::path::Path;
use std::time::Duration;

pub fn request(sock: &Path, req: &Value, timeout: Duration) -> Result<Value, String> {
    let mut s = UnixStream::connect(sock).map_err(|e| format!("{}: {e}", sock.display()))?;
    let _ = s.set_read_timeout(Some(timeout));
    let _ = s.set_write_timeout(Some(timeout));
    s.write_all(format!("{req}\n").as_bytes())
        .map_err(|e| e.to_string())?;
    let mut data = Vec::new();
    let mut buf = [0u8; 4096];
    while !data.ends_with(b"\n") && data.len() < 1 << 20 {
        match s.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => data.extend_from_slice(&buf[..n]),
            Err(e) => return Err(e.to_string()),
        }
    }
    serde_json::from_slice(&data).map_err(|e| format!("bad touchd reply: {e}"))
}

#[cfg(test)]
pub mod fake {
    use serde_json::Value;
    use std::io::{Read, Write};
    use std::os::unix::net::UnixListener;
    use std::path::Path;
    use std::sync::mpsc::{channel, Receiver};

    /// A fake touchd: answers each request with `reply(request)` and forwards the request.
    pub fn serve(sock: &Path, reply: impl Fn(&Value) -> Value + Send + 'static) -> Receiver<Value> {
        std::fs::create_dir_all(sock.parent().unwrap()).unwrap();
        let l = UnixListener::bind(sock).unwrap();
        let (tx, rx) = channel();
        std::thread::spawn(move || {
            while let Ok((mut c, _)) = l.accept() {
                let mut data = Vec::new();
                let mut b = [0u8; 1024];
                while !data.ends_with(b"\n") {
                    match c.read(&mut b) {
                        Ok(0) | Err(_) => break,
                        Ok(n) => data.extend_from_slice(&b[..n]),
                    }
                }
                let req: Value = serde_json::from_slice(&data).unwrap_or(Value::Null);
                let _ = c.write_all(format!("{}\n", reply(&req)).as_bytes());
                let _ = tx.send(req);
            }
        });
        rx
    }
}
