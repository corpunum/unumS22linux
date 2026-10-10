//! Owner confirmation on the phone's own screen, using the protocol the touch UI already has:
//! ask `s22-touchd` to show a confirm card (`ui confirm <id> <question> <timeout>` on `ctl.sock`),
//! then wait for the card to write `<confirm dir>/<id>.json` with `{"answer": "yes"|"no"}`.
//! Anything other than an explicit yes is a denial.

use super::touch;
use crate::backends::{Answer, Confirmer};
use crate::config::Config;
use serde_json::json;
use std::io::Read;
use std::sync::Arc;
use std::time::{Duration, Instant};

pub struct TouchConfirmer {
    cfg: Arc<Config>,
    /// Extra time to wait for the answer file after the card's own timeout.
    pub grace: Duration,
    pub poll: Duration,
}

fn new_id() -> String {
    let mut b = [0u8; 6];
    let ok = std::fs::File::open("/dev/urandom")
        .and_then(|mut f| f.read_exact(&mut b))
        .is_ok();
    if !ok {
        let n = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        b.copy_from_slice(&n.to_le_bytes()[..6]);
    }
    format!(
        "d{}",
        b.iter().map(|x| format!("{x:02x}")).collect::<String>()
    )
}

impl TouchConfirmer {
    pub fn new(cfg: Arc<Config>) -> Self {
        Self {
            cfg,
            grace: Duration::from_secs(5),
            poll: Duration::from_millis(250),
        }
    }
}

impl Confirmer for TouchConfirmer {
    fn ask(&self, question: &str, timeout_s: u64) -> Answer {
        let id = new_id();
        let q: String = question.chars().take(500).collect();
        let req = json!({"cmd": "ui", "args": ["confirm", id, q, timeout_s], "timeout": 8});
        let reply = match touch::request(&self.cfg.touch_sock, &req, Duration::from_secs(12)) {
            Ok(r) => r,
            Err(e) => return Answer::Unavailable(e),
        };
        if reply["ok"] == true && reply["result"] == "asking" {
            // fall through to waiting
        } else if reply["result"] == "busy" {
            return Answer::Busy;
        } else {
            return Answer::Unavailable(format!("touch UI did not show the question: {reply}"));
        }
        let file = self.cfg.confirm_dir.join(format!("{id}.json"));
        let until = Instant::now() + Duration::from_secs(timeout_s) + self.grace;
        while Instant::now() < until {
            if let Ok(text) = std::fs::read_to_string(&file) {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                    let _ = std::fs::remove_file(&file);
                    return match v["answer"].as_str() {
                        Some("yes") => Answer::Yes,
                        Some("timeout") => Answer::Timeout,
                        _ => Answer::No,
                    };
                }
            }
            std::thread::sleep(self.poll);
        }
        Answer::Timeout
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::Tmp;
    use serde_json::{json, Value};

    fn confirmer(t: &Tmp) -> TouchConfirmer {
        let mut c = TouchConfirmer::new(Arc::new(Config::rooted(t.path())));
        c.grace = Duration::from_millis(300);
        c.poll = Duration::from_millis(10);
        c
    }

    fn answering(t: &Tmp, answer: &'static str) -> std::sync::mpsc::Receiver<Value> {
        let dir = Config::rooted(t.path()).confirm_dir;
        std::fs::create_dir_all(&dir).unwrap();
        touch::fake::serve(&Config::rooted(t.path()).touch_sock, move |req| {
            let id = req["args"][1].as_str().unwrap().to_string();
            std::fs::write(
                dir.join(format!("{id}.json")),
                json!({"answer": answer}).to_string(),
            )
            .unwrap();
            json!({"ok": true, "result": "asking"})
        })
    }

    #[test]
    fn yes_and_no_follow_the_card_answer() {
        let t = Tmp::new();
        let rx = answering(&t, "yes");
        assert_eq!(confirmer(&t).ask("Send SMS?", 5), Answer::Yes);
        let req = rx.recv().unwrap();
        assert_eq!(req["cmd"], "ui");
        assert_eq!(req["args"][0], "confirm");
        assert_eq!(req["args"][2], "Send SMS?");
        assert_eq!(req["args"][3], 5);
        let t = Tmp::new();
        let _rx = answering(&t, "no");
        assert_eq!(confirmer(&t).ask("x", 5), Answer::No);
        let t = Tmp::new();
        let _rx = answering(&t, "maybe");
        assert_eq!(confirmer(&t).ask("x", 5), Answer::No);
    }

    #[test]
    fn answer_file_is_consumed() {
        let t = Tmp::new();
        let _rx = answering(&t, "yes");
        confirmer(&t).ask("x", 5);
        let left = std::fs::read_dir(Config::rooted(t.path()).confirm_dir)
            .unwrap()
            .count();
        assert_eq!(left, 0);
    }

    #[test]
    fn no_answer_times_out() {
        let t = Tmp::new();
        let _rx = touch::fake::serve(
            &Config::rooted(t.path()).touch_sock,
            |_| json!({"ok": true, "result": "asking"}),
        );
        assert_eq!(confirmer(&t).ask("x", 0), Answer::Timeout);
    }

    #[test]
    fn busy_screen_and_missing_ui_are_not_a_yes() {
        let t = Tmp::new();
        let _rx = touch::fake::serve(
            &Config::rooted(t.path()).touch_sock,
            |_| json!({"ok": true, "result": "busy"}),
        );
        assert_eq!(confirmer(&t).ask("x", 1), Answer::Busy);
        let t = Tmp::new();
        let _rx = touch::fake::serve(
            &Config::rooted(t.path()).touch_sock,
            |_| json!({"ok": false, "error": "no_session"}),
        );
        assert!(matches!(confirmer(&t).ask("x", 1), Answer::Unavailable(_)));
        let t = Tmp::new();
        assert!(matches!(confirmer(&t).ask("x", 1), Answer::Unavailable(_)));
    }
}
