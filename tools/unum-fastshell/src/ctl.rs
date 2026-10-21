//! `shell.sock`: how s22-touchd drives the shell, the same protocol as
//! tools/unum-shell (one JSON line `{"fn":"card","args":[...]}` in, one line
//! `{"ok":true,"result":"..."}` out). Each connection gets a thread; the call
//! is answered by the main loop.
use crate::net::{Msg, Net};
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixListener;
use std::time::Duration;

#[derive(Debug, Clone, PartialEq)]
pub struct Call {
    pub function: String,
    pub args: Vec<String>,
}

impl Call {
    pub fn parse(line: &str) -> Result<Call, String> {
        let v: Value = serde_json::from_str(line.trim()).map_err(|e| format!("bad json: {e}"))?;
        let function = v["fn"].as_str().ok_or("missing fn")?.to_string();
        let args = match &v["args"] {
            Value::Null => vec![],
            Value::Array(a) => a
                .iter()
                .map(|x| match x {
                    Value::String(s) => s.clone(),
                    other => other.to_string(),
                })
                .collect(),
            _ => return Err("args must be an array".into()),
        };
        Ok(Call { function, args })
    }
    pub fn arg(&self, i: usize) -> &str {
        self.args.get(i).map(String::as_str).unwrap_or("")
    }
}

pub fn serve(path: String, net: Net) -> std::io::Result<()> {
    if let Some(dir) = std::path::Path::new(&path).parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let _ = std::fs::remove_file(&path);
    let l = UnixListener::bind(&path)?;
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600))?;
    std::thread::spawn(move || {
        for s in l.incoming().flatten() {
            let n = net.clone();
            std::thread::spawn(move || {
                let mut s = s;
                s.set_read_timeout(Some(Duration::from_secs(5))).ok();
                let mut line = String::new();
                let ok = BufReader::new(&s).take_line(&mut line);
                let answer = if !ok {
                    json!({"ok": false, "error": "bad_request: no request"})
                } else {
                    match Call::parse(&line) {
                        Err(e) => json!({"ok": false, "error": format!("bad_request: {e}")}),
                        Ok(call) => {
                            let (tx, rx) = std::sync::mpsc::channel();
                            n.send(Msg::Ctl(call, tx));
                            match rx.recv_timeout(Duration::from_secs(8)) {
                                Ok(r) => json!({"ok": true, "result": r}),
                                Err(_) => json!({"ok": false, "error": "shell_timeout"}),
                            }
                        }
                    }
                };
                let _ = s.write_all(format!("{answer}\n").as_bytes());
            });
        }
    });
    Ok(())
}

trait TakeLine {
    fn take_line(self, out: &mut String) -> bool;
}

impl<R: std::io::Read> TakeLine for BufReader<R> {
    fn take_line(mut self, out: &mut String) -> bool {
        matches!(self.read_line(out), Ok(n) if n > 0)
    }
}

/// Write `<dir>/<id>.json` = {"id","answer","ts"} atomically (touchd / s22-ui format).
pub fn write_confirm(dir: &str, id: &str, answer: &str) -> std::io::Result<()> {
    std::fs::create_dir_all(dir)?;
    let ts = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_millis() as u64;
    let tmp = format!("{dir}/{id}.json.tmp");
    std::fs::write(&tmp, json!({"id": id, "answer": answer, "ts": ts}).to_string())?;
    std::fs::rename(&tmp, format!("{dir}/{id}.json"))
}
