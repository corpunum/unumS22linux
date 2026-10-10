//! The shell's state and the rules that change it. No iced types here, so every rule (cards,
//! confirmation, the touchd control protocol, chat streaming) is unit-tested without a window.

use crate::sse::ChatEvent;
use serde_json::{json, Value};
use std::time::{Duration, Instant};

pub const KNOWN_APPS: [&str; 5] = ["chat", "phone", "camera", "files", "settings"];
const MAX_CARDS: usize = 3;
const MAX_LINES: usize = 60;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Page {
    Home,
    Chat,
    Phone,
    Camera,
    Files,
    Settings,
}

impl Page {
    pub fn name(self) -> &'static str {
        match self {
            Page::Home => "",
            Page::Chat => "chat",
            Page::Phone => "phone",
            Page::Camera => "camera",
            Page::Files => "files",
            Page::Settings => "settings",
        }
    }

    pub fn from_app(app: &str) -> Option<Page> {
        Some(match app {
            "chat" => Page::Chat,
            "phone" => Page::Phone,
            "camera" => Page::Camera,
            "files" => Page::Files,
            "settings" => Page::Settings,
            _ => return None,
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Who {
    You,
    Agent,
    Tool,
    System,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Line {
    pub who: Who,
    pub text: String,
}

/// Chat with the phone's OpenUnum in session "touch", including the turn that is streaming now.
#[derive(Debug, Clone, Default)]
pub struct Chat {
    pub lines: Vec<Line>,
    /// Assistant text received so far in the running turn.
    pub live: String,
    /// What the agent is doing right now ("thinking", "using wifi_status").
    pub activity: Option<String>,
    pub busy: bool,
}

impl Chat {
    pub fn push(&mut self, who: Who, text: impl Into<String>) {
        self.lines.push(Line {
            who,
            text: text.into(),
        });
        if self.lines.len() > MAX_LINES {
            self.lines.drain(..self.lines.len() - MAX_LINES);
        }
    }

    pub fn start_turn(&mut self, prompt: &str) {
        self.push(Who::You, prompt);
        self.live.clear();
        self.activity = Some("thinking".into());
        self.busy = true;
    }

    /// Fold one streamed event into the running turn. Returns an agent prompt when the agent asks one.
    pub fn apply(&mut self, ev: ChatEvent) -> Option<Prompt> {
        match ev {
            ChatEvent::Delta { token, replace } => {
                if replace {
                    self.live = token;
                } else {
                    self.live.push_str(&token);
                }
                self.activity = None;
            }
            ChatEvent::Thinking => self.activity = Some("thinking".into()),
            ChatEvent::ToolStarted(name) => self.activity = Some(format!("using {name}")),
            ChatEvent::ToolFinished { name, ok } => {
                if !ok {
                    self.push(Who::Tool, format!("{name} failed"));
                }
                self.activity = Some("thinking".into());
            }
            ChatEvent::Prompt(p) => return Some(p),
            ChatEvent::Snapshot { done, reply, live } => {
                if let Some(l) = live.filter(|l| !l.is_empty() && self.live.is_empty()) {
                    self.live = l;
                }
                if done {
                    self.finish(reply);
                }
            }
            ChatEvent::TurnEnd { reply } => self.finish(reply),
        }
        None
    }

    /// End the turn with the final reply (preferred) or whatever streamed in.
    pub fn finish(&mut self, reply: Option<String>) {
        if !self.busy {
            return;
        }
        let text = reply
            .filter(|r| !r.trim().is_empty())
            .unwrap_or_else(|| std::mem::take(&mut self.live));
        self.live.clear();
        if !text.trim().is_empty() {
            self.push(Who::Agent, text);
        }
        self.activity = None;
        self.busy = false;
    }

    pub fn fail(&mut self, error: &str) {
        self.push(Who::System, format!("Message failed: {error}"));
        self.live.clear();
        self.activity = None;
        self.busy = false;
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Card {
    pub id: u32,
    pub title: String,
    pub body: String,
    pub until: Instant,
}

/// A yes/no question from `s22-touchd` (`ui confirm`): the owner's answer goes back as a file.
#[derive(Debug, Clone, PartialEq)]
pub struct Confirm {
    pub id: String,
    pub question: String,
    pub until: Instant,
    /// Seconds left, refreshed by `tick`, for the countdown on the card.
    pub remaining_s: u64,
}

/// A question the agent asked mid-turn (`ask_user`): answered through `POST /api/chat/answer`.
#[derive(Debug, Clone, PartialEq)]
pub struct Prompt {
    pub id: String,
    pub header: String,
    pub question: String,
    pub options: Vec<String>,
}

/// What the shell wants done outside its own state.
#[derive(Debug, Clone, PartialEq)]
pub enum Effect {
    Keyboard(bool),
    Refresh,
    /// Write `<confirm dir>/<id>.json` with this answer (`yes`, `no`, `timeout`).
    WriteConfirm {
        id: String,
        answer: &'static str,
    },
}

/// One call from `s22-touchd` on `shell.sock`: the same functions the Quickshell `touch` IPC has.
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
}

/// The status strip: battery, heat and network from s22d, model and health from OpenUnum.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Strip {
    pub battery_pct: Option<u32>,
    pub charging: Option<bool>,
    pub temp_c: Option<f64>,
    pub wifi: Option<String>,
    pub modem: Option<String>,
    pub display: Option<String>,
    pub brightness_pct: Option<u32>,
    pub muted: Option<bool>,
    pub model: Option<String>,
    pub healthy: Option<bool>,
    pub s22d: Option<bool>,
}

impl Strip {
    /// Take the fields `GET /v1/status` returns (API.md). Missing fields stay unknown.
    pub fn apply_status(&mut self, v: &Value) {
        self.s22d = Some(true);
        self.battery_pct = v["battery"]["percent"].as_u64().map(|p| p as u32);
        self.charging = v["battery"]["charging"].as_bool();
        self.temp_c = v["thermal_max_c"].as_f64();
        self.wifi = v["network"]["wlan0"].as_str().map(String::from);
        self.modem = v["modem"].as_str().map(String::from);
        self.display = v["display"]["state"].as_str().map(String::from);
        self.muted = v["audio"]["muted"].as_bool();
        let (b, m) = (
            v["display"]["brightness"].as_u64(),
            v["display"]["max_brightness"].as_u64(),
        );
        if let (Some(b), Some(m)) = (b, m.filter(|m| *m > 0)) {
            self.brightness_pct = Some(((b * 100 + m / 2) / m) as u32);
        }
    }

    pub fn s22d_down(&mut self) {
        *self = Strip {
            model: self.model.take(),
            healthy: self.healthy,
            s22d: Some(false),
            ..Strip::default()
        };
    }

    pub fn battery_label(&self) -> String {
        match (self.battery_pct, self.charging) {
            (Some(p), Some(true)) => format!("{p}% charging"),
            (Some(p), _) => format!("{p}%"),
            _ => "battery -".into(),
        }
    }

    pub fn temp_label(&self) -> String {
        self.temp_c
            .map(|t| format!("{t:.0} C"))
            .unwrap_or_else(|| "- C".into())
    }

    pub fn network_label(&self) -> String {
        match self.wifi.as_deref() {
            Some("up") => "wifi".into(),
            Some(s) => format!("wifi {s}"),
            None => "no network info".into(),
        }
    }

    pub fn line(&self) -> String {
        format!(
            "{}  |  {}  |  {}",
            self.battery_label(),
            self.temp_label(),
            self.network_label()
        )
    }

    pub fn headline(&self) -> &'static str {
        match (self.s22d, self.healthy) {
            (Some(false), _) => "DEVICE DAEMON OFFLINE",
            (_, Some(false)) => "OPENUNUM OFFLINE",
            (_, Some(true)) => "OPENUNUM ONLINE",
            _ => "CONNECTING",
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct PhoneInfo {
    pub modem_state: Option<String>,
    pub sim: Option<String>,
    pub operator: Option<String>,
    pub signal: Option<String>,
    pub messages: Vec<(String, String)>,
    pub error: Option<String>,
}

impl PhoneInfo {
    pub fn apply(&mut self, modem: &Value, sms: Option<&Value>) {
        let s = |v: &Value| match v {
            Value::String(s) => Some(s.clone()),
            Value::Null => None,
            other => Some(other.to_string()),
        };
        self.modem_state = s(&modem["modem_state"]);
        self.sim = s(&modem["sim"]);
        self.operator = s(&modem["operator"]);
        self.signal = s(&modem["signal"]);
        self.error = None;
        if let Some(list) = sms.and_then(|v| v["messages"].as_array()) {
            self.messages = list
                .iter()
                .rev()
                .take(5)
                .map(|m| {
                    (
                        m["from"]
                            .as_str()
                            .or(m["to"].as_str())
                            .unwrap_or("?")
                            .to_string(),
                        m["text"].as_str().unwrap_or("").chars().take(160).collect(),
                    )
                })
                .collect();
        }
    }
}

pub struct Shell {
    pub page: Page,
    pub locked: bool,
    pub input: String,
    pub chat: Chat,
    pub strip: Strip,
    pub phone: PhoneInfo,
    pub cards: Vec<Card>,
    pub card_seq: u32,
    pub confirm: Option<Confirm>,
    pub prompt: Option<Prompt>,
    pub brightness: u8,
    pub clock: String,
    pub hour: u32,
    pub note: String,
}

impl Default for Shell {
    fn default() -> Self {
        Self {
            page: Page::Home,
            locked: false,
            input: String::new(),
            chat: Chat {
                lines: vec![Line {
                    who: Who::Agent,
                    text: "Your agent, right here. What should we work on?".into(),
                }],
                ..Chat::default()
            },
            strip: Strip::default(),
            phone: PhoneInfo::default(),
            cards: vec![],
            card_seq: 0,
            confirm: None,
            prompt: None,
            brightness: 50,
            clock: "--:--".into(),
            hour: 12,
            note: String::new(),
        }
    }
}

fn valid_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 64
        && id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

fn clamp_secs(arg: Option<&String>, default: f64, lo: f64, hi: f64) -> Duration {
    let n = arg
        .and_then(|a| a.parse::<f64>().ok())
        .filter(|n| *n > 0.0)
        .unwrap_or(default);
    Duration::from_secs_f64(n.clamp(lo, hi))
}

impl Shell {
    pub fn goto(&mut self, page: Page) -> Vec<Effect> {
        self.page = page;
        self.note.clear();
        match page {
            Page::Chat => vec![Effect::Keyboard(true)],
            _ => vec![Effect::Keyboard(false)],
        }
    }

    pub fn add_card(&mut self, title: &str, body: &str, ttl: Duration, now: Instant) -> u32 {
        self.card_seq += 1;
        let keep = self.cards.len().saturating_sub(MAX_CARDS - 1);
        self.cards.drain(..keep);
        self.cards.push(Card {
            id: self.card_seq,
            title: title.chars().take(80).collect(),
            body: body.chars().take(400).collect(),
            until: now + ttl,
        });
        self.card_seq
    }

    pub fn dismiss_card(&mut self, id: u32) {
        self.cards.retain(|c| c.id != id);
    }

    /// The owner tapped yes or no (or the question expired).
    pub fn answer_confirm(&mut self, answer: &'static str) -> Vec<Effect> {
        match self.confirm.take() {
            Some(c) => vec![Effect::WriteConfirm { id: c.id, answer }],
            None => vec![],
        }
    }

    /// Expire cards and questions. Called once a second while any are showing.
    pub fn tick(&mut self, now: Instant) -> Vec<Effect> {
        self.cards.retain(|c| c.until > now);
        if self.confirm.as_ref().is_some_and(|c| c.until <= now) {
            return self.answer_confirm("timeout");
        }
        if let Some(c) = self.confirm.as_mut() {
            c.remaining_s = c.until.saturating_duration_since(now).as_secs();
        }
        vec![]
    }

    pub fn needs_ticks(&self) -> bool {
        !self.cards.is_empty() || self.confirm.is_some()
    }

    /// Run one `shell.sock` call. Returns the result string `touch` IPC would have returned.
    pub fn call(&mut self, call: &Call, now: Instant) -> (String, Vec<Effect>) {
        let a = |i: usize| call.args.get(i).map(String::as_str).unwrap_or("");
        match call.function.as_str() {
            "home" => {
                if self.locked {
                    self.locked = false;
                    ("unlocked".into(), vec![Effect::Refresh])
                } else {
                    let e = self.goto(Page::Home);
                    ("ok".into(), e)
                }
            }
            "hide" => ("ok".into(), vec![]),
            "back" => {
                if self.locked || self.confirm.is_some() {
                    ("ignored".into(), vec![])
                } else if self.page != Page::Home {
                    let e = self.goto(Page::Home);
                    ("grid".into(), e)
                } else {
                    ("hidden".into(), vec![])
                }
            }
            "switcher" => {
                if self.locked {
                    ("locked".into(), vec![])
                } else {
                    (
                        "unsupported: the app switcher is not implemented in unum-shell".into(),
                        vec![],
                    )
                }
            }
            "open" => {
                if self.locked {
                    return ("locked".into(), vec![]);
                }
                match Page::from_app(a(0)) {
                    Some(p) => {
                        let e = self.goto(p);
                        ("ok".into(), e)
                    }
                    None if ["terminal", "agent", "switcher"].contains(&a(0)) => {
                        (format!("unsupported in unum-shell: {}", a(0)), vec![])
                    }
                    None => (
                        format!("unknown app: {} (known: {})", a(0), KNOWN_APPS.join(", ")),
                        vec![],
                    ),
                }
            }
            "card" => {
                let ttl = clamp_secs(call.args.get(2), 8.0, 3.0, 120.0);
                let id = self.add_card(a(0), a(1), ttl, now);
                (format!("card {id}"), vec![])
            }
            "confirm" => {
                if !valid_id(a(0)) {
                    return ("bad id".into(), vec![]);
                }
                if self.confirm.is_some() {
                    return ("busy".into(), vec![]);
                }
                let t = clamp_secs(call.args.get(2), 60.0, 5.0, 600.0);
                self.confirm = Some(Confirm {
                    id: a(0).to_string(),
                    question: a(1).chars().take(500).collect(),
                    until: now + t,
                    remaining_s: t.as_secs(),
                });
                ("asking".into(), vec![Effect::Keyboard(false)])
            }
            "confirmResult" => {
                let pending = self.confirm.as_ref().is_some_and(|c| c.id == a(0));
                (if pending { "pending" } else { "done" }.into(), vec![])
            }
            "lock" => {
                self.locked = true;
                self.page = Page::Home;
                (
                    "locked".into(),
                    vec![Effect::Keyboard(false), Effect::Refresh],
                )
            }
            "unlock" => {
                self.locked = false;
                ("unlocked".into(), vec![])
            }
            "keyboard" => {
                let on = !["off", "false", "0"].contains(&a(0));
                ("ok".into(), vec![Effect::Keyboard(on)])
            }
            "state" => (self.state_json().to_string(), vec![]),
            "perf" => ("unsupported: no perf overlay in unum-shell".into(), vec![]),
            other => (format!("unknown function: {other}"), vec![]),
        }
    }

    pub fn state_json(&self) -> Value {
        json!({
            "backend": "unum-shell",
            "home": !self.locked,
            "page": self.page.name(),
            "locked": self.locked,
            "cards": self.cards.len(),
            "confirm": self.confirm.as_ref().map(|c| c.id.clone()),
            "busy": self.chat.busy,
            "status": {
                "battery": self.strip.battery_label(),
                "thermal": self.strip.temp_label(),
                "model": self.strip.model,
                "healthy": self.strip.healthy,
                "s22d": self.strip.s22d,
            },
        })
    }

    /// An agent prompt arrived over the stream. It is a card plus buttons while it is pending.
    pub fn set_prompt(&mut self, p: Prompt) {
        self.prompt = Some(p);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn call(f: &str, args: &[&str]) -> Call {
        Call {
            function: f.into(),
            args: args.iter().map(|s| s.to_string()).collect(),
        }
    }

    fn run(s: &mut Shell, f: &str, args: &[&str]) -> (String, Vec<Effect>) {
        s.call(&call(f, args), Instant::now())
    }

    #[test]
    fn gestures_follow_the_quickshell_semantics() {
        let mut s = Shell::default();
        assert_eq!(run(&mut s, "open", &["settings"]).0, "ok");
        assert_eq!(s.page, Page::Settings);
        assert_eq!(run(&mut s, "back", &[]).0, "grid");
        assert_eq!(s.page, Page::Home);
        assert_eq!(run(&mut s, "back", &[]).0, "hidden");
        run(&mut s, "open", &["chat"]);
        assert_eq!(run(&mut s, "home", &[]).0, "ok");
        assert_eq!(s.page, Page::Home);
        assert!(run(&mut s, "switcher", &[]).0.starts_with("unsupported"));
    }

    #[test]
    fn lock_unlock_and_home_on_the_lock_cover() {
        let mut s = Shell::default();
        run(&mut s, "open", &["chat"]);
        assert_eq!(run(&mut s, "lock", &[]).0, "locked");
        assert!(s.locked);
        assert_eq!(s.page, Page::Home);
        assert_eq!(run(&mut s, "open", &["chat"]).0, "locked");
        assert_eq!(run(&mut s, "switcher", &[]).0, "locked");
        assert_eq!(run(&mut s, "back", &[]).0, "ignored");
        assert_eq!(
            run(&mut s, "home", &[]).0,
            "unlocked",
            "bottom-edge swipe up unlocks"
        );
        assert!(!s.locked);
        run(&mut s, "lock", &[]);
        assert_eq!(run(&mut s, "unlock", &[]).0, "unlocked");
    }

    #[test]
    fn open_reports_unknown_and_unsupported_apps() {
        let mut s = Shell::default();
        assert!(run(&mut s, "open", &["nope"])
            .0
            .starts_with("unknown app: nope (known: chat"));
        assert!(run(&mut s, "open", &["terminal"])
            .0
            .starts_with("unsupported in unum-shell"));
        assert_eq!(s.page, Page::Home);
    }

    #[test]
    fn chat_page_asks_for_the_keyboard_and_other_pages_hide_it() {
        let mut s = Shell::default();
        assert_eq!(
            run(&mut s, "open", &["chat"]).1,
            vec![Effect::Keyboard(true)]
        );
        assert_eq!(
            run(&mut s, "open", &["phone"]).1,
            vec![Effect::Keyboard(false)]
        );
        assert_eq!(
            run(&mut s, "keyboard", &["on"]).1,
            vec![Effect::Keyboard(true)]
        );
        assert_eq!(
            run(&mut s, "keyboard", &["off"]).1,
            vec![Effect::Keyboard(false)]
        );
    }

    #[test]
    fn cards_are_limited_clamped_and_expire() {
        let mut s = Shell::default();
        let t0 = Instant::now();
        for i in 0..5 {
            s.call(&call("card", &[&format!("t{i}"), "body", "8"]), t0);
        }
        assert_eq!(s.cards.len(), 3);
        assert_eq!(s.cards[0].title, "t2");
        assert_eq!(run(&mut s, "card", &["x", "y", "9999"]).0, "card 6");
        let long = "z".repeat(900);
        s.call(&call("card", &[&long, &long]), t0);
        assert_eq!(s.cards.last().unwrap().title.len(), 80);
        assert_eq!(s.cards.last().unwrap().body.len(), 400);
        assert!(s.needs_ticks());
        s.tick(t0 + Duration::from_secs(9));
        assert_eq!(s.cards.len(), 1, "the 120 s card (ttl clamped) is still up");
        s.tick(t0 + Duration::from_secs(200));
        assert!(s.cards.is_empty());
        assert!(!s.needs_ticks());
    }

    #[test]
    fn card_ttl_has_a_floor() {
        let mut s = Shell::default();
        let t0 = Instant::now();
        s.call(&call("card", &["a", "b", "0.1"]), t0);
        assert_eq!(s.cards[0].until - t0, Duration::from_secs(3));
    }

    #[test]
    fn confirm_protocol() {
        let mut s = Shell::default();
        assert_eq!(run(&mut s, "confirm", &["bad id!", "q", "60"]).0, "bad id");
        assert_eq!(run(&mut s, "confirm", &["", "q", "60"]).0, "bad id");
        assert_eq!(
            run(&mut s, "confirm", &["dab12", "Send SMS?", "60"]).0,
            "asking"
        );
        assert_eq!(run(&mut s, "confirm", &["other", "q2", "60"]).0, "busy");
        assert_eq!(run(&mut s, "confirmResult", &["dab12"]).0, "pending");
        assert_eq!(
            run(&mut s, "back", &[]).0,
            "ignored",
            "back must not dismiss a question"
        );
        assert_eq!(
            s.answer_confirm("yes"),
            vec![Effect::WriteConfirm {
                id: "dab12".into(),
                answer: "yes"
            }]
        );
        assert_eq!(run(&mut s, "confirmResult", &["dab12"]).0, "done");
        assert!(
            s.answer_confirm("no").is_empty(),
            "answering twice writes nothing"
        );
    }

    #[test]
    fn unanswered_confirm_times_out_with_a_timeout_answer() {
        let mut s = Shell::default();
        let t0 = Instant::now();
        s.call(&call("confirm", &["c1", "q", "5"]), t0);
        assert!(s.tick(t0 + Duration::from_secs(4)).is_empty());
        assert_eq!(
            s.tick(t0 + Duration::from_secs(6)),
            vec![Effect::WriteConfirm {
                id: "c1".into(),
                answer: "timeout"
            }]
        );
        assert!(s.confirm.is_none());
        // timeouts are clamped to 5..600 s
        s.call(&call("confirm", &["c2", "q", "1"]), t0);
        assert_eq!(
            s.confirm.as_ref().unwrap().until - t0,
            Duration::from_secs(5)
        );
        s.confirm = None;
        s.call(&call("confirm", &["c3", "q", "99999"]), t0);
        assert_eq!(
            s.confirm.as_ref().unwrap().until - t0,
            Duration::from_secs(600)
        );
    }

    #[test]
    fn confirm_countdown_follows_the_clock() {
        let mut s = Shell::default();
        let t0 = Instant::now();
        s.call(&call("confirm", &["c1", "q", "30"]), t0);
        assert_eq!(s.confirm.as_ref().unwrap().remaining_s, 30);
        s.tick(t0 + Duration::from_secs(12));
        assert_eq!(s.confirm.as_ref().unwrap().remaining_s, 18);
    }

    #[test]
    fn state_reports_the_page_lock_and_pending_question() {
        let mut s = Shell::default();
        run(&mut s, "open", &["phone"]);
        run(&mut s, "confirm", &["c9", "q", "30"]);
        let v: Value = serde_json::from_str(&run(&mut s, "state", &[]).0).unwrap();
        assert_eq!(v["backend"], "unum-shell");
        assert_eq!(v["page"], "phone");
        assert_eq!(v["confirm"], "c9");
        assert_eq!(v["locked"], false);
    }

    #[test]
    fn unknown_functions_are_reported_not_ignored() {
        let mut s = Shell::default();
        assert!(run(&mut s, "rm", &[]).0.starts_with("unknown function"));
        assert!(run(&mut s, "perf", &["5"]).0.starts_with("unsupported"));
    }

    #[test]
    fn call_parsing() {
        let c = Call::parse(r#"{"fn":"card","args":["T","B",8]}"#).unwrap();
        assert_eq!(
            c,
            Call {
                function: "card".into(),
                args: vec!["T".into(), "B".into(), "8".into()]
            }
        );
        assert_eq!(Call::parse(r#"{"fn":"home"}"#).unwrap().args.len(), 0);
        assert!(Call::parse("not json").is_err());
        assert!(Call::parse(r#"{"args":[]}"#).is_err());
        assert!(Call::parse(r#"{"fn":"x","args":"y"}"#).is_err());
    }

    #[test]
    fn strip_reads_s22d_status() {
        let mut st = Strip::default();
        st.apply_status(&json!({
            "battery": {"percent": 83, "charging": true},
            "thermal_max_c": 41.4,
            "network": {"wlan0": "up", "usb0": "down"},
            "modem": "ONLINE",
            "display": {"state": "on", "brightness": 128, "max_brightness": 255},
            "audio": {"level": 0, "muted": true},
        }));
        assert_eq!(st.line(), "83% charging  |  41 C  |  wifi");
        assert_eq!(st.brightness_pct, Some(50));
        assert_eq!(st.muted, Some(true));
        st.healthy = Some(true);
        assert_eq!(st.headline(), "OPENUNUM ONLINE");
        st.s22d_down();
        assert_eq!(st.headline(), "DEVICE DAEMON OFFLINE");
        assert_eq!(st.line(), "battery -  |  - C  |  no network info");
    }

    #[test]
    fn strip_survives_missing_fields() {
        let mut st = Strip::default();
        st.apply_status(&json!({}));
        assert_eq!(st.battery_label(), "battery -");
        assert_eq!(st.brightness_pct, None);
    }

    #[test]
    fn phone_info_from_phoned_json() {
        let mut p = PhoneInfo::default();
        p.apply(
            &json!({"modem_state": "ONLINE", "sim": "READY", "operator": null, "signal": 3}),
            Some(&json!({"messages": [{"from": "+30690", "text": "a"}, {"from": "+30691", "text": "b"}]})),
        );
        assert_eq!(p.sim.as_deref(), Some("READY"));
        assert_eq!(p.operator, None);
        assert_eq!(p.signal.as_deref(), Some("3"));
        assert_eq!(
            p.messages[0],
            ("+30691".to_string(), "b".to_string()),
            "newest first"
        );
    }

    // ---------------------------------------------------------- chat streaming

    fn delta(t: &str) -> ChatEvent {
        ChatEvent::Delta {
            token: t.into(),
            replace: false,
        }
    }

    #[test]
    fn streamed_text_builds_up_and_the_reply_replaces_it() {
        let mut c = Chat::default();
        c.start_turn("hello");
        assert!(c.busy);
        assert_eq!(c.activity.as_deref(), Some("thinking"));
        c.apply(delta("Hel"));
        c.apply(delta("lo there"));
        assert_eq!(c.live, "Hello there");
        assert_eq!(c.activity, None);
        c.finish(Some("Hello there, final.".into()));
        assert!(!c.busy);
        assert!(c.live.is_empty());
        assert_eq!(
            c.lines.last().unwrap(),
            &Line {
                who: Who::Agent,
                text: "Hello there, final.".into()
            }
        );
    }

    #[test]
    fn without_a_reply_the_streamed_text_is_kept() {
        let mut c = Chat::default();
        c.start_turn("q");
        c.apply(delta("only streamed"));
        c.finish(None);
        assert_eq!(c.lines.last().unwrap().text, "only streamed");
        c.finish(Some("late".into()));
        assert_eq!(c.lines.len(), 2, "a second finish is ignored");
    }

    #[test]
    fn tool_activity_and_failures_are_shown() {
        let mut c = Chat::default();
        c.start_turn("q");
        c.apply(ChatEvent::ToolStarted("wifi_status".into()));
        assert_eq!(c.activity.as_deref(), Some("using wifi_status"));
        c.apply(ChatEvent::ToolFinished {
            name: "wifi_status".into(),
            ok: true,
        });
        assert_eq!(
            c.lines.len(),
            1,
            "only the question: a tool that worked adds no line"
        );
        c.apply(ChatEvent::ToolFinished {
            name: "camera_capture".into(),
            ok: false,
        });
        assert_eq!(
            c.lines.last().unwrap(),
            &Line {
                who: Who::Tool,
                text: "camera_capture failed".into()
            }
        );
    }

    #[test]
    fn plan_replies_replace_the_live_text() {
        let mut c = Chat::default();
        c.start_turn("q");
        c.apply(delta("draft"));
        c.apply(ChatEvent::Delta {
            token: "whole final reply".into(),
            replace: true,
        });
        assert_eq!(c.live, "whole final reply");
    }

    #[test]
    fn snapshots_finish_a_turn_only_when_done() {
        let mut c = Chat::default();
        c.start_turn("q");
        c.apply(ChatEvent::Snapshot {
            done: false,
            reply: None,
            live: Some("partial".into()),
        });
        assert!(c.busy);
        assert_eq!(c.live, "partial");
        c.apply(ChatEvent::Snapshot {
            done: true,
            reply: Some("done!".into()),
            live: None,
        });
        assert!(!c.busy);
        assert_eq!(c.lines.last().unwrap().text, "done!");
    }

    #[test]
    fn agent_prompts_are_returned_not_swallowed() {
        let mut c = Chat::default();
        c.start_turn("q");
        let p = Prompt {
            id: "ask-1".into(),
            header: "Aspect".into(),
            question: "Which?".into(),
            options: vec!["16:9".into()],
        };
        assert_eq!(c.apply(ChatEvent::Prompt(p.clone())), Some(p));
    }

    #[test]
    fn failure_ends_the_turn_with_a_system_line() {
        let mut c = Chat::default();
        c.start_turn("q");
        c.apply(delta("x"));
        c.fail("connection refused");
        assert!(!c.busy);
        assert!(c.live.is_empty());
        assert_eq!(c.lines.last().unwrap().who, Who::System);
    }

    #[test]
    fn history_is_bounded() {
        let mut c = Chat::default();
        for i in 0..200 {
            c.push(Who::You, format!("{i}"));
        }
        assert_eq!(c.lines.len(), 60);
        assert_eq!(c.lines.last().unwrap().text, "199");
    }
}
