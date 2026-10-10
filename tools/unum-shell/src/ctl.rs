//! `shell.sock`: how `s22-touchd` drives the shell. One JSON line in (`{"fn":"card","args":[...]}`),
//! one JSON line out (`{"ok":true,"result":"card 3"}`). The functions are the ones the Quickshell
//! `touch` IPC target has (see `model::Shell::call`), so gestures, cards and confirmation questions
//! reach unum-shell through the same touchd commands that reach Quickshell.

use crate::model::Call;
use serde_json::{json, Value};
use std::future::Future;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::{UnixListener, UnixStream};

const MAX_LINE: usize = 64 * 1024;

pub fn reply_json(result: Result<String, String>) -> String {
    match result {
        Ok(r) => json!({"ok": true, "result": r}),
        Err(e) => json!({"ok": false, "error": e}),
    }
    .to_string()
}

async fn handle<F, Fut>(stream: UnixStream, handler: F)
where
    F: Fn(Call) -> Fut,
    Fut: Future<Output = String>,
{
    let (r, mut w) = stream.into_split();
    let mut line = String::new();
    let mut reader = BufReader::new(r.take(MAX_LINE as u64));
    let read = tokio::time::timeout(Duration::from_secs(5), reader.read_line(&mut line)).await;
    let answer = match read {
        Ok(Ok(n)) if n > 0 => match Call::parse(&line) {
            Ok(call) => reply_json(Ok(handler(call).await)),
            Err(e) => reply_json(Err(format!("bad_request: {e}"))),
        },
        _ => reply_json(Err("bad_request: no request".into())),
    };
    let _ = w.write_all(format!("{answer}\n").as_bytes()).await;
}

use tokio::io::AsyncReadExt;

/// Listen on `path` (mode 0600, replacing a stale socket) and answer every request with `handler`.
pub async fn serve<F, Fut>(path: PathBuf, handler: F) -> std::io::Result<()>
where
    F: Fn(Call) -> Fut + Clone + Send + 'static,
    Fut: Future<Output = String> + Send + 'static,
{
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let _ = std::fs::remove_file(&path);
    let listener = UnixListener::bind(&path)?;
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600))?;
    loop {
        let (stream, _) = listener.accept().await?;
        let h = handler.clone();
        tokio::spawn(async move { handle(stream, h).await });
    }
}

/// Tell the asker (s22d, `s22-ui confirm`, the s22-device plugin) what the owner answered:
/// `<dir>/<id>.json` containing `{"id","answer","ts"}`, renamed into place so a reader never sees half of it.
pub fn write_confirm(dir: &Path, id: &str, answer: &str) -> std::io::Result<()> {
    std::fs::create_dir_all(dir)?;
    let ts = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64;
    let body: Value = json!({"id": id, "answer": answer, "ts": ts});
    let tmp = dir.join(format!("{id}.json.tmp"));
    std::fs::write(&tmp, body.to_string())?;
    std::fs::rename(&tmp, dir.join(format!("{id}.json")))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU32, Ordering};

    static N: AtomicU32 = AtomicU32::new(0);

    fn tmp() -> PathBuf {
        let d = std::env::temp_dir().join(format!(
            "unum-shell-ctl-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::SeqCst)
        ));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    async fn ask(sock: &Path, line: &str) -> Value {
        for _ in 0..100 {
            if sock.exists() {
                break;
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        let mut s = UnixStream::connect(sock).await.unwrap();
        s.write_all(format!("{line}\n").as_bytes()).await.unwrap();
        let mut out = String::new();
        BufReader::new(s).read_line(&mut out).await.unwrap();
        serde_json::from_str(&out).unwrap()
    }

    #[tokio::test]
    async fn requests_reach_the_handler_and_answers_come_back() {
        let dir = tmp();
        let sock = dir.join("shell.sock");
        let task = tokio::spawn(serve(sock.clone(), |c: Call| async move {
            format!("{}:{}", c.function, c.args.join("|"))
        }));
        let v = ask(&sock, r#"{"fn":"card","args":["T","B","8"]}"#).await;
        assert_eq!(v, json!({"ok": true, "result": "card:T|B|8"}));
        let v = ask(&sock, r#"{"fn":"home"}"#).await;
        assert_eq!(v["result"], "home:");
        assert_eq!(
            std::fs::metadata(&sock).unwrap().permissions().mode() & 0o777,
            0o600
        );
        task.abort();
    }

    #[tokio::test]
    async fn garbage_gets_an_error_not_a_crash() {
        let dir = tmp();
        let sock = dir.join("shell.sock");
        let task = tokio::spawn(serve(sock.clone(), |_c: Call| async { "x".to_string() }));
        for bad in ["not json", r#"{"args":[]}"#, ""] {
            let v = ask(&sock, bad).await;
            assert_eq!(v["ok"], false, "{bad}");
            assert!(v["error"].as_str().unwrap().starts_with("bad_request"));
        }
        assert_eq!(
            ask(&sock, r#"{"fn":"ok"}"#).await["ok"],
            true,
            "still serving"
        );
        task.abort();
    }

    #[tokio::test]
    async fn a_stale_socket_file_is_replaced() {
        let dir = tmp();
        let sock = dir.join("shell.sock");
        std::fs::write(&sock, "stale").unwrap();
        let task = tokio::spawn(serve(sock.clone(), |_c: Call| async {
            "fresh".to_string()
        }));
        tokio::time::sleep(Duration::from_millis(100)).await;
        assert_eq!(ask(&sock, r#"{"fn":"x"}"#).await["result"], "fresh");
        task.abort();
    }

    #[test]
    fn confirm_answers_are_written_atomically_in_touchd_format() {
        let dir = tmp().join("confirm");
        write_confirm(&dir, "dab12", "yes").unwrap();
        let v: Value =
            serde_json::from_str(&std::fs::read_to_string(dir.join("dab12.json")).unwrap())
                .unwrap();
        assert_eq!(
            (v["id"].clone(), v["answer"].clone()),
            (json!("dab12"), json!("yes"))
        );
        assert!(v["ts"].as_u64().unwrap() > 1_700_000_000_000);
        assert!(!dir.join("dab12.json.tmp").exists());
        let names: Vec<_> = std::fs::read_dir(&dir)
            .unwrap()
            .map(|e| e.unwrap().file_name())
            .collect();
        assert_eq!(names.len(), 1);
    }
}
