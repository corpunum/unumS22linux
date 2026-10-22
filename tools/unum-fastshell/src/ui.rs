//! The shell: state, layout, drawing, touch handling and the touchd call
//! protocol. Drawing is immediate-mode: `draw` repaints the whole scene but
//! every primitive is clipped to the current damage rectangle, so a frame
//! costs roughly the damaged area. `layout` runs the same code with an empty
//! clip to collect the hit regions.
use crate::config;
use crate::ctl::{write_confirm, Call};
use crate::font::{Face, Fonts};
use crate::gfx::{Canvas, Rect};
use crate::idle;
use crate::kbd::{self, Kbd, KeyOut};
use crate::net::{self, ChatEvent, Msg, Net, Prompt};
use serde_json::{json, Value};
use std::time::Instant;

/// Focus a running foot under sway, else start one.
/// squeekboard is started once by the sway config; it has been seen gone after
/// seat switches, so start it again when it is missing.
const FOOT: &str = "pidof squeekboard >/dev/null || swaymsg exec squeekboard >/dev/null; swaymsg '[app_id=\"foot\"] focus' >/dev/null 2>&1 || swaymsg exec foot";
pub const W: i32 = 1080;
pub const H: i32 = 2340;
pub const STATUS_H: i32 = 96;
const EDGE_B: i32 = 82; // 3.5 % of the height, as s22-touchd
const EDGE_S: i32 = 38; // 3.5 % of the width
const EDGE_T: i32 = 70; // 3 %
const SLOP: i32 = 26;
const KB_H: i32 = 640;
const M: i32 = 32;

pub const BG: u32 = 0x0b0f14;
const BAR: u32 = 0x0b0f14;
const SURF: u32 = 0x161c24;
const SURF2: u32 = 0x1e2630;
const PRESS: u32 = 0x2c3a4e;
const ACCENT: u32 = 0x3b82f6;
const ACCENT_P: u32 = 0x1d5fd0;
const TEXT: u32 = 0xe8edf3;
const DIM: u32 = 0x8d99a8;
const GOOD: u32 = 0x3fb950;
const WARN: u32 = 0xd29922;
const BAD: u32 = 0xf85149;
const USER_B: u32 = 0x1f4b86;
const AGENT_B: u32 = 0x1a2129;
const KEY: u32 = 0x2a323d;
const KEY_SP: u32 = 0x1c232c;
const KB_BG: u32 = 0x10151b;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Page {
    Home,
    Chat,
    Phone,
    Camera,
    Files,
    Settings,
    Switcher,
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
            Page::Switcher => "switcher",
        }
    }
    fn idx(self) -> usize {
        self as usize
    }
    pub fn from_app(a: &str) -> Option<Page> {
        Some(match a {
            "chat" | "agent" => Page::Chat,
            "phone" => Page::Phone,
            "camera" => Page::Camera,
            "files" => Page::Files,
            "settings" => Page::Settings,
            "switcher" | "apps" => Page::Switcher,
            "home" => Page::Home,
            _ => return None,
        })
    }
}

#[derive(Clone, Debug, PartialEq)]
pub enum Act {
    Open(Page),
    Back,
    Lock,
    Voice,
    Terminal,
    Sway,
    Send,
    Stop,
    Field,
    Key(usize, usize),
    ConfirmYes,
    ConfirmNo,
    Card(u32),
    PromptOpt(usize),
    FilesUp,
    File(usize),
    Bright,
    Model(&'static str),
    Classic,
    ScreenOff,
    IdleCycle(bool),
    ButtonsToggle,
    Capture,
    CamSensor(&'static str),
    Refresh,
    Nothing,
}

#[derive(Clone, Debug)]
pub struct Hit {
    pub r: Rect,
    pub act: Act,
    pub hold_ms: u32,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Who {
    You,
    Agent,
    Note,
}

pub struct Card {
    pub id: u32,
    pub title: String,
    pub body: String,
    pub until: Instant,
}

pub struct Confirm {
    pub id: String,
    pub question: String,
    pub until: Instant,
}

/// What the main loop must do outside the UI state.
#[derive(Debug, Clone, PartialEq)]
pub enum Effect {
    /// Show sway (foreign Wayland windows) and give it the panel.
    HandToSway,
    /// Take the panel back from sway.
    Activate,
    Display(bool),
    Spawn(Vec<String>),
    /// Run a native-root command detached (s22-display when sway shows).
    Native(Vec<String>),
    /// Run the voice assistant (`cmd`) with this environment; its last reply becomes a card.
    Assistant(String, Vec<(String, String)>),
    /// Push-to-talk released: tell the running assistant (held ms).
    PttRelease(u32),
    /// Speaker level changed (store it; set the amps if something is playing).
    Level(i32),
    /// Save the screen to the gallery (after the next frame), then flash.
    Screenshot,
}

/// The on-screen level / notice overlay (volume keys, keep-awake).
pub struct Osd {
    pub title: String,
    pub level: Option<i32>,
    pub until: Instant,
}

#[derive(Default, Clone)]
pub struct Status {
    pub model: Option<String>,
    pub health: Option<String>,
    pub sessions: Option<(usize, usize)>,
    pub running: Option<usize>,
    pub battery: Option<u32>,
    pub bat_state: String,
    pub bat_temp: Option<f32>,
    pub thermal: Option<f32>,
    pub thermal_detail: String,
    pub net: String,
    pub device: Option<Value>,
    pub radio: Option<&'static str>,
}

struct Touching {
    x0: i32,
    y0: i32,
    t0: u64,
    x: i32,
    y: i32,
    edge: Option<char>,
    dragging: bool,
    samples: Vec<(u64, i32)>,
    hit: Option<Hit>,
    in_scroll: bool,
}

#[derive(Clone)]
struct Block {
    who: Who,
    y: i32,
    h: i32,
    w: i32,
    lines: Vec<String>,
}

pub struct FileEntry {
    pub name: String,
    pub dir: bool,
    pub size: u64,
}

pub enum Viewer {
    Text(String, Vec<String>),
    Image(String, Vec<u32>, i32, i32),
}

pub struct Stats {
    pub lat: Vec<(&'static str, f64)>,
    pub render: Vec<f64>,
    pub copy: Vec<f64>,
    pub frames: u64,
}

pub struct App {
    pub fonts: Fonts,
    pub net: Option<Net>,
    pub page: Page,
    pub locked: bool,
    pub active: bool,
    pub display_on: bool,
    pub hits: Vec<Hit>,
    layout_pass: bool,
    pub pressed: Option<Hit>,
    touch: Option<Touching>,
    pub st: Status,
    pub chat: Vec<(Who, String)>,
    pub live: String,
    pub activity: Option<String>,
    pub busy: bool,
    /// The stream has shown evidence of the turn we sent (the first snapshot of a
    /// stream describes the session's previous, completed turn).
    turn_seen: bool,
    events_n: [u32; 3],
    turn_t: (Option<Instant>, Option<Instant>, u32),
    finished_turn: u64,
    turn: u64,
    blocks: Vec<Block>,
    blocks_key: (usize, usize, usize, i32),
    pub input: String,
    pub kb: Kbd,
    pub cards: Vec<Card>,
    card_seq: u32,
    pub confirm: Option<Confirm>,
    pub prompt: Option<Prompt>,
    pub files_dir: String,
    pub files: Vec<FileEntry>,
    pub viewer: Option<Viewer>,
    pub phone: Option<(Value, Value, Value)>,
    pub camera_state: String,
    pub camera_img: Option<(Vec<u32>, i32, i32)>,
    pub camera_poll: Option<Instant>,
    pub camera_sensor: &'static str,
    pub brightness: (i32, i32),
    bright_sent: Instant,
    pub note: String,
    scroll: [f32; 7],
    content_h: [i32; 7],
    pub scroll_rect: Option<Rect>,
    rendered_scroll: Option<(Page, bool, i32)>,
    velocity: f32,
    pub animating: bool,
    pub damage: Vec<Rect>,
    pub relayout: bool,
    pub effects: Vec<Effect>,
    pub mark: Option<(u64, &'static str)>,
    pub stats: Stats,
    pub clock: String,
    pub date: String,
    pub run_dir: String,
    pub chroot: String,
    pub repeat_at: Option<(Instant, usize, usize)>,
    pub hold: Option<(Instant, Hit)>,
    stick_bottom: bool,
    dummy: Canvas,
    pub idle_cfg: idle::Cfg,
    pub idle_path: String,
    pub last_touch: Instant,
    voice_running: bool,
    // ---- physical buttons (buttons.rs)
    /// Speaker level 0-10 (s22-say's level).
    pub level: i32,
    level_before: i32,
    muted_from: Option<i32>,
    pub osd: Option<Osd>,
    pub flash_until: Option<Instant>,
    /// Power + volume up: the screen timeout is suspended.
    pub keep_awake: bool,
    /// Settings toggle: the shell handles the keys (else s22-buttons does).
    pub buttons_enabled: bool,
    pub buttons_cfg: String,
    /// Latencies measured outside a page flip (modeset after a wake), for latency.jsonl.
    pub lat_unlogged: Vec<(&'static str, f64)>,
}

pub const GALLERY: &str = "/root/.openunum/assets/generated";
const PTT_HOOK: &str = "/srv/s22/buttons/hooks/assistant";

fn now_us() -> u64 {
    crate::drm::now_us()
}

fn pct(v: &[f64], q: f64) -> f64 {
    if v.is_empty() {
        return 0.0;
    }
    let mut s = v.to_vec();
    s.sort_by(|a, b| a.partial_cmp(b).unwrap());
    s[((s.len() - 1) as f64 * q).round() as usize]
}

impl App {
    pub fn new(net: Option<Net>, run_dir: &str, chroot: &str) -> App {
        let mut a = App {
            fonts: Fonts::new(),
            net,
            page: Page::Home,
            locked: false,
            active: false,
            display_on: true,
            hits: vec![],
            layout_pass: false,
            pressed: None,
            touch: None,
            st: Status::default(),
            chat: vec![(Who::Agent, "Your agent, right here. What should we work on?".into())],
            live: String::new(),
            activity: None,
            busy: false,
            turn_seen: false,
            events_n: [0; 3],
            turn_t: (None, None, 0),
            finished_turn: 0,
            turn: 0,
            blocks: vec![],
            blocks_key: (usize::MAX, 0, 0, 0),
            input: String::new(),
            kb: Kbd::default(),
            cards: vec![],
            card_seq: 0,
            confirm: None,
            prompt: None,
            files_dir: config::get().files_dir.clone(),
            files: vec![],
            viewer: None,
            phone: None,
            camera_state: String::new(),
            camera_img: None,
            camera_poll: None,
            camera_sensor: "rear",
            brightness: (128, 255),
            bright_sent: Instant::now(),
            note: String::new(),
            scroll: [0.0; 7],
            content_h: [0; 7],
            scroll_rect: None,
            rendered_scroll: None,
            velocity: 0.0,
            animating: false,
            damage: vec![Rect::new(0, 0, W, H)],
            relayout: true,
            effects: vec![],
            mark: None,
            stats: Stats { lat: vec![], render: vec![], copy: vec![], frames: 0 },
            clock: "--:--".into(),
            date: String::new(),
            run_dir: run_dir.into(),
            chroot: chroot.into(),
            repeat_at: None,
            hold: None,
            stick_bottom: true,
            dummy: Canvas::new(1, 1),
            idle_cfg: idle::Cfg::default(),
            idle_path: String::new(),
            last_touch: Instant::now(),
            voice_running: false,
            level: crate::buttons::DEFAULT_LEVEL,
            level_before: crate::buttons::DEFAULT_LEVEL,
            muted_from: None,
            osd: None,
            flash_until: None,
            keep_awake: false,
            buttons_enabled: true,
            buttons_cfg: String::new(),
            lat_unlogged: vec![],
        };
        a.dummy.clip = Rect::new(0, 0, 0, 0);
        a.read_sys();
        a
    }

    // ------------------------------------------------------------ damage

    pub fn dirty(&mut self, r: Rect) {
        if r.w > 0 && r.h > 0 {
            self.damage.push(r);
        }
    }
    pub fn dirty_all(&mut self) {
        self.damage.clear();
        self.damage.push(Rect::new(0, 0, W, H));
        self.relayout = true;
    }
    fn content_rect(&self) -> Rect {
        Rect::new(0, STATUS_H, W, H - STATUS_H)
    }
    fn dirty_content(&mut self) {
        let r = self.content_rect();
        self.dirty(r);
        self.relayout = true;
    }

    // ------------------------------------------------------------ status

    pub fn read_sys(&mut self) {
        let rd = |p: &str| std::fs::read_to_string(p).map(|s| s.trim().to_string()).unwrap_or_default();
        let (cfg, sys) = (config::get(), config::sys());
        let b = sys.battery.as_ref().map(|p| p.display().to_string()).unwrap_or_default();
        self.st.battery = rd(&format!("{b}/capacity")).parse().ok();
        self.st.bat_state = rd(&format!("{b}/status"));
        self.st.bat_temp = rd(&format!("{b}/temp")).parse::<f32>().ok().map(|t| t / 10.0);
        let mut mx = 0.0f32;
        let mut parts = vec![];
        for (ty, temp) in &sys.thermal {
            if let Ok(t) = rd(&temp.display().to_string()).parse::<f32>() {
                let c = t / 1000.0;
                if c > 0.0 {
                    mx = mx.max(c);
                    parts.push(format!("{ty} {c:.0}"));
                }
            }
        }
        self.st.thermal = (mx > 0.0).then_some(mx);
        self.st.thermal_detail = parts.join(" · ");
        let up = |n: &str| matches!(rd(&format!("/sys/class/net/{n}/operstate")).as_str(), "up" | "unknown");
        let any_up = |v: &[String]| v.iter().any(|n| up(n));
        let link = if any_up(&cfg.net_wifi) { "wifi" } else if any_up(&cfg.net_usb) { "usb" } else if any_up(&cfg.net_cell) { "cell" } else { "" };
        let ts = !cfg.net_vpn.is_empty() && std::path::Path::new(&format!("/sys/class/net/{}", cfg.net_vpn)).exists();
        self.st.net = match (link, ts) {
            ("", true) => "ts".into(),
            ("", false) => "offline".into(),
            (l, true) => format!("{l} · ts"),
            (l, false) => l.into(),
        };
        let bl = sys.backlight.as_ref().map(|p| p.display().to_string()).unwrap_or_default();
        if let (Ok(b), Ok(m)) = (rd(&format!("{bl}/brightness")).parse::<i32>(), rd(&format!("{bl}/max_brightness")).parse::<i32>()) {
            self.brightness = (b, m.max(1));
        }
        self.update_clock();
        if self.page == Page::Home || self.locked {
            self.dirty_content();
        }
        self.dirty(Rect::new(0, 0, W, STATUS_H));
    }

    pub fn update_clock(&mut self) {
        let t = unsafe { libc::time(std::ptr::null_mut()) };
        let mut tm: libc::tm = unsafe { std::mem::zeroed() };
        unsafe { libc::localtime_r(&t, &mut tm) };
        let c = format!("{:02}:{:02}", tm.tm_hour, tm.tm_min);
        const DAYS: [&str; 7] = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
        const MON: [&str; 12] = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
        let d = format!("{} {} {}", DAYS[tm.tm_wday.clamp(0, 6) as usize], tm.tm_mday, MON[tm.tm_mon.clamp(0, 11) as usize]);
        if c != self.clock {
            self.clock = c;
            self.date = d;
            self.dirty(Rect::new(0, 0, 300, STATUS_H));
            if self.locked {
                self.dirty_content();
            }
        }
    }

    // ------------------------------------------------------------ messages

    pub fn on_msg(&mut self, m: Msg) {
        match m {
            Msg::Agent { model, health, sessions, running } => {
                if model.is_some() {
                    self.st.model = model;
                }
                self.st.health = health;
                if sessions.is_some() {
                    self.st.sessions = sessions;
                }
                if running.is_some() {
                    self.st.running = running;
                }
                if matches!(self.page, Page::Home | Page::Chat | Page::Settings) || self.locked {
                    self.dirty_content();
                }
                self.dirty(Rect::new(0, 0, W, STATUS_H));
            }
            Msg::Device(v) => {
                self.st.device = Some(v);
                if self.page == Page::Settings {
                    self.dirty_content();
                }
            }
            Msg::History(lines) => {
                if !self.busy && self.chat.len() <= 1 && !lines.is_empty() {
                    self.chat = lines.into_iter().map(|(you, t)| (if you { Who::You } else { Who::Agent }, t)).collect();
                    self.stick_bottom = true;
                    self.chat_changed();
                }
            }
            Msg::Chat(id, ev) if id == self.turn => self.chat_event(ev),
            Msg::ChatReply(id, Ok(Some(reply))) if id == self.finished_turn && id != 0 => {
                // the POST's reply is authoritative: replace a streamed/snapshot ending
                if let Some(last) = self.chat.last_mut().filter(|l| l.0 == Who::Agent) {
                    if last.1 != reply.trim() {
                        last.1 = reply.trim().to_string();
                        self.chat_changed();
                    }
                }
            }
            Msg::ChatReply(id, r) if id == self.turn => match r {
                Ok(Some(reply)) => self.finish(Some(reply)),
                Ok(None) => {}
                Err(e) => self.fail(&e),
            },
            Msg::ChatStreamClosed(id, err) if id == self.turn => {
                if let Some(e) = err {
                    if self.busy && self.live.is_empty() {
                        self.activity = Some(format!("waiting for the reply ({e})"));
                        self.chat_changed();
                    }
                }
            }
            Msg::Phone(a, b, c) => {
                self.phone = Some((a, b, c));
                if self.page == Page::Phone {
                    self.dirty_content();
                }
            }
            Msg::Touchd(tag, v) => self.on_touchd(tag, v),
            Msg::Radio(v) => {
                let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
                let b = net::radio_badge(&v, now);
                if b != self.st.radio {
                    self.st.radio = b;
                    self.dirty(Rect::new(0, 0, W, STATUS_H));
                }
            }
            Msg::Assistant(v) => {
                self.voice_running = false;
                self.last_touch = Instant::now();
                let you = v["transcript"].as_str().or(v["text"].as_str()).unwrap_or("").to_string();
                let ans = v["reply"].as_str().or(v["answer"].as_str()).unwrap_or("").to_string();
                if !you.is_empty() || !ans.is_empty() {
                    self.add_card(&format!("Voice: {}", if you.is_empty() { "(no speech)" } else { &you }), &ans, 20.0);
                } else {
                    self.add_card("Voice", "Finished (see assistant.jsonl)", 6.0);
                }
            }
            _ => {}
        }
    }

    fn on_touchd(&mut self, tag: &str, v: Value) {
        match tag {
            "camera" => {
                if v["ok"] == true {
                    self.camera_state = format!("{} camera: metering, then a burst…", v["sensor"].as_str().unwrap_or("rear"));
                    self.camera_poll = Some(Instant::now());
                } else {
                    self.camera_state = format!("camera: {}", v["error"].as_str().unwrap_or("failed"));
                }
            }
            "model" => {
                self.note = if v["ok"] == true {
                    format!("Model: {}", net::model_label(v["current"]["model"].as_str().unwrap_or("?")))
                } else {
                    format!("Model switch failed: {}", v["error"].as_str().unwrap_or("?"))
                };
                if let Some(n) = &self.net {
                    net::refresh_agent(n);
                }
            }
            "brightness" => {
                if let (Some(b), Some(m)) = (v["brightness"].as_i64(), v["max"].as_i64()) {
                    self.brightness = (b as i32, m.max(1) as i32);
                }
            }
            _ => {}
        }
        if matches!(self.page, Page::Camera | Page::Settings) {
            self.dirty_content();
        }
    }

    pub fn poll_camera(&mut self) {
        let Some(t) = self.camera_poll else { return };
        let p = format!("{}/camera.json", self.run_dir);
        let v: Value = std::fs::read_to_string(&p).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or(Value::Null);
        match v["state"].as_str() {
            Some("done") => {
                self.camera_poll = None;
                if v["rc"] == 0 {
                    self.camera_state = camera_summary(&v);
                    self.load_camera_image();
                } else {
                    self.camera_state = format!("capture failed (rc {})", v["rc"]);
                }
            }
            Some("failed") => {
                self.camera_poll = None;
                self.camera_state = format!("capture failed: {}", v["error"].as_str().unwrap_or("?"));
            }
            _ => {
                if t.elapsed().as_secs() > 200 {
                    self.camera_poll = None;
                    self.camera_state = "capture timed out".into();
                }
            }
        }
        if self.page == Page::Camera {
            self.dirty_content();
        }
    }

    pub fn load_camera_image(&mut self) {
        let p = format!("{}/camera-latest.png", self.run_dir);
        self.camera_img = load_png(&p, 1016, 1400);
    }

    // ------------------------------------------------------------ chat

    fn chat_changed(&mut self) {
        if self.page == Page::Chat {
            self.dirty_content();
        }
    }

    fn chat_event(&mut self, ev: ChatEvent) {
        match &ev {
            ChatEvent::Snapshot { pending, .. } => {
                if *pending {
                    self.turn_seen = true;
                }
            }
            _ => self.turn_seen = true,
        }
        let k = match &ev { ChatEvent::Delta { .. } => 0, ChatEvent::Snapshot { .. } => 1, _ => 2 };
        self.events_n[k] += 1;
        if k == 0 {
            if self.turn_t.1.is_none() {
                self.turn_t.1 = Some(Instant::now());
            }
            if self.page == Page::Chat && self.active {
                self.turn_t.2 += 1;
            }
        }
        match ev {
            ChatEvent::Delta { token, replace } => {
                if replace {
                    self.live = token;
                } else {
                    self.live.push_str(&token);
                }
                self.activity = None;
            }
            ChatEvent::Thinking => self.activity = Some("thinking…".into()),
            ChatEvent::ToolStarted(n) => self.activity = Some(format!("using {n}")),
            ChatEvent::ToolFinished { name, ok } => {
                if !ok {
                    self.chat.push((Who::Note, format!("{name} failed")));
                }
                self.activity = Some("thinking…".into());
            }
            ChatEvent::Prompt(p) => {
                if self.page != Page::Chat {
                    self.add_card("The agent has a question", &p.question, 30.0);
                }
                self.prompt = Some(p);
            }
            ChatEvent::Snapshot { done, reply, live, .. } => {
                if !self.turn_seen {
                    return;
                }
                // snapshots carry the whole answer so far: authoritative after a reconnect
                if let Some(l) = live.filter(|l| !l.is_empty()) {
                    self.live = l;
                }
                if done {
                    self.finish(reply);
                }
            }
            ChatEvent::TurnEnd { reply } => self.finish(reply),
        }
        self.chat_changed();
    }

    fn finish(&mut self, reply: Option<String>) {
        if !self.busy {
            return;
        }
        let ms = |t: Option<Instant>| t.map(|t| t.elapsed().as_millis() as i64).unwrap_or(-1);
        eprintln!(
            "fastshell: turn {} finished: {} content deltas ({} while the chat page was on screen), {} snapshots, {} other events, live {} chars; sent {} ms ago, first delta {} ms ago",
            self.turn, self.events_n[0], self.turn_t.2, self.events_n[1], self.events_n[2], self.live.chars().count(), ms(self.turn_t.0), ms(self.turn_t.1)
        );
        self.turn_t = (None, None, 0);
        self.events_n = [0; 3];
        let text = reply.filter(|r| !r.trim().is_empty()).unwrap_or_else(|| std::mem::take(&mut self.live));
        self.live.clear();
        if !text.trim().is_empty() {
            self.chat.push((Who::Agent, text.trim().to_string()));
        }
        self.activity = None;
        self.busy = false;
        self.finished_turn = self.turn;
        self.turn += 1; // stop the stream thread
        if let Some(n) = &self.net {
            n.turn.store(self.turn, std::sync::atomic::Ordering::SeqCst);
        }
        self.chat_changed();
    }

    fn fail(&mut self, e: &str) {
        self.chat.push((Who::Note, format!("Message failed: {e}")));
        self.live.clear();
        self.activity = None;
        self.busy = false;
        self.chat_changed();
    }

    pub fn send(&mut self) {
        let t = self.input.trim().to_string();
        if t.is_empty() || self.busy {
            return;
        }
        self.input.clear();
        self.chat.push((Who::You, t.clone()));
        self.live.clear();
        self.activity = Some("thinking…".into());
        self.busy = true;
        self.turn_seen = false;
        self.turn_t = (Some(Instant::now()), None, 0);
        self.stick_bottom = true;
        self.turn += 1;
        if let Some(n) = &self.net {
            net::chat_turn(n, self.turn, t);
        }
        self.chat_changed();
    }

    // ------------------------------------------------------------ cards & calls

    pub fn add_card(&mut self, title: &str, body: &str, ttl: f64) -> u32 {
        self.card_seq += 1;
        while self.cards.len() >= 3 {
            self.cards.remove(0);
        }
        self.cards.push(Card {
            id: self.card_seq,
            title: title.chars().take(80).collect(),
            body: body.chars().take(400).collect(),
            until: Instant::now() + std::time::Duration::from_secs_f64(ttl.clamp(3.0, 120.0)),
        });
        self.dirty_content();
        self.card_seq
    }

    fn answer_confirm(&mut self, answer: &str) {
        if let Some(c) = self.confirm.take() {
            let _ = write_confirm(&format!("{}/confirm", self.run_dir), &c.id, answer);
            self.dirty_all();
        }
    }

    /// Why the screen must stay on, if it must (other than s22-say's lock, which main checks).
    pub fn idle_inhibitor(&self) -> Option<&'static str> {
        if self.busy {
            Some("chat streaming")
        } else if self.confirm.is_some() || self.prompt.is_some() {
            Some("agent question")
        } else if self.page == Page::Camera || self.camera_poll.is_some() {
            Some("camera")
        } else if self.voice_running {
            Some("voice turn")
        } else if self.keep_awake {
            Some("keep screen on (Power + Volume up)")
        } else {
            None
        }
    }

    /// Once a second while the panel is lit. True when it is time to power off.
    /// An inhibited screen restarts its countdown, so the full timeout runs after the reason ends.
    pub fn idle_check(&mut self, now: Instant, say_locked: bool) -> bool {
        if self.idle_inhibitor().is_some() || say_locked {
            self.last_touch = now;
            return false;
        }
        idle::due(&self.idle_cfg, now.saturating_duration_since(self.last_touch), self.locked, false)
    }

    /// The Power-press path: lock, hide the keyboard, panel off.
    pub fn screen_timeout_off(&mut self) {
        self.locked = true;
        self.kb.visible = false;
        self.dirty_all();
        self.effects.push(Effect::Display(false));
    }

    // ------------------------------------------------------------ physical buttons

    /// Apply what the button recogniser decided (buttons::Machine). `t_in` is the
    /// key event's CLOCK_MONOTONIC µs, for the key -> flip latency log.
    pub fn button_actions(&mut self, acts: &[crate::buttons::Action], t_in: u64) {
        use crate::buttons::{Action, LEVELS};
        if acts.is_empty() {
            return;
        }
        self.last_touch = Instant::now();
        for a in acts {
            match *a {
                Action::Wake => {
                    if self.active {
                        self.effects.push(Effect::Display(true));
                    } else {
                        self.effects.push(Effect::Native(vec!["/srv/s22/hardware/bin/s22-display".into(), "on".into()]));
                    }
                }
                Action::Lock => {
                    if self.active {
                        self.locked = true;
                        self.kb.visible = false;
                        self.osd = None;
                        self.dirty_all();
                        self.effects.push(Effect::Display(false));
                    } else {
                        self.effects.push(Effect::Native(vec!["/srv/s22/hardware/bin/s22-display".into(), "off".into()]));
                    }
                }
                Action::Step(d) => {
                    self.level_before = self.level;
                    self.level = (self.level + d).clamp(0, LEVELS);
                    if self.level > 0 {
                        self.muted_from = None;
                    }
                    self.effects.push(Effect::Level(self.level));
                    self.show_level();
                }
                Action::Revert => {
                    self.level = self.level_before;
                    self.effects.push(Effect::Level(self.level));
                    self.hide_osd();
                }
                Action::Mute => {
                    if self.level == 0 {
                        self.level = self.muted_from.take().unwrap_or(crate::buttons::DEFAULT_LEVEL).max(1);
                    } else {
                        self.muted_from = Some(self.level);
                        self.level = 0;
                    }
                    self.level_before = self.level;
                    self.effects.push(Effect::Level(self.level));
                    self.show_level();
                }
                Action::PttStart(held) => {
                    self.voice_running = true;
                    self.effects.push(Effect::Assistant(
                        PTT_HOOK.into(),
                        vec![
                            ("S22_BUTTON".into(), "volup".into()),
                            ("S22_HELD_MS".into(), held.to_string()),
                            ("S22_PTT".into(), "hold".into()),
                            ("S22_PTT_STOP".into(), format!("{}/ptt-release", crate::buttons::BUTTONS_BASE)),
                        ],
                    ));
                    self.add_card("Listening…", "Speak while you hold Volume up; release to send.", 12.0);
                }
                Action::PttRelease(held) => self.effects.push(Effect::PttRelease(held)),
                Action::Assistant(held) => {
                    self.voice_running = true;
                    self.effects.push(Effect::Assistant(PTT_HOOK.into(), vec![("S22_BUTTON".into(), "volup".into()), ("S22_HELD_MS".into(), held.to_string())]));
                    self.add_card("Listening…", "Speak now; the answer is spoken and shown here.", 12.0);
                }
                Action::Camera => {
                    if !self.active {
                        self.effects.push(Effect::Activate);
                    }
                    self.locked = false;
                    self.goto(Page::Camera);
                    self.effects.push(Effect::Display(true));
                }
                Action::Screenshot => {
                    self.hide_osd();
                    self.effects.push(Effect::Screenshot);
                }
                Action::KeepAwake => {
                    self.keep_awake = !self.keep_awake;
                    let t = if self.keep_awake { "Screen stays on" } else { "Screen timeout on" };
                    self.show_osd(t, None, 1500);
                }
            }
        }
        if !self.damage.is_empty() || acts.contains(&Action::Wake) {
            self.mark = Some((t_in, "button"));
        }
    }

    fn osd_rect() -> Rect {
        Rect::new(M, STATUS_H + 24, W - 2 * M, 170)
    }

    fn show_osd(&mut self, title: &str, level: Option<i32>, ms: u64) {
        self.osd = Some(Osd { title: title.into(), level, until: Instant::now() + std::time::Duration::from_millis(ms) });
        self.dirty(Self::osd_rect());
    }

    fn show_level(&mut self) {
        let t = if self.level == 0 { "Muted".to_string() } else { format!("Volume {}", self.level) };
        self.show_osd(&t, Some(self.level), 1500);
    }

    fn hide_osd(&mut self) {
        if self.osd.take().is_some() {
            self.dirty(Self::osd_rect());
        }
    }

    /// The screenshot was taken (the flash shows now); `name` is the gallery file.
    pub fn screenshot_taken(&mut self, name: &str) {
        self.flash_until = Some(Instant::now() + std::time::Duration::from_millis(140));
        self.dirty(Rect::new(0, 0, W, H));
        self.add_card("Screenshot saved", &format!("Gallery and Files: {GALLERY}/{name}"), 5.0);
        self.files_dir = GALLERY.into();
        self.files.clear();
    }

    /// When an overlay must change next (OSD timeout, end of the flash).
    pub fn overlay_deadline(&self) -> Option<Instant> {
        [self.osd.as_ref().map(|o| o.until), self.flash_until].into_iter().flatten().min()
    }

    pub fn expire_overlays(&mut self, now: Instant) {
        if self.osd.as_ref().is_some_and(|o| o.until <= now) {
            self.hide_osd();
        }
        if self.flash_until.is_some_and(|t| t <= now) {
            self.flash_until = None;
            self.dirty_all();
        }
    }

    fn draw_osd(&mut self, cv: &mut Canvas) {
        let Some(o) = self.osd.as_ref() else { return };
        let r = Self::osd_rect();
        if self.layout_pass || !r.hits(&cv.clip) {
            return;
        }
        let (title, level) = (o.title.clone(), o.level);
        cv.rrect(&Rect::new(r.x - 2, r.y - 2, r.w + 4, r.h + 4), 34, 0x3b4656);
        cv.rrect(&r, 32, 0x1b2533);
        match level {
            Some(l) => {
                self.txt(cv, Face::Bold, 40, r.x + 36, r.y + 62, &title, TEXT);
                let n = crate::buttons::LEVELS;
                let gap = 10;
                let sw = (r.w - 72 - gap * (n - 1)) / n;
                for i in 0..n {
                    let seg = Rect::new(r.x + 36 + i * (sw + gap), r.y + 98, sw, 40);
                    cv.rrect(&seg, 10, if i < l { ACCENT } else { 0x34404f });
                }
            }
            None => self.txt_center(cv, Face::Bold, 44, r, &title, TEXT),
        }
    }

    pub fn load_idle(&mut self) {
        if !self.idle_path.is_empty() {
            if let Ok(t) = std::fs::read_to_string(&self.idle_path) {
                self.idle_cfg = idle::parse(&t);
            }
        }
    }

    fn save_idle(&self) {
        if !self.idle_path.is_empty() {
            let tmp = format!("{}.tmp", self.idle_path);
            if std::fs::write(&tmp, idle::to_json(&self.idle_cfg)).is_ok() {
                let _ = std::fs::rename(&tmp, &self.idle_path);
            }
        }
    }

    /// Once a second: expire cards and questions, refresh countdowns.
    pub fn tick(&mut self) {
        let now = Instant::now();
        let n = self.cards.len();
        self.cards.retain(|c| c.until > now);
        if self.cards.len() != n {
            self.dirty_content();
        }
        if self.confirm.as_ref().is_some_and(|c| c.until <= now) {
            self.answer_confirm("timeout");
        } else if self.confirm.is_some() {
            self.dirty_content();
        }
        self.update_clock();
        if self.camera_poll.is_some() {
            self.poll_camera();
        }
    }

    #[allow(dead_code)]
    pub fn needs_ticks(&self) -> bool {
        !self.cards.is_empty() || self.confirm.is_some() || self.camera_poll.is_some()
    }

    pub fn goto(&mut self, p: Page) {
        if p != self.page {
            self.page = p;
            self.velocity = 0.0;
            self.animating = false;
        }
        self.note.clear();
        self.kb.visible = p == Page::Chat;
        if p == Page::Chat {
            self.stick_bottom = true;
            if let Some(n) = &self.net {
                if self.chat.len() <= 1 {
                    net::fetch_history(n);
                }
            }
        }
        if p == Page::Files && self.files.is_empty() {
            self.load_dir();
        }
        if p == Page::Phone {
            if let Some(n) = &self.net {
                net::fetch_phone(n);
            }
        }
        if p == Page::Camera && self.camera_img.is_none() {
            self.load_camera_image();
        }
        if p == Page::Settings {
            if let Some(n) = &self.net {
                net::touchd_async(n, "brightness", json!({"cmd": "brightness", "arg": "get"}), 8);
            }
        }
        self.dirty_all();
    }

    pub fn state_json(&self) -> Value {
        json!({
            "backend": "fastshell",
            "active": self.active,
            "home": !self.locked && self.page == Page::Home,
            "page": self.page.name(),
            "locked": self.locked,
            "display": if self.display_on { "on" } else { "off" },
            "cards": self.cards.len(),
            "confirm": self.confirm.as_ref().map(|c| c.id.clone()),
            "busy": self.busy,
            "keyboard": self.kb.visible,
            "status": {
                "battery": self.st.battery.map(|b| format!("{b}%")),
                "thermal": self.st.thermal.map(|t| format!("{t:.0}°C")),
                "model": self.st.model,
                "healthy": self.st.health,
                "radio": self.st.radio,
            },
        })
    }

    pub fn perf_json(&self) -> Value {
        let by = |k: &str| -> Vec<f64> { self.stats.lat.iter().filter(|(kk, _)| *kk == k).map(|(_, v)| *v).collect() };
        let s = |v: Vec<f64>| json!({"n": v.len(), "p50_ms": (pct(&v, 0.5) * 100.0).round() / 100.0, "p95_ms": (pct(&v, 0.95) * 100.0).round() / 100.0, "max_ms": (pct(&v, 1.0) * 100.0).round() / 100.0});
        json!({
            "frames": self.stats.frames,
            "touch_to_flip": {"press": s(by("press")), "page": s(by("page")), "key": s(by("key")), "action": s(by("action")), "scroll": s(by("scroll")), "button": s(by("button"))},
            "render_ms": s(self.stats.render.clone()),
            "copy_ms": s(self.stats.copy.clone()),
        })
    }

    /// One call from s22-touchd (shell.sock). Same functions as the Quickshell `touch` IPC.
    pub fn call(&mut self, c: &Call) -> String {
        let a0 = c.arg(0).to_string();
        if !matches!(c.function.as_str(), "state" | "perf" | "ping" | "confirmResult") {
            self.last_touch = Instant::now();
        }
        if !self.display_on && self.active && ["home", "open", "switcher", "card", "confirm"].contains(&c.function.as_str()) {
            // wake for anything the owner must see; the lock cover stays on top
            self.effects.push(Effect::Display(true));
        }
        match c.function.as_str() {
            "ping" => "pong".into(),
            "send" => {
                // type-and-send into Agent chat (tests, and agents that want the chat visible)
                if self.busy {
                    return "busy".into();
                }
                self.input = c.args.join(" ");
                if !self.active {
                    self.effects.push(Effect::Activate);
                }
                if !self.locked {
                    self.goto(Page::Chat);
                }
                self.send();
                format!("sent turn {}", self.turn)
            }
            "home" => {
                if !self.active {
                    self.effects.push(Effect::Activate);
                }
                if self.locked {
                    self.locked = false;
                    self.dirty_all();
                    return "unlocked".into();
                }
                self.goto(Page::Home);
                "ok".into()
            }
            "hide" => "ok".into(),
            "back" => {
                if self.locked || self.confirm.is_some() {
                    return "ignored".into();
                }
                if !self.active {
                    self.effects.push(Effect::Activate);
                    return "shown".into();
                }
                self.back()
            }
            "switcher" => {
                if self.locked {
                    return "locked".into();
                }
                if !self.active {
                    self.effects.push(Effect::Activate);
                }
                self.goto(Page::Switcher);
                "ok".into()
            }
            "gesture" => {
                // touchd's edge-swipe recogniser: the shell recognises its own while it
                // owns the panel, so only act when sway is showing.
                if self.active {
                    return "handled-natively".into();
                }
                self.effects.push(Effect::Activate);
                match a0.as_str() {
                    "switcher" => self.goto(Page::Switcher),
                    "home" => {
                        if !self.locked {
                            self.goto(Page::Home)
                        }
                    }
                    _ => {}
                }
                "activated".into()
            }
            "open" => {
                if self.locked {
                    return "locked".into();
                }
                match a0.as_str() {
                    "terminal" => {
                        self.effects.push(Effect::HandToSway);
                        self.effects.push(Effect::Spawn(vec!["sh".into(), "-c".into(), FOOT.into()]));
                self.effects.push(Effect::Spawn(vec!["@delay1500".into(), "gdbus".into(), "call".into(), "--session".into(), "--dest".into(), "sm.puri.OSK0".into(), "--object-path".into(), "/sm/puri/OSK0".into(), "--method".into(), "sm.puri.OSK0.SetVisible".into(), "true".into()]));
                        "ok (foot under sway)".into()
                    }
                    a => match Page::from_app(a) {
                        Some(p) => {
                            if !self.active {
                                self.effects.push(Effect::Activate);
                            }
                            self.goto(p);
                            "ok".into()
                        }
                        None => format!("unknown app: {a} (known: chat, phone, camera, files, settings, switcher, terminal)"),
                    },
                }
            }
            "card" => {
                let ttl = c.arg(2).parse::<f64>().ok().filter(|t| *t > 0.0).unwrap_or(8.0);
                let id = self.add_card(c.arg(0), c.arg(1), ttl);
                format!("card {id}")
            }
            "confirm" => {
                let id = a0.clone();
                if id.is_empty() || id.len() > 64 || !id.chars().all(|ch| ch.is_ascii_alphanumeric() || ch == '_' || ch == '-') {
                    return "bad id".into();
                }
                if self.confirm.is_some() {
                    return "busy".into();
                }
                let t = c.arg(2).parse::<f64>().ok().filter(|t| *t > 0.0).unwrap_or(60.0).clamp(5.0, 600.0);
                self.confirm = Some(Confirm { id, question: c.arg(1).chars().take(500).collect(), until: Instant::now() + std::time::Duration::from_secs_f64(t) });
                if !self.active {
                    self.effects.push(Effect::Activate);
                }
                self.dirty_all();
                "asking".into()
            }
            "confirmResult" => {
                if self.confirm.as_ref().is_some_and(|x| x.id == a0) { "pending".into() } else { "done".into() }
            }
            "lock" => {
                self.locked = true;
                self.kb.visible = false;
                self.dirty_all();
                "locked".into()
            }
            "unlock" => {
                self.locked = false;
                self.dirty_all();
                "unlocked".into()
            }
            "keyboard" => {
                let on = !["off", "false", "0"].contains(&a0.as_str());
                self.kb.visible = on;
                self.dirty_all();
                "ok".into()
            }
            "display" => {
                if !self.active {
                    return "delegate".into();
                }
                let on = match a0.as_str() {
                    "on" => true,
                    "off" => false,
                    "toggle" => !self.display_on,
                    _ => return if self.display_on { "on".into() } else { "off".into() },
                };
                if !on {
                    self.locked = true;
                    self.kb.visible = false;
                    self.dirty_all();
                }
                self.effects.push(Effect::Display(on));
                if on { "on".into() } else { "off".into() }
            }
            "state" => self.state_json().to_string(),
            "perf" => self.perf_json().to_string(),
            other => format!("unknown function: {other}"),
        }
    }

    fn back(&mut self) -> String {
        if self.viewer.is_some() {
            self.viewer = None;
            self.dirty_all();
            return "viewer closed".into();
        }
        if self.kb.visible && self.page != Page::Chat {
            self.kb.visible = false;
            self.dirty_all();
            return "keyboard hidden".into();
        }
        if self.page != Page::Home {
            self.goto(Page::Home);
            return "grid".into();
        }
        "home".into()
    }

    // ------------------------------------------------------------ files

    fn host_path(&self, p: &str) -> String {
        format!("{}{}", self.chroot, p)
    }

    pub fn load_dir(&mut self) {
        let mut v = vec![];
        if let Ok(rd) = std::fs::read_dir(self.host_path(&self.files_dir)) {
            for e in rd.flatten() {
                let name = e.file_name().to_string_lossy().to_string();
                let md = e.metadata().ok();
                let dir = md.as_ref().map(|m| m.is_dir()).unwrap_or(false);
                v.push(FileEntry { name, dir, size: md.map(|m| m.len()).unwrap_or(0) });
            }
        }
        v.sort_by(|a, b| b.dir.cmp(&a.dir).then(a.name.to_lowercase().cmp(&b.name.to_lowercase())));
        self.files = v;
        self.scroll[Page::Files.idx()] = 0.0;
    }

    fn open_file(&mut self, i: usize) {
        let Some(e) = self.files.get(i) else { return };
        let path = if self.files_dir == "/" { format!("/{}", e.name) } else { format!("{}/{}", self.files_dir, e.name) };
        if e.dir {
            self.files_dir = path;
            self.load_dir();
            self.dirty_content();
            return;
        }
        let host = self.host_path(&path);
        let lower = e.name.to_lowercase();
        if lower.ends_with(".png") {
            if let Some((px, w, h)) = load_png(&host, 1016, 1900) {
                self.viewer = Some(Viewer::Image(e.name.clone(), px, w, h));
                self.dirty_content();
                return;
            }
        }
        let mut buf = vec![];
        if let Ok(f) = std::fs::File::open(&host) {
            use std::io::Read;
            let _ = f.take(64 * 1024).read_to_end(&mut buf);
        }
        let text = if buf.iter().take(4096).any(|&b| b == 0) {
            format!("Binary file, {} bytes.", e.size)
        } else {
            String::from_utf8_lossy(&buf).replace('\t', "    ")
        };
        let lines = self.fonts.wrap(Face::Regular, 32, &text, W - 2 * M - 16);
        self.viewer = Some(Viewer::Text(e.name.clone(), lines.into_iter().take(3000).collect()));
        self.scroll[Page::Files.idx()] = 0.0;
        self.dirty_content();
    }

    // ------------------------------------------------------------ activation

    fn activate(&mut self, act: Act, t_in: u64) {
        let page_before = (self.page, self.locked, self.viewer.is_some(), self.kb.visible);
        let mut kind = "action";
        match act {
            Act::Open(p) => self.goto(p),
            Act::Back => {
                self.back();
            }
            Act::Lock => {
                self.locked = true;
                self.kb.visible = false;
                self.dirty_all();
            }
            Act::Voice => {
                self.voice_running = true;
                let cmd = std::env::var("FASTSHELL_ASSISTANT").unwrap_or_else(|_| config::get().assistant.clone());
                self.effects.push(Effect::Assistant(cmd, vec![("S22_BUTTON".into(), "fastshell".into())]));
                self.add_card("Listening…", "Speak now; the answer is spoken and shown here.", 12.0);
            }
            Act::Terminal => {
                self.effects.push(Effect::HandToSway);
                self.effects.push(Effect::Spawn(vec!["sh".into(), "-c".into(), FOOT.into()]));
                self.effects.push(Effect::Spawn(vec!["@delay1500".into(), "gdbus".into(), "call".into(), "--session".into(), "--dest".into(), "sm.puri.OSK0".into(), "--object-path".into(), "/sm/puri/OSK0".into(), "--method".into(), "sm.puri.OSK0.SetVisible".into(), "true".into()]));
            }
            Act::Sway => self.effects.push(Effect::HandToSway),
            Act::Send => self.send(),
            Act::Stop => {
                if let Some(n) = &self.net {
                    net::cancel_turn(n);
                }
                self.activity = Some("stopping…".into());
                self.chat_changed();
            }
            Act::Field => {
                self.kb.visible = true;
                self.dirty_content();
            }
            Act::Key(r, c) => {
                kind = "key";
                self.key(r, c);
            }
            Act::ConfirmYes => self.answer_confirm("yes"),
            Act::ConfirmNo => self.answer_confirm("no"),
            Act::Card(id) => {
                self.cards.retain(|c| c.id != id);
                if self.prompt.is_some() && self.page != Page::Chat {
                    self.goto(Page::Chat);
                }
                self.dirty_content();
            }
            Act::PromptOpt(i) => {
                if let Some(p) = self.prompt.take() {
                    let v = p.options.get(i).cloned().unwrap_or_default();
                    self.chat.push((Who::You, v.clone()));
                    if let Some(n) = &self.net {
                        net::answer_prompt(n, p.id, v);
                    }
                    self.chat_changed();
                }
            }
            Act::FilesUp => {
                if let Some(i) = self.files_dir.rfind('/') {
                    self.files_dir = if i == 0 { "/".into() } else { self.files_dir[..i].to_string() };
                    self.load_dir();
                    self.dirty_content();
                }
            }
            Act::File(i) => self.open_file(i),
            Act::Bright => {}
            Act::Model(m) => {
                self.note = format!("Switching model to {m}…");
                if let Some(n) = &self.net {
                    net::touchd_async(n, "model", json!({"cmd": "model", "arg": m}), 60);
                }
                self.dirty_content();
            }
            Act::Classic => {
                let f = &config::get().ui_backend_file;
                let _ = std::fs::write(format!("{f}.tmp"), "quickshell\n").and_then(|_| std::fs::rename(format!("{f}.tmp"), f));
                self.note = "Switching to the classic desktop…".into();
                self.dirty_content();
            }
            Act::ScreenOff => {
                self.locked = true;
                self.kb.visible = false;
                self.dirty_all();
                self.effects.push(Effect::Display(false));
            }
            Act::IdleCycle(locked) => {
                if locked {
                    self.idle_cfg.locked_s = idle::next_preset(self.idle_cfg.locked_s);
                } else {
                    self.idle_cfg.unlocked_s = idle::next_preset(self.idle_cfg.unlocked_s);
                }
                self.save_idle();
                self.dirty_content();
            }
            Act::ButtonsToggle => {
                self.buttons_enabled = !self.buttons_enabled;
                if !self.buttons_cfg.is_empty() {
                    crate::buttons::save_enabled(&self.buttons_cfg, self.buttons_enabled);
                }
                self.note = if self.buttons_enabled { "Buttons: handled by the shell".into() } else { "Buttons: handed to s22-buttons".into() };
                self.dirty_content();
            }
            Act::Capture => {
                self.camera_state = "starting the camera…".into();
                if let Some(n) = &self.net {
                    net::touchd_async(n, "camera", json!({"cmd": "camera_capture", "sensor": self.camera_sensor}), 10);
                }
                self.dirty_content();
            }
            Act::CamSensor(s) => {
                self.camera_sensor = s;
                self.dirty_content();
            }
            Act::Refresh => {
                if let Some(n) = &self.net {
                    match self.page {
                        Page::Phone => net::fetch_phone(n),
                        _ => net::refresh_agent(n),
                    }
                }
                self.read_sys();
            }
            Act::Nothing => {}
        }
        let after = (self.page, self.locked, self.viewer.is_some(), self.kb.visible);
        if after != page_before {
            kind = "page";
        }
        self.mark = Some((t_in, kind));
    }

    fn key(&mut self, r: usize, c: usize) {
        let rows = kbd::rows(self.kb.layout);
        let Some(k) = rows.get(r).and_then(|row| row.get(c)) else { return };
        match self.kb.press(k) {
            KeyOut::Text(s) => {
                if self.input.chars().count() < 4000 {
                    self.input.push_str(&s);
                }
            }
            KeyOut::Backspace => {
                self.input.pop();
            }
            KeyOut::Enter => {
                if self.page == Page::Chat {
                    self.send();
                }
            }
            KeyOut::Redraw => {
                let kr = self.kb_rect();
                self.dirty(kr);
                self.relayout = true;
            }
        }
        let f = self.field_rect();
        self.dirty(f);
        // shift / tonos indicators live on keys
        if self.kb.visible {
            let kr = self.kb_rect();
            self.dirty(kr);
        }
    }

    // ------------------------------------------------------------ touch

    fn hit_at(&self, x: i32, y: i32) -> Option<Hit> {
        self.hits.iter().rev().find(|h| h.r.contains(x, y)).cloned()
    }

    fn set_pressed(&mut self, h: Option<Hit>) {
        if let Some(p) = &self.pressed {
            let r = p.r;
            self.dirty(r);
        }
        if let Some(p) = &h {
            let r = p.r;
            self.dirty(r);
        }
        self.pressed = h;
    }

    pub fn touch(&mut self, ev: crate::touch::Touch) {
        use crate::touch::Touch;
        self.last_touch = Instant::now();
        if !self.display_on {
            return;
        }
        match ev {
            Touch::Down(x, y, t) => {
                self.velocity = 0.0;
                self.animating = false;
                let edge = if y >= H - EDGE_B {
                    Some('b')
                } else if y < EDGE_T {
                    Some('t')
                } else if x < EDGE_S {
                    Some('l')
                } else if x >= W - EDGE_S {
                    Some('r')
                } else {
                    None
                };
                let hit = if self.locked && self.confirm.is_none() { None } else { self.hit_at(x, y) };
                let in_scroll = self.scroll_rect.is_some_and(|r| r.contains(x, y)) && self.confirm.is_none() && !self.locked;
                if let Some(h) = &hit {
                    if h.hold_ms > 0 {
                        self.hold = Some((Instant::now(), h.clone()));
                    }
                    if let Act::Key(r, c) = h.act {
                        let rows = kbd::rows(self.kb.layout);
                        if rows[r][c] == kbd::BKSP {
                            self.repeat_at = Some((Instant::now() + std::time::Duration::from_millis(450), r, c));
                        }
                    }
                    if h.act == Act::Bright {
                        self.slide(x, true);
                    }
                }
                self.set_pressed(hit.clone());
                if hit.is_some() {
                    self.mark = Some((t, "press"));
                }
                self.touch = Some(Touching { x0: x, y0: y, t0: t, x, y, edge, dragging: false, samples: vec![(t, y)], hit, in_scroll });
            }
            Touch::Move(x, y, t) => {
                let Some(mut tc) = self.touch.take() else { return };
                let (dx, dy) = (x - tc.x0, y - tc.y0);
                if tc.hit.as_ref().is_some_and(|h| h.act == Act::Bright) {
                    self.slide(x, false);
                    tc.x = x;
                    tc.y = y;
                    self.touch = Some(tc);
                    return;
                }
                if !tc.dragging && (dx.abs() > SLOP || dy.abs() > SLOP) {
                    tc.dragging = true;
                    self.repeat_at = None;
                    self.hold = None;
                    if self.pressed.is_some() {
                        self.set_pressed(None);
                    }
                }
                if tc.dragging && tc.in_scroll && tc.edge.is_none() {
                    let step = y - tc.y;
                    if step != 0 {
                        self.scroll_by(-(step as f32));
                        self.stick_bottom = false;
                        if !matches!(self.mark, Some((_, "scroll"))) {
                            self.mark = Some((t, "scroll"));
                        }
                    }
                }
                tc.samples.push((t, y));
                if tc.samples.len() > 12 {
                    tc.samples.remove(0);
                }
                tc.x = x;
                tc.y = y;
                self.touch = Some(tc);
            }
            Touch::Up(x, y, t) => {
                let Some(tc) = self.touch.take() else { return };
                self.repeat_at = None;
                let hold = self.hold.take();
                let (dx, dy) = (x - tc.x0, y - tc.y0);
                let dt_ms = (t.saturating_sub(tc.t0)) / 1000;
                // edge swipes (same thresholds as s22-touchd's EdgeSwipe)
                let gesture = match tc.edge {
                    _ if dt_ms > 900 => None,
                    Some('b') if -dy >= H * 12 / 100 && dx.abs() * 10 <= -dy * 6 => Some("home"),
                    Some('t') if dy >= H * 12 / 100 && dx.abs() * 10 <= dy * 6 => Some("switcher"),
                    Some('l') if dx >= W * 10 / 100 && dy.abs() * 10 <= dx * 6 => Some("back"),
                    Some('r') if -dx >= W * 10 / 100 && dy.abs() * 10 <= -dx * 6 => Some("back"),
                    _ => None,
                };
                let pressed = self.pressed.clone();
                self.set_pressed(None);
                if let Some(g) = gesture {
                    if self.confirm.is_some() {
                        return;
                    }
                    let before = (self.page, self.locked);
                    match g {
                        "home" => {
                            if self.locked {
                                self.locked = false;
                                self.dirty_all();
                            } else {
                                self.goto(Page::Home);
                            }
                        }
                        "switcher" => {
                            if !self.locked {
                                self.goto(Page::Switcher)
                            }
                        }
                        _ => {
                            if !self.locked {
                                self.back();
                            }
                        }
                    }
                    let _ = before;
                    self.mark = Some((t, "page"));
                    return;
                }
                if self.locked && self.confirm.is_none() {
                    if -dy >= H * 22 / 100 {
                        self.locked = false;
                        self.dirty_all();
                        self.mark = Some((t, "page"));
                    }
                    return;
                }
                if tc.dragging && tc.in_scroll {
                    // kinetic: velocity over the last ~80 ms
                    let last = *tc.samples.last().unwrap();
                    let first = tc.samples.iter().find(|(ts, _)| last.0.saturating_sub(*ts) <= 80_000).copied().unwrap_or(last);
                    let span = (last.0.saturating_sub(first.0)) as f32 / 1e6;
                    if span > 0.008 {
                        self.velocity = -((last.1 - first.1) as f32) / span;
                        if self.velocity.abs() > 120.0 {
                            self.animating = true;
                        }
                    }
                    return;
                }
                if tc.dragging {
                    return;
                }
                if let Some(h) = pressed {
                    if h.act == Act::Bright {
                        self.slide(x, true);
                        return;
                    }
                    if h.hold_ms > 0 {
                        let held = hold.map(|(s, _)| s.elapsed().as_millis() as u32).unwrap_or(0);
                        if held < h.hold_ms {
                            self.note = format!("Hold for {:.1} s", h.hold_ms as f32 / 1000.0);
                            self.dirty_content();
                            return;
                        }
                    }
                    if h.r.contains(x, y) || matches!(h.act, Act::Key(..)) {
                        self.activate(h.act.clone(), t);
                    }
                }
            }
            Touch::Cancel(_) => {
                self.touch = None;
                self.repeat_at = None;
                self.hold = None;
                self.set_pressed(None);
            }
        }
    }

    /// Hold-to-activate buttons fire while still held.
    pub fn check_hold(&mut self) -> bool {
        if let Some((s, h)) = &self.hold {
            if s.elapsed().as_millis() as u32 >= h.hold_ms {
                let h = h.clone();
                self.hold = None;
                self.touch = None;
                self.set_pressed(None);
                self.activate(h.act, now_us());
                return true;
            }
        }
        false
    }

    pub fn check_repeat(&mut self) {
        if let Some((at, r, c)) = self.repeat_at {
            if Instant::now() >= at {
                self.key(r, c);
                self.repeat_at = Some((Instant::now() + std::time::Duration::from_millis(70), r, c));
            }
        }
    }

    fn slider_rect(&self) -> Rect {
        Rect::new(M + 40, STATUS_H + 130 + 20 + 190 + M + 130, W - 2 * M - 80, 110)
    }

    fn slide(&mut self, x: i32, commit: bool) {
        let r = self.slider_rect();
        let f = ((x - r.x) as f32 / r.w as f32).clamp(0.02, 1.0);
        let v = ((f * self.brightness.1 as f32).round() as i32).max(1);
        if v != self.brightness.0 {
            self.brightness.0 = v;
            self.dirty(Rect::new(0, r.y - 80, W, r.h + 100));
        }
        if commit || self.bright_sent.elapsed().as_millis() > 150 {
            self.bright_sent = Instant::now();
            if let Some(n) = &self.net {
                net::touchd_async(n, "brightness", json!({"cmd": "brightness", "arg": v.to_string()}), 8);
            }
        }
    }

    fn max_scroll(&self) -> f32 {
        let r = self.scroll_rect.unwrap_or_default();
        (self.content_h[self.page.idx()] - r.h).max(0) as f32
    }

    pub fn scroll_by(&mut self, d: f32) {
        let i = self.page.idx();
        let old = self.scroll[i];
        self.scroll[i] = (self.scroll[i] + d).clamp(0.0, self.max_scroll());
        if (self.scroll[i] as i32) != (old as i32) {
            self.relayout = true;
        }
        if self.page == Page::Chat {
            self.stick_bottom = self.scroll[i] >= self.max_scroll() - 2.0;
        }
    }

    /// Advance the kinetic scroll by `dt` seconds. Returns whether it still runs.
    pub fn animate(&mut self, dt: f32) -> bool {
        if !self.animating {
            return false;
        }
        let before = self.scroll[self.page.idx()];
        self.scroll_by(self.velocity * dt);
        self.velocity *= (-dt / 0.325).exp();
        let after = self.scroll[self.page.idx()];
        if self.velocity.abs() < 25.0 || (after - before).abs() < 0.01 {
            self.animating = false;
            self.velocity = 0.0;
        }
        self.animating
    }

    // ------------------------------------------------------------ render plumbing

    /// Collect hit regions (and scroll geometry) for the current state.
    pub fn layout(&mut self) {
        self.hits.clear();
        self.layout_pass = true;
        let mut cv = std::mem::replace(&mut self.dummy, Canvas::new(1, 1));
        cv.clip = Rect::new(0, 0, 0, 0);
        self.scene(&mut cv);
        self.dummy = cv;
        self.layout_pass = false;
        self.relayout = false;
    }

    /// Before drawing: apply a pending scroll to the canvas by moving pixels and
    /// return (rects to re-render, extra rects to copy to scanout).
    pub fn prepare_frame(&mut self, cv: &mut Canvas) -> (Vec<Rect>, Vec<Rect>) {
        if self.relayout {
            self.layout();
        }
        let mut render = std::mem::take(&mut self.damage);
        let mut copy = vec![];
        let cur = self.scroll_rect.map(|r| (self.page, self.viewer.is_some(), r, self.scroll[self.page.idx()] as i32));
        if let (Some((p, v, r, s)), Some((rp, rv, rs))) = (cur, self.rendered_scroll) {
            if p == rp && v == rv && s != rs {
                let overlay = self.confirm.is_some() || self.osd.is_some() || self.flash_until.is_some() || !self.cards.is_empty() && self.cards_rect().hits(&r);
                let whole_damaged = render.iter().any(|d| d.intersect(&r).is_some_and(|i| i.area() * 10 >= r.area() * 9));
                if !overlay && !whole_damaged && (s - rs).abs() < r.h {
                    if let Some(strip) = cv.scroll(&r, s - rs) {
                        render.push(strip);
                    }
                    copy.push(r);
                } else {
                    render.push(r);
                }
            }
        }
        self.rendered_scroll = cur.map(|(p, v, _, s)| (p, v, s));
        crate::gfx::merge_rects(&mut render);
        (render, copy)
    }

    fn hit(&mut self, r: Rect, act: Act) {
        if self.layout_pass {
            self.hits.push(Hit { r, act, hold_ms: 0 });
        }
    }

    fn is_pressed(&self, a: &Act) -> bool {
        self.pressed.as_ref().is_some_and(|p| &p.act == a)
    }

    fn txt(&mut self, cv: &mut Canvas, f: Face, px: u16, x: i32, y: i32, s: &str, c: u32) -> i32 {
        if self.layout_pass {
            return x;
        }
        self.fonts.draw(cv, f, px, x, y, s, c)
    }

    fn txt_center(&mut self, cv: &mut Canvas, f: Face, px: u16, r: Rect, s: &str, c: u32) {
        if self.layout_pass || !r.hits(&cv.clip) {
            return;
        }
        let w = self.fonts.measure(f, px, s);
        let asc = self.fonts.ascent(f, px);
        let base = r.y + (r.h + asc) / 2 - asc / 8;
        self.fonts.draw(cv, f, px, r.x + (r.w - w) / 2, base, s, c);
    }

    fn button(&mut self, cv: &mut Canvas, r: Rect, label: &str, act: Act, fill: u32, fill_p: u32, px: u16) {
        let p = self.is_pressed(&act);
        if !self.layout_pass && r.hits(&cv.clip) {
            cv.rrect(&r, 28, if p { fill_p } else { fill });
        }
        self.txt_center(cv, Face::Bold, px, r, label, TEXT);
        self.hit(r, act);
    }

    // ------------------------------------------------------------ scene

    pub fn scene(&mut self, cv: &mut Canvas) {
        let clip = cv.clip;
        if !self.layout_pass {
            cv.fill(&Rect::new(0, 0, W, H), BG);
        }
        self.scroll_rect = None;
        if self.locked {
            self.draw_lock(cv);
        } else {
            self.draw_status(cv);
            match self.page {
                Page::Home => self.draw_home(cv),
                Page::Chat => self.draw_chat(cv),
                Page::Phone => self.draw_phone(cv),
                Page::Camera => self.draw_camera(cv),
                Page::Files => self.draw_files(cv),
                Page::Settings => self.draw_settings(cv),
                Page::Switcher => self.draw_switcher(cv),
            }
            if self.kb.visible {
                self.draw_kbd(cv);
            }
            self.draw_cards(cv);
        }
        if self.confirm.is_some() {
            self.draw_confirm(cv);
        }
        self.draw_osd(cv);
        if self.flash_until.is_some() && !self.layout_pass {
            cv.fill_alpha(&Rect::new(0, 0, W, H), 0xffffff, 200);
        }
        cv.clip = clip;
    }

    fn draw_status(&mut self, cv: &mut Canvas) {
        let bar = Rect::new(0, 0, W, STATUS_H);
        if !self.layout_pass && bar.hits(&cv.clip) {
            cv.fill(&bar, BAR);
            let clock = self.clock.clone();
            self.txt(cv, Face::Bold, 40, M, 64, &clock, TEXT);
            let mut right = vec![];
            if let Some(b) = self.st.battery {
                right.push(format!("{b}%"));
            }
            if let Some(t) = self.st.thermal {
                right.push(format!("{t:.0}°C"));
            }
            right.push(self.st.net.clone());
            let s = right.join("  ·  ");
            let w = self.fonts.measure(Face::Regular, 32, &s);
            self.txt(cv, Face::Regular, 32, W - M - w, 62, &s, DIM);
            if let Some(badge) = self.st.radio {
                // small pill left of the status text: 5G accent, others plain
                let bw = self.fonts.measure(Face::Bold, 28, badge);
                let pill = Rect::new(W - M - w - bw - 52, 30, bw + 28, 44);
                cv.rrect(&pill, 12, if badge == "5G" { ACCENT } else { SURF2 });
                self.txt(cv, Face::Bold, 28, pill.x + 14, 62, badge, TEXT);
            }
            let dot = match self.st.health.as_deref() {
                Some("healthy") => GOOD,
                Some("down") => BAD,
                Some(_) => WARN,
                None => DIM,
            };
            cv.circle(200, 50, 9, dot);
        }
    }

    fn header(&mut self, cv: &mut Canvas, title: &str, sub: &str) -> i32 {
        let r = Rect::new(0, STATUS_H, W, 130);
        let back = Rect::new(8, STATUS_H + 5, 140, 120);
        if !self.layout_pass && r.hits(&cv.clip) {
            if self.is_pressed(&Act::Back) {
                cv.rrect(&back, 28, PRESS);
            }
            self.txt(cv, Face::Regular, 72, 40, STATUS_H + 92, "‹", TEXT);
            self.txt(cv, Face::Bold, 48, 130, STATUS_H + 66, title, TEXT);
            let s = self.fonts.ellipsize(Face::Regular, 30, sub, W - 160 - M);
            self.txt(cv, Face::Regular, 30, 132, STATUS_H + 110, &s, DIM);
        }
        self.hit(back, Act::Back);
        STATUS_H + 130
    }

    fn draw_home(&mut self, cv: &mut Canvas) {
        let mut y = STATUS_H + 30;
        // title
        if !self.layout_pass && Rect::new(0, y, W, 150).hits(&cv.clip) {
            let date = self.date.clone();
            self.txt(cv, Face::Bold, 64, M, y + 70, "OpenUnum", TEXT);
            self.txt(cv, Face::Regular, 34, M, y + 122, &date, DIM);
        }
        y += 160;
        // live cards 2x2
        let cw = (W - 3 * M) / 2;
        let ch = 210;
        let st = self.st.clone();
        let health = st.health.clone().unwrap_or_else(|| "connecting".into());
        let cards: [(&str, String, String, u32); 4] = [
            ("Agent", st.model.clone().unwrap_or_else(|| "—".into()), health.clone(), match health.as_str() { "healthy" => GOOD, "down" => BAD, "connecting" => DIM, _ => WARN }),
            (
                "Sessions",
                st.sessions.map(|(a, _)| format!("{a} active")).unwrap_or_else(|| "—".into()),
                format!("of {} · {} missions running", st.sessions.map(|s| s.1).unwrap_or(0), st.running.unwrap_or(0)),
                ACCENT,
            ),
            (
                "Battery",
                st.battery.map(|b| format!("{b}%")).unwrap_or_else(|| "—".into()),
                format!("{}{}", st.bat_state, st.bat_temp.map(|t| format!(" · {t:.1}°C")).unwrap_or_default()),
                match st.battery { Some(b) if b < 15 => BAD, Some(b) if b < 30 => WARN, _ => GOOD },
            ),
            (
                "Thermal",
                st.thermal.map(|t| format!("{t:.0}°C")).unwrap_or_else(|| "—".into()),
                st.thermal_detail.clone(),
                match st.thermal { Some(t) if t >= 60.0 => BAD, Some(t) if t >= 48.0 => WARN, _ => GOOD },
            ),
        ];
        for (i, (label, value, detail, color)) in cards.iter().enumerate() {
            let r = Rect::new(M + (i as i32 % 2) * (cw + M), y + (i as i32 / 2) * (ch + M), cw, ch);
            let act = if i == 0 { Act::Open(Page::Chat) } else if i == 1 { Act::Open(Page::Switcher) } else { Act::Refresh };
            if !self.layout_pass && r.hits(&cv.clip) {
                cv.rrect(&r, 32, if self.is_pressed(&act) && i < 2 { PRESS } else { SURF });
                cv.circle(r.x + 40, r.y + 52, 10, *color);
                self.txt(cv, Face::Regular, 30, r.x + 62, r.y + 63, label, DIM);
                let v = self.fonts.ellipsize(Face::Bold, 46, value, r.w - 56);
                self.txt(cv, Face::Bold, 46, r.x + 28, r.y + 130, &v, TEXT);
                let d = self.fonts.ellipsize(Face::Regular, 28, detail, r.w - 56);
                self.txt(cv, Face::Regular, 28, r.x + 28, r.y + 180, &d, DIM);
            }
            if i < 2 {
                self.hit(r, act);
            }
        }
        y += 2 * ch + 2 * M + 10;
        // app tiles 3x2
        let tw = (W - 4 * M) / 3;
        let th = 250;
        let tiles: [(&str, Act, u32, &str); 6] = [
            ("Agent", Act::Open(Page::Chat), 0x3b82f6, "A"),
            ("Phone", Act::Open(Page::Phone), 0x3fb950, "P"),
            ("Camera", Act::Open(Page::Camera), 0xe3b341, "C"),
            ("Files", Act::Open(Page::Files), 0xa371f7, "F"),
            ("Terminal", Act::Terminal, 0x8b949e, ">_"),
            ("Settings", Act::Open(Page::Settings), 0xf0883e, "S"),
        ];
        for (i, (label, act, color, glyph)) in tiles.iter().enumerate() {
            let r = Rect::new(M + (i as i32 % 3) * (tw + M), y + (i as i32 / 3) * (th + M), tw, th);
            if !self.layout_pass && r.hits(&cv.clip) {
                let p = self.is_pressed(act);
                cv.rrect(&r, 36, if p { PRESS } else { SURF });
                let ic = Rect::new(r.x + (r.w - 110) / 2, r.y + 34, 110, 110);
                cv.rrect(&ic, 30, *color);
                self.txt_center(cv, Face::Bold, 48, ic, glyph, 0x0b0f14);
                self.txt_center(cv, Face::Bold, 36, Rect::new(r.x, r.y + 160, r.w, 64), label, TEXT);
            }
            self.hit(r, act.clone());
        }
        y += 2 * th + 2 * M + 10;
        // action row
        let bw = (W - 4 * M) / 3;
        let bh = 150;
        let row: [(&str, Act, u32, u32); 3] = [("Talk", Act::Voice, ACCENT, ACCENT_P), ("Apps", Act::Open(Page::Switcher), SURF2, PRESS), ("Lock", Act::Lock, SURF2, PRESS)];
        for (i, (label, act, f, fp)) in row.iter().enumerate() {
            let r = Rect::new(M + i as i32 * (bw + M), y, bw, bh);
            self.button(cv, r, label, act.clone(), *f, *fp, 40);
            if i == 0 && !self.layout_pass && r.hits(&cv.clip) {
                mic(cv, r.x + 60, r.y + bh / 2, TEXT, if self.is_pressed(act) { ACCENT_P } else { ACCENT });
            }
        }
        y += bh + M;
        // last reply preview
        let last = self.chat.iter().rev().find(|(w, _)| *w == Who::Agent).map(|(_, t)| t.clone()).unwrap_or_default();
        let r = Rect::new(M, y, W - 2 * M, (H - EDGE_B - 70 - y).max(120));
        if !self.layout_pass && r.hits(&cv.clip) {
            cv.rrect(&r, 32, if self.is_pressed(&Act::Open(Page::Chat)) { PRESS } else { SURF });
            self.txt(cv, Face::Regular, 30, r.x + 28, r.y + 52, "Last reply", DIM);
            let lines = self.fonts.wrap(Face::Regular, 34, &last, r.w - 56);
            let maxl = ((r.h - 80) / 48).max(1) as usize;
            for (k, l) in lines.iter().take(maxl).enumerate() {
                let l = if k + 1 == maxl && lines.len() > maxl { format!("{l}…") } else { l.clone() };
                self.txt(cv, Face::Regular, 34, r.x + 28, r.y + 104 + k as i32 * 48, &l, TEXT);
            }
        }
        self.hit(r, Act::Open(Page::Chat));
        if !self.layout_pass {
            let hint = "Swipe up from the bottom: home  ·  from a side: back  ·  from the top: apps";
            let w = self.fonts.measure(Face::Regular, 25, hint);
            self.txt(cv, Face::Regular, 25, (W - w) / 2, H - EDGE_B - 22, hint, 0x5d6875);
        }
    }

    fn kb_rect(&self) -> Rect {
        Rect::new(0, H - KB_H, W, KB_H)
    }

    fn field_rect(&self) -> Rect {
        let bottom = if self.kb.visible { H - KB_H } else { H - EDGE_B - 10 };
        Rect::new(M, bottom - 150, W - 2 * M - 2 * 140, 128)
    }

    fn chat_blocks(&mut self, width: i32) {
        let key = (self.chat.len(), self.chat.last().map(|l| l.1.len()).unwrap_or(0), self.live.len() + self.activity.as_ref().map(|a| a.len() + 1).unwrap_or(0) + self.prompt.as_ref().map(|p| p.question.len() + 7).unwrap_or(0), width);
        if key == self.blocks_key {
            return;
        }
        self.blocks_key = key;
        let maxw = width * 84 / 100;
        let mut blocks = vec![];
        let mut y = 24;
        let lh = 54;
        let mut items: Vec<(Who, String)> = self.chat.clone();
        if self.busy {
            let mut t = self.live.clone();
            if let Some(a) = &self.activity {
                if !t.is_empty() {
                    t.push('\n');
                }
                t.push_str(&format!("[{a}]"));
            }
            items.push((Who::Agent, if t.is_empty() { "…".into() } else { t }));
        }
        for (who, text) in items {
            let px = if who == Who::Note { 30 } else { 40 };
            let lines = self.fonts.wrap(Face::Regular, px, &text, maxw - 64);
            let w = lines.iter().map(|l| self.fonts.measure(Face::Regular, px, l)).max().unwrap_or(0) + 64;
            let h = lines.len() as i32 * if who == Who::Note { 42 } else { lh } + 44;
            blocks.push(Block { who, y, h, w: w.min(maxw), lines });
            y += h + 22;
        }
        self.blocks = blocks;
        self.content_h[Page::Chat.idx()] = y + 20;
    }

    fn draw_chat(&mut self, cv: &mut Canvas) {
        let sub = match (&self.activity, &self.st.model) {
            (Some(a), _) => a.clone(),
            (None, Some(m)) => format!("{m} · session touch"),
            _ => "session touch".into(),
        };
        let top = self.header(cv, "Agent", &sub);
        if self.busy {
            let r = Rect::new(W - M - 170, STATUS_H + 24, 170, 84);
            self.button(cv, r, "Stop", Act::Stop, 0x5a1e1e, 0x7a2a2a, 34);
        }
        let field = self.field_rect();
        let mut list_bottom = field.y - 16;
        // agent question buttons sit above the input row
        let prompt = self.prompt.clone();
        if let Some(p) = &prompt {
            let n = p.options.len().max(1) as i32;
            let qh = 70 + ((n + 1) / 2) * 110;
            let r = Rect::new(M, list_bottom - qh, W - 2 * M, qh);
            if !self.layout_pass && r.hits(&cv.clip) {
                cv.rrect(&r, 28, 0x2a2410);
                let q = self.fonts.ellipsize(Face::Bold, 32, &format!("{} {}", p.header, p.question), r.w - 48);
                self.txt(cv, Face::Bold, 32, r.x + 24, r.y + 48, &q, 0xf2cc60);
            }
            let bw = (r.w - 3 * 20) / 2;
            for (i, o) in p.options.iter().enumerate() {
                let b = Rect::new(r.x + 20 + (i as i32 % 2) * (bw + 20), r.y + 70 + (i as i32 / 2) * 110, bw, 92);
                let lbl = self.fonts.ellipsize(Face::Bold, 32, o, bw - 24);
                self.button(cv, b, &lbl, Act::PromptOpt(i), SURF2, PRESS, 32);
            }
            list_bottom = r.y - 12;
        }
        let list = Rect::new(0, top, W, list_bottom - top);
        self.scroll_rect = Some(list);
        self.chat_blocks(W - 2 * M);
        let i = Page::Chat.idx();
        let maxs = (self.content_h[i] - list.h).max(0) as f32;
        if self.stick_bottom {
            self.scroll[i] = maxs;
        }
        self.scroll[i] = self.scroll[i].clamp(0.0, maxs);
        let off = self.scroll[i] as i32;
        if !self.layout_pass && list.hits(&cv.clip) {
            let saved = cv.clip;
            if let Some(c) = saved.intersect(&list) {
                cv.clip = c;
                let blocks = std::mem::take(&mut self.blocks);
                for b in &blocks {
                    let y = list.y + b.y - off;
                    if y + b.h < c.y || y > c.y + c.h {
                        continue;
                    }
                    let (x, fill, px, lh, col) = match b.who {
                        Who::You => (W - M - b.w, USER_B, 40u16, 54, TEXT),
                        Who::Agent => (M, AGENT_B, 40, 54, TEXT),
                        Who::Note => ((W - b.w) / 2, BG, 30, 42, DIM),
                    };
                    cv.rrect(&Rect::new(x, y, b.w, b.h), 30, fill);
                    for (k, l) in b.lines.iter().enumerate() {
                        let c2 = if l.starts_with('[') && l.ends_with(']') { DIM } else { col };
                        self.fonts.draw(cv, Face::Regular, px, x + 32, y + 22 + (k as i32 + 1) * lh - lh / 4, l, c2);
                    }
                }
                self.blocks = blocks;
                cv.clip = saved;
            }
        }
        // input row
        if !self.layout_pass && Rect::new(0, field.y - 16, W, field.h + 32).hits(&cv.clip) {
            cv.fill(&Rect::new(0, field.y - 16, W, field.h + 32), BG);
            cv.rrect(&field, 40, if self.kb.visible { 0x1f2a38 } else { SURF });
            let maxw = field.w - 64;
            if self.input.is_empty() {
                self.txt(cv, Face::Regular, 38, field.x + 32, field.y + 78, "Message the agent…", DIM);
            } else {
                let t = self.fonts.tail(Face::Regular, 38, &self.input.replace('\n', " "), maxw - 10);
                let end = self.txt(cv, Face::Regular, 38, field.x + 32, field.y + 78, &t, TEXT);
                cv.fill(&Rect::new(end + 3, field.y + 34, 4, 58), ACCENT);
            }
        }
        self.hit(field, Act::Field);
        let mic_r = Rect::new(W - M - 2 * 128 - 12, field.y, 128, field.h);
        let send_r = Rect::new(W - M - 128, field.y, 128, field.h);
        self.button(cv, mic_r, "", Act::Voice, SURF2, PRESS, 30);
        if !self.layout_pass && mic_r.hits(&cv.clip) {
            mic(cv, mic_r.x + 64, mic_r.y + 64, TEXT, if self.is_pressed(&Act::Voice) { PRESS } else { SURF2 });
        }
        let can = !self.input.trim().is_empty() && !self.busy;
        self.button(cv, send_r, "Send", Act::Send, if can { ACCENT } else { SURF2 }, ACCENT_P, 30);
    }

    fn list_page(&mut self, cv: &mut Canvas, top: i32, rows: &[(String, String, u32)], row_acts: Option<&dyn Fn(usize) -> Act>, row_h: i32) {
        let bottom = if self.kb.visible { H - KB_H } else { H - EDGE_B };
        let list = Rect::new(0, top, W, bottom - top);
        self.scroll_rect = Some(list);
        let i = self.page.idx();
        self.content_h[i] = rows.len() as i32 * row_h + 40;
        let maxs = (self.content_h[i] - list.h).max(0) as f32;
        self.scroll[i] = self.scroll[i].clamp(0.0, maxs);
        let off = self.scroll[i] as i32;
        let saved = cv.clip;
        let c = if self.layout_pass { Some(Rect::new(0, 0, 0, 0)) } else { saved.intersect(&list) };
        let Some(c) = c else { return };
        cv.clip = c;
        let first = ((off - 20) / row_h).max(0) as usize;
        for (k, (a, b, col)) in rows.iter().enumerate().skip(first) {
            let y = list.y + 20 + k as i32 * row_h - off;
            if y > list.bottom() {
                break;
            }
            let r = Rect::new(M, y, W - 2 * M, row_h - 12);
            if let Some(f) = row_acts {
                let act = f(k);
                if self.layout_pass {
                    if let Some(vis) = r.intersect(&list) {
                        self.hits.push(Hit { r: vis, act, hold_ms: 0 });
                    }
                    continue;
                }
                cv.rrect(&r, 24, if self.is_pressed(&act) { PRESS } else { SURF });
            } else if self.layout_pass {
                continue;
            }
            let ta = self.fonts.ellipsize(Face::Bold, 36, a, r.w - 56);
            self.fonts.draw(cv, Face::Bold, 36, r.x + 28, r.y + if b.is_empty() { (row_h - 12 + 28) / 2 } else { 52 }, &ta, *col);
            if !b.is_empty() {
                let tb = self.fonts.ellipsize(Face::Regular, 30, b, r.w - 56);
                self.fonts.draw(cv, Face::Regular, 30, r.x + 28, r.y + 96, &tb, DIM);
            }
        }
        cv.clip = saved;
    }

    fn draw_files(&mut self, cv: &mut Canvas) {
        if let Some(v) = &self.viewer {
            let (name, is_img) = match v {
                Viewer::Text(n, _) => (n.clone(), false),
                Viewer::Image(n, ..) => (n.clone(), true),
            };
            let top = self.header(cv, &name, if is_img { "image" } else { "text preview (first 64 KB)" });
            if let Some(Viewer::Image(_, px, w, h)) = &self.viewer {
                let (w, h) = (*w, *h);
                let r = Rect::new((W - w) / 2, top + 20, w, h);
                if !self.layout_pass && r.hits(&cv.clip) {
                    let px = px.clone();
                    cv.image(&r, &px, w, h);
                }
                return;
            }
            let lines = match &self.viewer {
                Some(Viewer::Text(_, l)) => l.clone(),
                _ => vec![],
            };
            let list = Rect::new(0, top, W, H - EDGE_B - top);
            self.scroll_rect = Some(list);
            let i = Page::Files.idx();
            self.content_h[i] = lines.len() as i32 * 44 + 40;
            let maxs = (self.content_h[i] - list.h).max(0) as f32;
            self.scroll[i] = self.scroll[i].clamp(0.0, maxs);
            let off = self.scroll[i] as i32;
            if self.layout_pass {
                return;
            }
            let saved = cv.clip;
            if let Some(c) = saved.intersect(&list) {
                cv.clip = c;
                let first = (off / 44).max(0) as usize;
                for (k, l) in lines.iter().enumerate().skip(first) {
                    let y = list.y + 50 + k as i32 * 44 - off;
                    if y - 44 > list.bottom() {
                        break;
                    }
                    self.fonts.draw(cv, Face::Regular, 32, M + 8, y, l, 0xc9d1d9);
                }
                cv.clip = saved;
            }
            return;
        }
        let dir = self.files_dir.clone();
        let top = self.header(cv, "Files", &dir);
        let up = Rect::new(W - M - 170, STATUS_H + 24, 170, 84);
        if dir != "/" {
            self.button(cv, up, "Up", Act::FilesUp, SURF2, PRESS, 34);
        }
        let rows: Vec<(String, String, u32)> = self
            .files
            .iter()
            .map(|e| {
                if e.dir {
                    (format!("{}/", e.name), "folder".into(), 0x9ecbff)
                } else {
                    (e.name.clone(), human(e.size), TEXT)
                }
            })
            .collect();
        if rows.is_empty() && !self.layout_pass {
            self.txt(cv, Face::Regular, 34, M, top + 80, "(empty or unreadable)", DIM);
        }
        self.list_page(cv, top, &rows, Some(&|k| Act::File(k)), 140);
    }

    fn draw_phone(&mut self, cv: &mut Canvas) {
        let top = self.header(cv, "Phone", "read-only · calls and SMS stay with the gated agent tools");
        let r = Rect::new(W - M - 200, STATUS_H + 24, 200, 84);
        self.button(cv, r, "Refresh", Act::Refresh, SURF2, PRESS, 32);
        let mut rows: Vec<(String, String, u32)> = vec![];
        match &self.phone {
            None => rows.push(("Loading…".into(), "s22-phoned on :8095".into(), DIM)),
            Some((st, sms, calls)) => {
                let s = |v: &Value| match v {
                    Value::String(s) => s.clone(),
                    Value::Null => "?".into(),
                    o => o.to_string(),
                };
                if st["ok"] == false {
                    rows.push(("Modem unavailable".into(), s(&st["error"]), BAD));
                } else {
                    rows.push((format!("Modem {}", s(&st["modem_state"])), format!("SIM {}", s(&st["sim"])), GOOD));
                    let reg = &st["registration"];
                    rows.push((format!("Network {}", s(&reg["reg_status"])), format!("{} · VoLTE {}", s(&reg["act"]).to_uppercase(), s(&reg["volte"])), TEXT));
                }
                rows.push(("Messages".into(), String::new(), 0x9ecbff));
                let msgs = sms["messages"].as_array().cloned().unwrap_or_default();
                if msgs.is_empty() {
                    rows.push(("No messages".into(), String::new(), DIM));
                }
                for m in msgs.iter().take(20) {
                    rows.push((
                        format!("{}  ·  {}", m["from"].as_str().unwrap_or("?"), m["ts"].as_str().or(m["time"].as_str()).unwrap_or("")),
                        m["text"].as_str().unwrap_or("").replace('\n', " "),
                        TEXT,
                    ));
                }
                rows.push(("Calls".into(), String::new(), 0x9ecbff));
                let cs = calls["calls"].as_array().cloned().unwrap_or_default();
                if cs.is_empty() {
                    rows.push(("No calls".into(), String::new(), DIM));
                }
                for c in cs.iter().take(20) {
                    rows.push((
                        format!("{} {}", c["direction"].as_str().unwrap_or(""), c["number"].as_str().or(c["from"].as_str()).unwrap_or("?")),
                        format!("{} {}", c["state"].as_str().unwrap_or(""), c["ts"].as_str().or(c["time"].as_str()).unwrap_or("")),
                        TEXT,
                    ));
                }
            }
        }
        self.list_page(cv, top, &rows, None, 140);
    }

    fn draw_camera(&mut self, cv: &mut Canvas) {
        let sub = format!("{} · processed by s22-develop", self.camera_sensor);
        let top = self.header(cv, "Camera", &sub);
        let area = Rect::new(M, top + 10, W - 2 * M, 1400);
        if !self.layout_pass && area.hits(&cv.clip) {
            cv.rrect(&area, 28, SURF);
            match &self.camera_img {
                Some((px, w, h)) => {
                    let r = Rect::new(area.x + (area.w - w) / 2, area.y + (area.h - h) / 2, *w, *h);
                    let (px, w, h) = (px.clone(), *w, *h);
                    cv.image(&r, &px, w, h);
                }
                None => self.txt_center(cv, Face::Regular, 34, area, "No picture yet", DIM),
            }
        }
        let st = if self.camera_state.is_empty() { "Hold the button for 1 s to take a picture".to_string() } else { self.camera_state.clone() };
        if !self.layout_pass {
            self.txt(cv, Face::Regular, 32, M, area.bottom() + 60, &st, DIM);
        }
        let b = Rect::new(M, area.bottom() + 100, W - 2 * M, 150);
        let busy = self.camera_poll.is_some();
        self.button(cv, b, if busy { "Capturing…" } else { "Hold to capture" }, if busy { Act::Nothing } else { Act::Capture }, ACCENT, ACCENT_P, 40);
        if !busy {
            if let Some(h) = self.hits.last_mut().filter(|_| self.layout_pass) {
                h.hold_ms = 1000;
            }
        }
        // sensor choice: two half-width toggles under the shutter button
        let half = (W - 3 * M) / 2;
        for (i, (s, label)) in [("rear", "Rear"), ("front", "Front")].into_iter().enumerate() {
            let r = Rect::new(M + i as i32 * (half + M), b.bottom() + 30, half, 110);
            let on = self.camera_sensor == s;
            let act = if busy { Act::Nothing } else { Act::CamSensor(s) };
            self.button(cv, r, label, act, if on { SURF2 } else { SURF }, PRESS, 34);
            if on && !self.layout_pass && r.hits(&cv.clip) {
                cv.rrect(&Rect::new(r.x + r.w / 2 - 40, r.bottom() - 14, 80, 6), 3, ACCENT);
            }
        }
    }

    fn draw_settings(&mut self, cv: &mut Canvas) {
        let top = self.header(cv, "Settings", "");
        let mut y = top + 20;
        let dev = self.st.device.clone().unwrap_or(Value::Null);
        let netw = &dev["network"];
        let row = |a: &str| -> String { netw[a].as_str().unwrap_or("absent").to_string() };
        let wifi = format!("wlan0 {}  ·  usb {}  ·  tailscale {}", row("wlan0"), row("ecm0"), row("tailscale0"));
        // network card
        let r = Rect::new(M, y, W - 2 * M, 190);
        if !self.layout_pass && r.hits(&cv.clip) {
            cv.rrect(&r, 28, SURF);
            self.txt(cv, Face::Regular, 30, r.x + 28, r.y + 54, "Network", DIM);
            self.txt(cv, Face::Bold, 40, r.x + 28, r.y + 112, &self.st.net.clone(), TEXT);
            let w = self.fonts.ellipsize(Face::Regular, 28, &wifi, r.w - 56);
            self.txt(cv, Face::Regular, 28, r.x + 28, r.y + 160, &w, DIM);
        }
        y += 190 + M;
        // brightness
        let r = Rect::new(M, y, W - 2 * M, 270);
        let sl = self.slider_rect();
        debug_assert!(sl.y > y);
        if !self.layout_pass && r.hits(&cv.clip) {
            cv.rrect(&r, 28, SURF);
            let (b, m) = self.brightness;
            self.txt(cv, Face::Regular, 30, r.x + 28, r.y + 54, "Brightness", DIM);
            self.txt(cv, Face::Bold, 40, r.x + 28, r.y + 108, &format!("{}%", (b * 100 + m / 2) / m), TEXT);
            let track = Rect::new(sl.x, sl.y + sl.h / 2 - 12, sl.w, 24);
            cv.rrect(&track, 12, SURF2);
            let fw = (sl.w as f32 * b as f32 / m as f32) as i32;
            cv.rrect(&Rect::new(sl.x, track.y, fw.max(24), 24), 12, ACCENT);
            cv.circle(sl.x + fw, sl.y + sl.h / 2, if self.is_pressed(&Act::Bright) { 40 } else { 32 }, TEXT);
        }
        self.hit(Rect::new(sl.x - 30, sl.y, sl.w + 60, sl.h), Act::Bright);
        y += 270 + M;
        // model
        let r = Rect::new(M, y, W - 2 * M, 300);
        let cur = self.st.model.clone().unwrap_or_else(|| "?".into());
        if !self.layout_pass && r.hits(&cv.clip) {
            cv.rrect(&r, 28, SURF);
            self.txt(cv, Face::Regular, 30, r.x + 28, r.y + 54, "Agent model (hold to switch)", DIM);
            let c = self.fonts.ellipsize(Face::Bold, 40, &cur, r.w - 56);
            self.txt(cv, Face::Bold, 40, r.x + 28, r.y + 108, &c, TEXT);
        }
        let bw = (r.w - 3 * 28) / 2;
        let b1 = Rect::new(r.x + 28, r.y + 150, bw, 120);
        let b2 = Rect::new(r.x + 56 + bw, r.y + 150, bw, 120);
        self.button(cv, b1, "Luna", Act::Model("luna"), SURF2, PRESS, 36);
        self.hold_last(800);
        self.button(cv, b2, "Local (Gemma E2B)", Act::Model("local"), SURF2, PRESS, 36);
        self.hold_last(800);
        y += 300 + M;
        // screen + desktop
        let bw = (W - 3 * M) / 2;
        self.button(cv, Rect::new(M, y, bw, 140), "Screen off", Act::ScreenOff, SURF2, PRESS, 36);
        self.button(cv, Rect::new(2 * M + bw, y, bw, 140), "Sway desktop", Act::Sway, SURF2, PRESS, 36);
        y += 140 + M;
        let fmt = |n: u64| if n == 0 { "never".to_string() } else { format!("{n} s") };
        let (l1, l2) = (format!("Idle off: {}", fmt(self.idle_cfg.unlocked_s)), format!("Lock off: {}", fmt(self.idle_cfg.locked_s)));
        self.button(cv, Rect::new(M, y, bw, 140), &l1, Act::IdleCycle(false), SURF2, PRESS, 36);
        self.button(cv, Rect::new(2 * M + bw, y, bw, 140), &l2, Act::IdleCycle(true), SURF2, PRESS, 36);
        y += 140 + M;
        self.button(cv, Rect::new(M, y, W - 2 * M, 140), "Classic shell (Quickshell) — hold", Act::Classic, 0x3a2a14, 0x55401e, 36);
        self.hold_last(1200);
        y += 140 + M;
        y = self.draw_buttons_section(cv, y);
        if !self.layout_pass && Rect::new(0, y, W, 400).hits(&cv.clip) {
            let note = self.note.clone();
            if !note.is_empty() {
                self.txt(cv, Face::Bold, 32, M, y + 40, &note, 0xf2cc60);
            }
            let p = self.perf_json();
            let line = format!(
                "fastshell · tap→flip p50 {} ms (n {}) · page p50 {} ms · render p50 {} ms",
                p["touch_to_flip"]["press"]["p50_ms"], p["touch_to_flip"]["press"]["n"], p["touch_to_flip"]["page"]["p50_ms"], p["render_ms"]["p50_ms"]
            );
            let lines = self.fonts.wrap(Face::Regular, 28, &line, W - 2 * M);
            for (k, l) in lines.iter().enumerate() {
                self.txt(cv, Face::Regular, 28, M, y + 80 + k as i32 * 36, l, DIM);
            }
        }
    }

    /// Settings → Buttons: every physical-button binding (buttons::BINDINGS) and
    /// the hold-toggle that hands the keys back to s22-buttons. Returns the next y.
    fn draw_buttons_section(&mut self, cv: &mut Canvas, y: i32) -> i32 {
        let rows = crate::buttons::BINDINGS;
        let row_h = 40;
        let h = 84 + rows.len() as i32 * row_h + 16;
        let r = Rect::new(M, y, W - 2 * M, h);
        let tog = Rect::new(r.right() - 330, r.y + 14, 302, 64);
        if !self.layout_pass && r.hits(&cv.clip) {
            cv.rrect(&r, 28, SURF);
            self.txt(cv, Face::Bold, 34, r.x + 28, r.y + 58, "Buttons", TEXT);
            let on = self.buttons_enabled;
            cv.rrect(&tog, 20, if self.is_pressed(&Act::ButtonsToggle) { PRESS } else if on { ACCENT_P } else { SURF2 });
            self.txt_center(cv, Face::Regular, 26, tog, if on { "shell · hold: off" } else { "s22-buttons · hold: on" }, TEXT);
            let mut yy = r.y + 84 + 28;
            for (k, v) in rows.iter() {
                self.txt(cv, Face::Bold, 27, r.x + 28, yy, k, TEXT);
                let vv = self.fonts.ellipsize(Face::Regular, 27, v, r.w - 420);
                self.txt(cv, Face::Regular, 27, r.x + 390, yy, &vv, DIM);
                yy += row_h;
            }
        }
        self.hit(tog, Act::ButtonsToggle);
        self.hold_last(800);
        y + h + M / 2
    }

    fn hold_last(&mut self, ms: u32) {
        if self.layout_pass {
            if let Some(h) = self.hits.last_mut() {
                h.hold_ms = ms;
            }
        }
    }

    fn draw_switcher(&mut self, cv: &mut Canvas) {
        let top = self.header(cv, "Apps", "switch page, or show the sway desktop (terminal, other windows)");
        let items: [(&str, &str, Act); 8] = [
            ("Home", "cards and tiles", Act::Open(Page::Home)),
            ("Agent", "chat with OpenUnum", Act::Open(Page::Chat)),
            ("Phone", "modem, SMS, calls (read-only)", Act::Open(Page::Phone)),
            ("Camera", "rear still", Act::Open(Page::Camera)),
            ("Files", "browse /root", Act::Open(Page::Files)),
            ("Settings", "brightness, network, model", Act::Open(Page::Settings)),
            ("Terminal", "foot under sway", Act::Terminal),
            ("Sway desktop", "whatever runs under sway", Act::Sway),
        ];
        let mut y = top + 20;
        for (a, b, act) in items.iter() {
            let r = Rect::new(M, y, W - 2 * M, 150);
            if !self.layout_pass && r.hits(&cv.clip) {
                cv.rrect(&r, 28, if self.is_pressed(act) { PRESS } else { SURF });
                self.txt(cv, Face::Bold, 40, r.x + 32, r.y + 66, a, TEXT);
                self.txt(cv, Face::Regular, 30, r.x + 32, r.y + 116, b, DIM);
            }
            self.hit(r, act.clone());
            y += 150 + 20;
        }
    }

    fn cards_rect(&self) -> Rect {
        Rect::new(0, STATUS_H, W, 20 + self.cards.len() as i32 * 230)
    }

    fn draw_cards(&mut self, cv: &mut Canvas) {
        let mut y = STATUS_H + 16;
        let cards: Vec<(u32, String, String)> = self.cards.iter().map(|c| (c.id, c.title.clone(), c.body.clone())).collect();
        for (id, title, body) in cards {
            let bl = self.fonts.wrap(Face::Regular, 32, &body, W - 4 * M - 40);
            let n = bl.len().min(3) as i32;
            let h = 96 + n * 44;
            let r = Rect::new(M, y, W - 2 * M, h);
            if !self.layout_pass && r.hits(&cv.clip) {
                cv.rrect(&Rect::new(r.x - 2, r.y - 2, r.w + 4, r.h + 4), 30, 0x3b4656);
                cv.rrect(&r, 28, if self.is_pressed(&Act::Card(id)) { PRESS } else { 0x1b2533 });
                let t = self.fonts.ellipsize(Face::Bold, 36, &title, r.w - 120);
                self.fonts.draw(cv, Face::Bold, 36, r.x + 32, r.y + 58, &t, TEXT);
                self.fonts.draw(cv, Face::Regular, 40, r.x + r.w - 60, r.y + 58, "×", DIM);
                for (k, l) in bl.iter().take(3).enumerate() {
                    self.fonts.draw(cv, Face::Regular, 32, r.x + 32, r.y + 104 + k as i32 * 44, l, 0xc9d1d9);
                }
            }
            self.hit(r, Act::Card(id));
            y += h + 14;
        }
    }

    fn draw_confirm(&mut self, cv: &mut Canvas) {
        let Some(c) = &self.confirm else { return };
        let q = c.question.clone();
        let left = c.until.saturating_duration_since(Instant::now()).as_secs();
        if !self.layout_pass {
            let full = Rect::new(0, 0, W, H);
            cv.fill_alpha(&full, 0x000000, 170);
        }
        let lines = self.fonts.wrap(Face::Regular, 40, &q, W - 4 * M - 64);
        let n = lines.len().min(10) as i32;
        let h = 150 + n * 56 + 200;
        let r = Rect::new(M, (H - h) / 2, W - 2 * M, h);
        if !self.layout_pass && r.hits(&cv.clip) {
            cv.rrect(&r, 36, 0x1b2533);
            self.txt(cv, Face::Bold, 44, r.x + 40, r.y + 80, "The agent asks", 0xf2cc60);
            let cd = format!("{left} s");
            let w = self.fonts.measure(Face::Regular, 32, &cd);
            self.txt(cv, Face::Regular, 32, r.right() - 40 - w, r.y + 78, &cd, DIM);
            for (k, l) in lines.iter().take(10).enumerate() {
                self.txt(cv, Face::Regular, 40, r.x + 40, r.y + 150 + k as i32 * 56, l, TEXT);
            }
        }
        let bw = (r.w - 3 * 40) / 2;
        let by = r.bottom() - 170;
        self.button(cv, Rect::new(r.x + 40, by, bw, 130), "No", Act::ConfirmNo, SURF2, PRESS, 42);
        self.button(cv, Rect::new(r.x + 80 + bw, by, bw, 130), "Yes", Act::ConfirmYes, 0x238636, 0x1a6b2a, 42);
        if self.layout_pass {
            // modal: only its own buttons are live
            let keep: Vec<Hit> = self.hits.iter().filter(|h| matches!(h.act, Act::ConfirmYes | Act::ConfirmNo)).cloned().collect();
            self.hits = keep;
        }
    }

    fn draw_lock(&mut self, cv: &mut Canvas) {
        if self.layout_pass {
            return;
        }
        cv.fill(&Rect::new(0, 0, W, H), 0x05070a);
        let clock = self.clock.clone();
        let date = self.date.clone();
        let w = self.fonts.measure(Face::Bold, 200, &clock);
        self.txt(cv, Face::Bold, 200, (W - w) / 2, 700, &clock, TEXT);
        let w = self.fonts.measure(Face::Regular, 44, &date);
        self.txt(cv, Face::Regular, 44, (W - w) / 2, 790, &date, DIM);
        let info = format!(
            "{}  ·  {}  ·  {}",
            self.st.battery.map(|b| format!("{b}% {}", self.st.bat_state)).unwrap_or_default(),
            self.st.model.clone().unwrap_or_default(),
            self.st.health.clone().unwrap_or_default()
        );
        let info = self.fonts.ellipsize(Face::Regular, 34, &info, W - 2 * M);
        let w = self.fonts.measure(Face::Regular, 34, &info);
        self.txt(cv, Face::Regular, 34, (W - w) / 2, 880, &info, DIM);
        let mut y = 1050;
        if !self.cards.is_empty() || self.prompt.is_some() {
            let s = format!("{} card(s){}", self.cards.len(), if self.prompt.is_some() { " · the agent has a question" } else { "" });
            let w = self.fonts.measure(Face::Bold, 36, &s);
            self.txt(cv, Face::Bold, 36, (W - w) / 2, y, &s, 0xf2cc60);
            y += 70;
            let titles: Vec<String> = self.cards.iter().map(|c| c.title.clone()).collect();
            for t in titles.iter().take(3) {
                let t = self.fonts.ellipsize(Face::Regular, 32, t, W - 4 * M);
                let w = self.fonts.measure(Face::Regular, 32, &t);
                self.txt(cv, Face::Regular, 32, (W - w) / 2, y, &t, TEXT);
                y += 50;
            }
        }
        let s = "Swipe up to unlock";
        let w = self.fonts.measure(Face::Regular, 36, s);
        self.txt(cv, Face::Regular, 36, (W - w) / 2, H - 200, s, DIM);
        cv.rrect(&Rect::new(W / 2 - 80, H - 150, 160, 10), 5, 0x3a4350);
    }

    fn draw_kbd(&mut self, cv: &mut Canvas) {
        let kr = self.kb_rect();
        if !self.layout_pass && kr.hits(&cv.clip) {
            cv.fill(&kr, KB_BG);
        }
        let rows = kbd::rows(self.kb.layout);
        let unit = (W - 12) as f32 / 10.0;
        let rh = 148;
        for (ri, row) in rows.iter().enumerate() {
            let total: f32 = row.iter().map(|k| kbd::units(k)).sum();
            let mut x = 6.0 + (10.0 - total) * unit / 2.0;
            let y = kr.y + 18 + ri as i32 * rh;
            for (ci, k) in row.iter().enumerate() {
                let w = kbd::units(k) * unit;
                let r = Rect::new(x as i32 + 6, y, w as i32 - 12, rh - 16);
                let act = Act::Key(ri, ci);
                if !self.layout_pass && r.hits(&cv.clip) {
                    let p = self.is_pressed(&act);
                    let on = (*k == kbd::SHIFT && self.kb.shift) || (*k == kbd::TONOS && self.kb.tonos);
                    let fill = if p || on { 0x4a5a70 } else if kbd::is_special(k) { KEY_SP } else { KEY };
                    cv.rrect(&r, 16, if *k == kbd::ENTER { if p { ACCENT_P } else { ACCENT } } else { fill });
                    let lbl = self.kb.label(k);
                    let px = if lbl.chars().count() > 1 { 32 } else { 48 };
                    self.txt_center(cv, if px == 48 { Face::Regular } else { Face::Bold }, px, r, &lbl, TEXT);
                }
                // generous hit area: the full cell
                let cell = Rect::new(x as i32, y - 8, w as i32, rh);
                self.hit(cell, act);
                x += w;
            }
        }
    }
}

/// A microphone icon centred at (cx, cy).
fn mic(cv: &mut Canvas, cx: i32, cy: i32, fg: u32, bg: u32) {
    cv.rrect(&Rect::new(cx - 15, cy - 34, 30, 50), 15, fg);
    cv.rrect(&Rect::new(cx - 25, cy - 8, 50, 30), 15, fg);
    cv.rrect(&Rect::new(cx - 19, cy - 14, 38, 30), 13, bg);
    cv.rrect(&Rect::new(cx - 15, cy - 34, 30, 46), 15, fg);
    cv.fill(&Rect::new(cx - 3, cy + 20, 6, 14), fg);
    cv.fill(&Rect::new(cx - 14, cy + 32, 28, 5), fg);
}

pub fn human(n: u64) -> String {
    if n >= 1 << 30 {
        format!("{:.1} GB", n as f64 / (1u64 << 30) as f64)
    } else if n >= 1 << 20 {
        format!("{:.1} MB", n as f64 / (1u64 << 20) as f64)
    } else if n >= 1 << 10 {
        format!("{:.1} KB", n as f64 / 1024.0)
    } else {
        format!("{n} B")
    }
}

/// Decode a PNG and scale it (nearest) to fit max_w x max_h. XRGB out.
/// One line for the Camera page from touchd's camera.json.
pub fn camera_summary(v: &Value) -> String {
    let s = v["sensor"].as_str().unwrap_or("rear");
    let ms = v["exposure_us"].as_f64().map(|e| format!("{:.0} ms", e / 1000.0)).unwrap_or_default();
    let g = v["gain"].as_f64().map(|g| format!("{g:.1}x")).unwrap_or_default();
    if v["develop_ok"] == true {
        let n = v["stacked"].as_u64().unwrap_or(1);
        let f = v["jpeg"].as_str().and_then(|p| p.rsplit('/').next()).unwrap_or("");
        format!("{s} · {ms} · {g} · {n} frames stacked · {f}")
    } else if v["develop_ok"] == false {
        format!("{s} · raw preview only ({})", v["develop_error"].as_str().unwrap_or("develop failed"))
    } else {
        "captured".into()
    }
}

pub fn load_png(path: &str, max_w: i32, max_h: i32) -> Option<(Vec<u32>, i32, i32)> {
    let f = std::fs::File::open(path).ok()?;
    let mut dec = png::Decoder::new(f);
    dec.set_transformations(png::Transformations::EXPAND | png::Transformations::STRIP_16);
    let mut r = dec.read_info().ok()?;
    let (iw, ih) = (r.info().width as i32, r.info().height as i32);
    if iw as i64 * ih as i64 > 40_000_000 {
        return None;
    }
    let mut buf = vec![0; r.output_buffer_size()];
    let info = r.next_frame(&mut buf).ok()?;
    let ch = match info.color_type {
        png::ColorType::Rgb => 3,
        png::ColorType::Rgba => 4,
        png::ColorType::Grayscale => 1,
        png::ColorType::GrayscaleAlpha => 2,
        _ => return None,
    };
    let s = (max_w as f32 / iw as f32).min(max_h as f32 / ih as f32).min(1.0);
    let (w, h) = (((iw as f32 * s) as i32).max(1), ((ih as f32 * s) as i32).max(1));
    let mut out = vec![0u32; (w * h) as usize];
    let stride = info.line_size;
    for y in 0..h {
        let sy = (y as i64 * ih as i64 / h as i64) as usize;
        for x in 0..w {
            let sx = (x as i64 * iw as i64 / w as i64) as usize;
            let o = sy * stride + sx * ch;
            let (r8, g8, b8) = if ch >= 3 { (buf[o], buf[o + 1], buf[o + 2]) } else { (buf[o], buf[o], buf[o]) };
            out[(y * w + x) as usize] = (r8 as u32) << 16 | (g8 as u32) << 8 | b8 as u32;
        }
    }
    Some((out, w, h))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ctl::Call;

    fn call(a: &mut App, f: &str, args: &[&str]) -> String {
        a.call(&Call { function: f.into(), args: args.iter().map(|s| s.to_string()).collect() })
    }

    fn app(dir: &str) -> App {
        let mut a = App::new(None, dir, "");
        a.active = true;
        a
    }

    #[test]
    fn touchd_protocol_pages_lock_and_cards() {
        let mut a = app("/nonexistent");
        assert_eq!(call(&mut a, "open", &["settings"]), "ok");
        assert_eq!(a.page, Page::Settings);
        assert_eq!(call(&mut a, "back", &[]), "grid");
        assert_eq!(a.page, Page::Home);
        assert!(call(&mut a, "open", &["nope"]).starts_with("unknown app"));
        assert_eq!(call(&mut a, "card", &["T", "B", "8"]), "card 1");
        assert_eq!(call(&mut a, "lock", &[]), "locked");
        assert_eq!(call(&mut a, "open", &["chat"]), "locked");
        assert_eq!(call(&mut a, "home", &[]), "unlocked");
        assert_eq!(call(&mut a, "gesture", &["home"]), "handled-natively");
        a.active = false;
        assert_eq!(call(&mut a, "gesture", &["home"]), "activated");
        assert!(a.effects.contains(&Effect::Activate));
        assert_eq!(call(&mut a, "display", &["off"]), "delegate");
        let v: serde_json::Value = serde_json::from_str(&call(&mut a, "state", &[])).unwrap();
        assert_eq!(v["backend"], "fastshell");
    }

    #[test]
    fn confirm_answer_is_written_in_touchd_format() {
        let dir = std::env::temp_dir().join(format!("fastshell-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let mut a = app(dir.to_str().unwrap());
        assert_eq!(call(&mut a, "confirm", &["bad id!", "q"]), "bad id");
        assert_eq!(call(&mut a, "confirm", &["c1", "Proceed?", "30"]), "asking");
        assert_eq!(call(&mut a, "confirm", &["c2", "again?", "30"]), "busy");
        assert_eq!(call(&mut a, "confirmResult", &["c1"]), "pending");
        a.layout();
        let yes = a.hits.iter().find(|h| h.act == Act::ConfirmYes).expect("yes button").r;
        assert_eq!(a.hits.len(), 2, "the dialog is modal");
        a.touch(crate::touch::Touch::Down(yes.x + 10, yes.y + 10, 1));
        a.touch(crate::touch::Touch::Up(yes.x + 10, yes.y + 10, 2));
        assert_eq!(call(&mut a, "confirmResult", &["c1"]), "done");
        let v: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(dir.join("confirm/c1.json")).unwrap()).unwrap();
        assert_eq!(v["answer"], "yes");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn greek_keyboard_tonos_and_shift() {
        let mut k = Kbd::default();
        k.press(kbd::LANG);
        assert!(matches!(k.press(kbd::TONOS), KeyOut::Redraw));
        match k.press("α") {
            KeyOut::Text(t) => assert_eq!(t, "ά"),
            _ => panic!(),
        }
        k.press(kbd::SHIFT);
        match k.press("ω") {
            KeyOut::Text(t) => assert_eq!(t, "Ω"),
            _ => panic!(),
        }
    }

    #[test]
    fn edge_swipe_up_goes_home_and_unlocks() {
        let mut a = app("/nonexistent");
        a.goto(Page::Files);
        a.layout();
        a.touch(crate::touch::Touch::Down(540, H - 20, 0));
        a.touch(crate::touch::Touch::Move(540, H - 400, 100_000));
        a.touch(crate::touch::Touch::Up(540, H - 500, 200_000));
        assert_eq!(a.page, Page::Home);
        a.locked = true;
        a.touch(crate::touch::Touch::Down(540, 1800, 0));
        a.touch(crate::touch::Touch::Up(540, 1100, 300_000));
        assert!(!a.locked);
    }

    #[test]
    fn radio_badge_5g_nsa_and_lte() {
        let base = json!({"ok": true, "registration": {"reg_status": "home_3gpp", "act": "lte"}, "radio": {"nr_nsa": false}, "nr_last_seen": null});
        assert_eq!(net::radio_badge(&base, 1000.0), Some("4G"));
        let mut v = base.clone();
        v["radio"]["nr_nsa"] = json!(true);
        assert_eq!(net::radio_badge(&v, 1000.0), Some("5G"));
        let mut v = base.clone();
        v["nr_last_seen"] = json!(950.0);
        assert_eq!(net::radio_badge(&v, 1000.0), Some("5G"), "NR seen 50 s ago");
        assert_eq!(net::radio_badge(&v, 1200.0), Some("4G"), "NR seen 250 s ago is stale");
        let mut v = base.clone();
        v["registration"]["reg_status"] = json!("searching");
        assert_eq!(net::radio_badge(&v, 1000.0), None);
        assert_eq!(net::radio_badge(&json!({"ok": false}), 1000.0), None);
        assert_eq!(net::radio_badge(&Value::Null, 1000.0), None);
    }

    #[test]
    fn camera_summary_lines() {
        let v = json!({"rc": 0, "sensor": "front", "exposure_us": 30000, "gain": 5.5, "develop_ok": true, "stacked": 4,
                       "jpeg": "/mnt/omarchy-trial/root/.openunum/assets/generated/cam-1-front.jpg"});
        assert_eq!(camera_summary(&v), "front · 30 ms · 5.5x · 4 frames stacked · cam-1-front.jpg");
        let v = json!({"rc": 0, "sensor": "rear", "exposure_us": 20000, "gain": 8.0, "develop_ok": false, "develop_error": "no s22-develop"});
        assert!(camera_summary(&v).contains("raw preview only (no s22-develop)"));
        assert_eq!(camera_summary(&json!({"rc": 0})), "captured");
    }

    #[test]
    fn button_actions_level_overlay_lock_and_wake() {
        use crate::buttons::Action as B;
        let mut a = app("/nonexistent");
        a.level = 4;
        a.damage.clear();
        a.button_actions(&[B::Step(1)], 1000);
        assert_eq!(a.level, 5);
        assert_eq!(a.effects, vec![Effect::Level(5)]);
        assert_eq!(a.osd.as_ref().map(|o| (o.title.as_str(), o.level)), Some(("Volume 5", Some(5))));
        assert_eq!(a.mark, Some((1000, "button")), "the overlay frame is timed from the key event");
        // hold: the step is undone, the overlay goes, and mute keeps the old level for unmute
        a.effects.clear();
        a.button_actions(&[B::Revert, B::Mute], 2000);
        assert_eq!(a.level, 0);
        assert_eq!(a.effects, vec![Effect::Level(4), Effect::Level(0)]);
        assert_eq!(a.osd.as_ref().unwrap().title, "Muted");
        a.button_actions(&[B::Mute], 3000);
        assert_eq!(a.level, 4, "unmute restores");
        for _ in 0..20 {
            a.button_actions(&[B::Step(1)], 4000);
        }
        assert_eq!(a.level, 10, "clamped");
        // the overlay expires
        let until = a.overlay_deadline().unwrap();
        a.expire_overlays(until);
        assert!(a.osd.is_none());
        // Power: lock + panel off; wake keeps the lock cover
        a.effects.clear();
        a.button_actions(&[B::Lock], 5000);
        assert!(a.locked);
        assert_eq!(a.effects, vec![Effect::Display(false)]);
        a.display_on = false;
        a.effects.clear();
        a.button_actions(&[B::Wake], 6000);
        assert_eq!(a.effects, vec![Effect::Display(true)]);
        assert!(a.locked);
        // sway has the panel: s22-display does it
        a.active = false;
        a.effects.clear();
        a.button_actions(&[B::Lock], 7000);
        assert_eq!(a.effects, vec![Effect::Native(vec!["/srv/s22/hardware/bin/s22-display".into(), "off".into()])]);
    }

    #[test]
    fn button_chords_camera_screenshot_keep_awake_and_ptt() {
        use crate::buttons::Action as B;
        let mut a = app("/nonexistent");
        a.locked = true;
        a.button_actions(&[B::Wake, B::Camera], 1);
        assert!(!a.locked && a.page == Page::Camera);
        a.goto(Page::Home);
        a.effects.clear();
        a.button_actions(&[B::Step(-1), B::Revert, B::Screenshot], 2);
        assert!(a.osd.is_none(), "no level overlay in the screenshot");
        assert_eq!(a.effects.last(), Some(&Effect::Screenshot));
        a.screenshot_taken("screenshot-1.png");
        assert!(a.flash_until.is_some());
        assert_eq!(a.files_dir, GALLERY);
        assert!(a.cards.iter().any(|c| c.title == "Screenshot saved"));
        a.expire_overlays(a.flash_until.unwrap());
        assert!(a.flash_until.is_none());
        // keep awake inhibits the screen timeout
        a.button_actions(&[B::KeepAwake], 3);
        assert_eq!(a.idle_inhibitor(), Some("keep screen on (Power + Volume up)"));
        a.button_actions(&[B::KeepAwake], 4);
        assert_eq!(a.idle_inhibitor(), None);
        // push-to-talk goes to the same hook with the same environment as s22-buttons
        a.effects.clear();
        a.button_actions(&[B::Revert, B::PttStart(1000)], 5);
        let Some(Effect::Assistant(cmd, env)) = a.effects.iter().find(|e| matches!(e, Effect::Assistant(..))).cloned() else { panic!("no assistant") };
        assert_eq!(cmd, "/srv/s22/buttons/hooks/assistant");
        assert!(env.contains(&("S22_PTT".into(), "hold".into())));
        assert!(env.contains(&("S22_PTT_STOP".into(), "/srv/s22/buttons/ptt-release".into())));
        assert_eq!(a.idle_inhibitor(), Some("voice turn"));
        a.button_actions(&[B::PttRelease(2500)], 6);
        assert_eq!(a.effects.last(), Some(&Effect::PttRelease(2500)));
    }

    #[test]
    fn settings_lists_every_button_binding_and_the_toggle() {
        let mut a = app("/nonexistent");
        a.goto(Page::Settings);
        a.layout();
        let tog = a.hits.iter().find(|h| h.act == Act::ButtonsToggle).expect("toggle").clone();
        assert_eq!(tog.hold_ms, 800);
        assert!(tog.r.bottom() < H - EDGE_B, "fits above the bottom edge");
        let d = std::env::temp_dir().join(format!("fs-buttons-{}.json", std::process::id()));
        a.buttons_cfg = d.to_string_lossy().into();
        a.activate(Act::ButtonsToggle, 0);
        assert!(!a.buttons_enabled);
        assert!(!crate::buttons::load_enabled(&a.buttons_cfg));
        a.activate(Act::ButtonsToggle, 0);
        assert!(crate::buttons::load_enabled(&a.buttons_cfg));
        let _ = std::fs::remove_file(&d);
        assert_eq!(crate::buttons::BINDINGS.len(), 9);
    }

    #[test]
    fn screen_timeout_policy_in_the_app() {
        use std::time::Duration;
        let mut a = app("/nonexistent");
        let t0 = Instant::now();
        a.last_touch = t0;
        let at = |s: u64| t0 + Duration::from_secs(s);
        assert!(!a.idle_check(at(59), false));
        assert!(a.idle_check(at(60), false), "unlocked idle 60 s");
        // a touch resets the countdown
        a.last_touch = at(50);
        assert!(!a.idle_check(at(100), false));
        assert!(a.idle_check(at(110), false));
        // locked: 15 s
        a.locked = true;
        a.last_touch = t0;
        assert!(!a.idle_check(at(14), false));
        assert!(a.idle_check(at(15), false));
        // s22-say lock holds it, and restarts the countdown
        assert!(!a.idle_check(at(100), true));
        assert!(!a.idle_check(at(110), false));
        assert!(a.idle_check(at(115), false));
        // each inhibitor
        a.last_touch = t0;
        a.busy = true;
        assert!(!a.idle_check(at(500), false));
        a.busy = false;
        a.goto(Page::Camera);
        assert_eq!(a.idle_inhibitor(), Some("camera"));
        a.goto(Page::Home);
        assert_eq!(call(&mut a, "confirm", &["c1", "ok?", "30"]), "asking");
        assert_eq!(a.idle_inhibitor(), Some("agent question"));
        // never
        a.confirm = None;
        a.idle_cfg = idle::Cfg { unlocked_s: 0, locked_s: 0 };
        a.last_touch = t0;
        assert!(!a.idle_check(at(100000), false));
        // power-off path
        a.screen_timeout_off();
        assert!(a.locked && a.effects.contains(&Effect::Display(false)));
    }

    #[test]
    fn touch_and_calls_reset_the_idle_clock_but_polls_do_not() {
        let mut a = app("/nonexistent");
        let old = Instant::now() - std::time::Duration::from_secs(50);
        a.last_touch = old;
        call(&mut a, "state", &[]);
        call(&mut a, "ping", &[]);
        assert_eq!(a.last_touch, old);
        a.touch(crate::touch::Touch::Down(500, 1000, 0));
        assert!(a.last_touch > old);
        a.last_touch = old;
        call(&mut a, "display", &["on"]);
        assert!(a.last_touch > old);
    }

    #[test]
    fn idle_cycle_button_saves_the_config() {
        let dir = std::env::temp_dir().join(format!("fastshell-idle-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let mut a = app("/nonexistent");
        a.idle_path = dir.join("screen-timeout.json").to_str().unwrap().into();
        a.activate(Act::IdleCycle(false), 0);
        assert_eq!(a.idle_cfg.unlocked_s, 120);
        let mut b = app("/nonexistent");
        b.idle_path = a.idle_path.clone();
        b.load_idle();
        assert_eq!(b.idle_cfg, a.idle_cfg);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
