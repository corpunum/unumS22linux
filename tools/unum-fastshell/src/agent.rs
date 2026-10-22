//! Home's Agent card: hold to speak, release and the agent takes over; holding
//! again while it works queues the next utterance; STOP cancels the turn, the
//! audio and the queue.
//!
//! The voice work is s22-assistant's card modes (fillers, spoken thinking,
//! -16 LUFS voice): `--card listen` records while held and prints the
//! transcript, `--card turn --text T` runs one turn and prints its states.
//! `Card` is the pure state machine (unit-tested); main runs its `CardCmd`s.
use serde_json::Value;
use std::collections::VecDeque;

pub const RELEASE_FILE: &str = "/run/s22-fastshell.card-release";

#[derive(Clone, Debug, PartialEq)]
pub enum Listen {
    /// started; the microphone is not open yet
    Starting,
    Listening,
    Transcribing,
}

#[derive(Clone, Debug, PartialEq)]
pub enum Status {
    /// queued behind a push-to-talk turn of the Volume-up button
    Waiting,
    Thinking,
    Tool(String),
    Speaking,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Turn {
    pub gen: u64,
    pub pid: Option<i32>,
    pub text: String,
    pub status: Status,
}

#[derive(Clone, Debug, PartialEq)]
pub enum CardCmd {
    /// s22-assistant --card listen --stop-file RELEASE_FILE
    Listen { gen: u64 },
    /// the finger is up: write the release note (unix seconds)
    Release,
    /// s22-assistant --card turn --text T
    Turn { gen: u64, text: String },
    /// SIGTERM to the child's process group (s22-say and its player included)
    Kill { pid: i32 },
    /// POST /api/chat/cancel for the voice session
    CancelApi,
}

#[derive(Default)]
pub struct Card {
    gen: u64,
    pub listen: Option<(u64, Listen)>,
    pub listen_pid: Option<i32>,
    pub turn: Option<Turn>,
    pub queue: VecDeque<String>,
    /// what the owner said last, and the agent's last reply
    pub you: String,
    pub reply: String,
    pub note: String,
    pub held: bool,
}

impl Card {
    pub fn active(&self) -> bool {
        self.listen.is_some() || self.turn.is_some()
    }

    /// Finger down on the card.
    pub fn press(&mut self) -> Vec<CardCmd> {
        self.held = true;
        if self.listen.is_some() {
            return vec![];
        }
        self.gen += 1;
        self.listen = Some((self.gen, Listen::Starting));
        self.note.clear();
        vec![CardCmd::Listen { gen: self.gen }]
    }

    /// Finger up (a short tap records the assistant's fixed window, like Talk).
    pub fn release(&mut self) -> Vec<CardCmd> {
        let was = std::mem::replace(&mut self.held, false);
        if was && self.listen.is_some() {
            vec![CardCmd::Release]
        } else {
            vec![]
        }
    }

    pub fn spawned(&mut self, turn: bool, gen: u64, pid: i32) {
        if turn {
            if let Some(t) = self.turn.as_mut().filter(|t| t.gen == gen) {
                t.pid = Some(pid);
            }
        } else if self.listen.as_ref().is_some_and(|l| l.0 == gen) {
            self.listen_pid = Some(pid);
        }
    }

    fn start_turn(&mut self, text: String) -> Vec<CardCmd> {
        self.gen += 1;
        self.you = text.clone();
        self.reply.clear();
        self.turn = Some(Turn { gen: self.gen, pid: None, text: text.clone(), status: Status::Thinking });
        vec![CardCmd::Turn { gen: self.gen, text }]
    }

    /// A typed utterance (ctl `agent_say`): same path as a transcript.
    pub fn say(&mut self, text: &str) -> Vec<CardCmd> {
        let t = text.trim().to_string();
        if t.is_empty() {
            return vec![];
        }
        if self.turn.is_some() {
            self.queue.push_back(t);
            return vec![];
        }
        self.start_turn(t)
    }

    /// An event line from `--card listen` (or main's synthetic {"event":"exit"}).
    pub fn listen_event(&mut self, gen: u64, v: &Value) -> Vec<CardCmd> {
        if self.listen.as_ref().map(|l| l.0) != Some(gen) {
            return vec![];
        }
        if let Some(t) = v["transcript"].as_str() {
            self.listen = None;
            self.listen_pid = None;
            let t = t.trim().to_string();
            if !t.chars().any(char::is_alphanumeric) {
                self.note = "Didn't catch that".into();
                return vec![];
            }
            if self.turn.is_some() {
                self.queue.push_back(t);
                return vec![];
            }
            return self.start_turn(t);
        }
        match (v["state"].as_str(), v["event"].as_str()) {
            (Some("listening"), _) => self.listen = Some((gen, Listen::Listening)),
            (Some("transcribing"), _) => self.listen = Some((gen, Listen::Transcribing)),
            (Some("error"), _) => self.note = format!("Listening failed: {}", v["error"].as_str().unwrap_or("?")),
            (_, Some("exit")) => {
                self.listen = None;
                self.listen_pid = None;
                if self.note.is_empty() && v["code"] != 0 {
                    self.note = "Listening stopped".into();
                }
            }
            _ => {}
        }
        vec![]
    }

    /// An event line from `--card turn` (or the synthetic exit).
    pub fn turn_event(&mut self, gen: u64, v: &Value) -> Vec<CardCmd> {
        let Some(t) = self.turn.as_mut().filter(|t| t.gen == gen) else { return vec![] };
        match (v["state"].as_str(), v["event"].as_str()) {
            (Some("waiting"), _) => t.status = Status::Waiting,
            (Some("thinking"), _) => t.status = Status::Thinking,
            (Some("tool"), _) => t.status = Status::Tool(v["name"].as_str().unwrap_or("?").to_string()),
            (Some("reply"), _) => self.reply = v["text"].as_str().unwrap_or("").to_string(),
            (Some("speaking"), _) => t.status = Status::Speaking,
            (Some("error"), _) => self.note = format!("The agent failed: {}", v["error"].as_str().unwrap_or("?")),
            (_, Some("exit")) => {
                self.turn = None;
                if let Some(next) = self.queue.pop_front() {
                    return self.start_turn(next);
                }
            }
            _ => {}
        }
        vec![]
    }

    /// STOP: cancel the turn (agent + audio), drop the queue and any recording.
    pub fn stop(&mut self) -> Vec<CardCmd> {
        let mut c = vec![];
        if let Some(t) = self.turn.take() {
            if let Some(pid) = t.pid {
                c.push(CardCmd::Kill { pid });
            }
            c.push(CardCmd::CancelApi);
        }
        if self.listen.take().is_some() {
            if let Some(pid) = self.listen_pid.take() {
                c.push(CardCmd::Kill { pid });
            }
        }
        let n = self.queue.len();
        self.queue.clear();
        self.held = false;
        self.note = if n > 0 { format!("Stopped (and {n} queued dropped)") } else { "Stopped".into() };
        c
    }

    /// The card's status line.
    pub fn status_line(&self) -> String {
        let mut s = match (&self.listen, &self.turn) {
            (Some((_, Listen::Starting)), None) | (Some((_, Listen::Listening)), None) => "Listening… release to send".to_string(),
            (Some((_, Listen::Transcribing)), None) => "Transcribing…".to_string(),
            (_, Some(t)) => match &t.status {
                Status::Waiting => "Waiting for the voice button's turn…".to_string(),
                Status::Thinking => "Thinking…".to_string(),
                Status::Tool(n) => format!("Running a tool: {n}"),
                Status::Speaking => "Speaking…".to_string(),
            },
            (None, None) => if self.note.is_empty() { "Hold to talk to your agent".to_string() } else { self.note.clone() },
        };
        if let (Some(l), Some(_)) = (&self.listen, &self.turn) {
            s.push_str(if l.1 == Listen::Transcribing { " · transcribing the next…" } else { " · listening for the next…" });
        }
        if !self.queue.is_empty() {
            s.push_str(&format!(" · queued ({})", self.queue.len()));
        }
        s
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn said(c: &mut Card, text: &str) -> Vec<CardCmd> {
        let mut out = c.press();
        let g = c.listen.as_ref().unwrap().0;
        c.spawned(false, g, 100 + g as i32);
        c.listen_event(g, &json!({"state": "listening"}));
        out.extend(c.release());
        c.listen_event(g, &json!({"state": "transcribing"}));
        out.extend(c.listen_event(g, &json!({"transcript": text})));
        out.extend(c.listen_event(g, &json!({"event": "exit", "code": 0})));
        out
    }

    #[test]
    fn hold_speak_release_starts_a_turn() {
        let mut c = Card::default();
        assert_eq!(c.status_line(), "Hold to talk to your agent");
        assert_eq!(c.press(), vec![CardCmd::Listen { gen: 1 }]);
        assert_eq!(c.status_line(), "Listening… release to send");
        assert!(c.press().is_empty(), "one recording at a time");
        c.listen_event(1, &json!({"state": "transcribing"}));
        assert_eq!(c.release(), vec![CardCmd::Release]);
        assert_eq!(c.status_line(), "Transcribing…");
        let cmds = c.listen_event(1, &json!({"transcript": " turn on the wifi "}));
        assert_eq!(cmds, vec![CardCmd::Turn { gen: 2, text: "turn on the wifi".into() }]);
        assert_eq!(c.you, "turn on the wifi");
        c.spawned(true, 2, 555);
        c.turn_event(2, &json!({"state": "tool", "name": "phone_device"}));
        assert_eq!(c.status_line(), "Running a tool: phone_device");
        c.turn_event(2, &json!({"state": "reply", "text": "Wi-Fi is on."}));
        c.turn_event(2, &json!({"state": "speaking"}));
        assert_eq!(c.status_line(), "Speaking…");
        assert_eq!(c.reply, "Wi-Fi is on.");
        assert!(c.turn_event(2, &json!({"event": "exit", "code": 0})).is_empty());
        assert!(!c.active());
    }

    #[test]
    fn holding_again_while_the_agent_works_queues() {
        let mut c = Card::default();
        said(&mut c, "first");
        let g1 = c.turn.as_ref().unwrap().gen;
        c.spawned(true, g1, 700);
        assert!(said(&mut c, "second").iter().all(|x| !matches!(x, CardCmd::Turn { .. })), "queued, not started");
        said(&mut c, "third");
        assert_eq!(c.queue, ["second", "third"]);
        assert!(c.status_line().ends_with("queued (2)"), "{}", c.status_line());
        // the running turn ends: the next one starts, in order
        let cmds = c.turn_event(g1, &json!({"event": "exit", "code": 0}));
        assert!(matches!(&cmds[..], [CardCmd::Turn { text, .. }] if text == "second"));
        assert_eq!(c.queue.len(), 1);
        // silence is not queued
        let before = c.queue.len();
        said(&mut c, "  ");
        assert_eq!(c.queue.len(), before);
    }

    #[test]
    fn stop_cancels_turn_audio_queue_and_recording() {
        let mut c = Card::default();
        said(&mut c, "first");
        let g = c.turn.as_ref().unwrap().gen;
        c.spawned(true, g, 900);
        said(&mut c, "second");
        c.press(); // listening for a third
        let lg = c.listen.as_ref().unwrap().0;
        c.spawned(false, lg, 901);
        let cmds = c.stop();
        assert_eq!(cmds, vec![CardCmd::Kill { pid: 900 }, CardCmd::CancelApi, CardCmd::Kill { pid: 901 }]);
        assert!(!c.active() && c.queue.is_empty());
        assert!(c.note.starts_with("Stopped"));
        // late lines from the killed children change nothing
        assert!(c.turn_event(g, &json!({"event": "exit", "code": -1})).is_empty());
        assert!(c.listen_event(lg, &json!({"transcript": "third"})).is_empty());
        assert!(!c.active());
        // STOP with nothing running is harmless
        assert!(Card::default().stop().is_empty());
    }

    #[test]
    fn listen_errors_and_waiting_behind_the_button() {
        let mut c = Card::default();
        c.press();
        c.listen_event(1, &json!({"state": "error", "error": "already listening"}));
        c.listen_event(1, &json!({"event": "exit", "code": 1}));
        assert!(!c.active());
        assert_eq!(c.status_line(), "Listening failed: already listening");
        said(&mut c, "hello");
        let g = c.turn.as_ref().unwrap().gen;
        c.turn_event(g, &json!({"state": "waiting"}));
        assert!(c.status_line().starts_with("Waiting"));
    }
}
