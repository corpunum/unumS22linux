//! The iced application: wires the pure state in `model.rs` to the network, the touchd socket and the
//! screen. The decisions live in `model.rs`; this file only moves messages around.

use crate::ctl;
use crate::model::{Call, Effect, Line, Page, Shell, Who};
use crate::net::{self, AgentInfo, ChatMsg, Endpoints};
use crate::view;
use iced::futures::channel::oneshot;
use iced::futures::SinkExt;
use iced::widget::scrollable;
use iced::{executor, subscription, Application, Command, Element, Subscription, Theme};
use serde_json::Value;
use std::any::TypeId;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// One call from `s22-touchd` waiting for its answer.
#[derive(Debug, Clone)]
pub struct CtlRequest {
    pub call: Call,
    pub reply: Arc<Mutex<Option<oneshot::Sender<String>>>>,
}

#[derive(Debug, Clone)]
pub enum Message {
    Poll,
    ExpireTick,
    Status(Result<Value, String>),
    Agent(AgentInfo),
    Phone(Result<(Value, Option<Value>), String>),
    History(Result<Vec<Line>, String>),
    Navigate(Page),
    Input(String),
    Send,
    Stop,
    Chat(ChatMsg),
    DismissCard(u32),
    Confirm(bool),
    PromptAnswer(String),
    PromptSkip,
    BrightnessChanged(u8),
    BrightnessCommit,
    Done(Result<(), String>),
    Ctl(CtlRequest),
}

pub struct UnumShell {
    shell: Shell,
    ep: Endpoints,
    /// `POST /api/chat` answered 202: the reply will only come through the stream or the history.
    reply_pending: bool,
}

fn local_time() -> (u32, u32) {
    // SAFETY: localtime_r writes only into the tm we pass; time(NULL) is always valid.
    unsafe {
        let t = libc::time(std::ptr::null_mut());
        let mut tm: libc::tm = std::mem::zeroed();
        libc::localtime_r(&t, &mut tm);
        (tm.tm_hour as u32, tm.tm_min as u32)
    }
}

/// squeekboard's visibility switch, the same call the Quickshell shell makes.
fn set_keyboard(visible: bool) {
    if std::env::var_os("DBUS_SESSION_BUS_ADDRESS").is_none() {
        return;
    }
    std::thread::spawn(move || {
        let _ = std::process::Command::new("gdbus")
            .args([
                "call",
                "--session",
                "--dest",
                "sm.puri.OSK0",
                "--object-path",
                "/sm/puri/OSK0",
            ])
            .args([
                "--method",
                "sm.puri.OSK0.SetVisible",
                if visible { "true" } else { "false" },
            ])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status();
    });
}

impl UnumShell {
    fn snap_chat(&self) -> Command<Message> {
        if self.shell.page == Page::Chat {
            scrollable::snap_to(view::chat_scroll_id(), scrollable::RelativeOffset::END)
        } else {
            Command::none()
        }
    }

    fn poll(&self) -> Command<Message> {
        let ep = self.ep.clone();
        let mut cmds = vec![Command::perform(
            net::fetch_agent(ep.clone()),
            Message::Agent,
        )];
        // A switched-off screen does not need fresh numbers.
        if self.shell.strip.display.as_deref() != Some("off") {
            cmds.push(Command::perform(
                net::fetch_status(ep.clone()),
                Message::Status,
            ));
        }
        if self.shell.page == Page::Phone {
            cmds.push(Command::perform(net::fetch_phone(ep), Message::Phone));
        }
        Command::batch(cmds)
    }

    fn run_effects(&mut self, effects: Vec<Effect>) -> Command<Message> {
        let mut cmds = vec![];
        for e in effects {
            match e {
                Effect::Keyboard(on) => set_keyboard(on),
                Effect::Refresh => cmds.push(self.poll()),
                Effect::WriteConfirm { id, answer } => {
                    if let Err(err) = ctl::write_confirm(&self.ep.confirm_dir(), &id, answer) {
                        self.shell.note = format!("could not record the answer: {err}");
                    }
                }
            }
        }
        Command::batch(cmds)
    }

    fn clock(&mut self) {
        let (h, m) = local_time();
        self.shell.hour = h;
        self.shell.clock = format!("{h:02}:{m:02}");
    }
}

impl Application for UnumShell {
    type Executor = executor::Default;
    type Message = Message;
    type Theme = Theme;
    type Flags = Endpoints;

    fn new(ep: Endpoints) -> (Self, Command<Message>) {
        let mut app = Self {
            shell: Shell::default(),
            ep: ep.clone(),
            reply_pending: false,
        };
        app.clock();
        let cmds = Command::batch([
            app.poll(),
            Command::perform(net::fetch_history(ep), Message::History),
        ]);
        (app, cmds)
    }

    fn title(&self) -> String {
        "unum-shell".into()
    }

    fn theme(&self) -> Theme {
        Theme::Dark
    }

    fn subscription(&self) -> Subscription<Message> {
        let mut subs = vec![iced::time::every(Duration::from_secs(15)).map(|_| Message::Poll)];
        if self.shell.needs_ticks() {
            subs.push(iced::time::every(Duration::from_secs(1)).map(|_| Message::ExpireTick));
        }
        let sock = self.ep.sock();
        subs.push(subscription::channel(
            (TypeId::of::<CtlRequest>(), sock.clone()),
            32,
            move |output| async move {
                let handler = move |call: Call| {
                    let mut out = output.clone();
                    async move {
                        let (tx, rx) = oneshot::channel();
                        let reply = Arc::new(Mutex::new(Some(tx)));
                        if out
                            .send(Message::Ctl(CtlRequest { call, reply }))
                            .await
                            .is_err()
                        {
                            return "shell is closing".to_string();
                        }
                        tokio::time::timeout(Duration::from_secs(5), rx)
                            .await
                            .ok()
                            .and_then(|r| r.ok())
                            .unwrap_or_else(|| "timeout".to_string())
                    }
                };
                if let Err(e) = ctl::serve(sock, handler).await {
                    eprintln!("unum-shell: control socket failed: {e}");
                }
                std::future::pending().await
            },
        ));
        Subscription::batch(subs)
    }

    fn update(&mut self, msg: Message) -> Command<Message> {
        match msg {
            Message::Poll => {
                self.clock();
                return self.poll();
            }
            Message::ExpireTick => {
                let fx = self.shell.tick(Instant::now());
                return self.run_effects(fx);
            }
            Message::Status(Ok(v)) => self.shell.strip.apply_status(&v),
            Message::Status(Err(e)) => {
                self.shell.strip.s22d_down();
                self.shell.note = e;
            }
            Message::Agent(info) => {
                self.shell.strip.model = info.model;
                self.shell.strip.healthy = info.healthy;
            }
            Message::Phone(Ok((modem, sms))) => self.shell.phone.apply(&modem, sms.as_ref()),
            Message::Phone(Err(e)) => self.shell.phone.error = Some(e),
            Message::History(Ok(lines)) => {
                if self.shell.chat.busy {
                    // The turn finished while only the history could tell us the answer.
                    let last = lines
                        .iter()
                        .rev()
                        .find(|l| l.who == Who::Agent)
                        .map(|l| l.text.clone());
                    self.shell.chat.finish(last);
                } else if !lines.is_empty() {
                    self.shell.chat.lines = lines;
                }
                return self.snap_chat();
            }
            Message::History(Err(e)) => self.shell.note = e,
            Message::Navigate(p) => {
                let fx = self.shell.goto(p);
                let mut cmds = vec![self.run_effects(fx), self.snap_chat()];
                if p == Page::Phone {
                    cmds.push(Command::perform(
                        net::fetch_phone(self.ep.clone()),
                        Message::Phone,
                    ));
                }
                return Command::batch(cmds);
            }
            Message::Input(s) => self.shell.input = s,
            Message::Send => {
                let prompt = self.shell.input.trim().to_string();
                if prompt.is_empty() || self.shell.chat.busy {
                    return Command::none();
                }
                self.shell.input.clear();
                self.shell.chat.start_turn(&prompt);
                self.reply_pending = false;
                let stream = net::chat_turn(self.ep.clone(), prompt);
                return Command::batch([Command::run(stream, Message::Chat), self.snap_chat()]);
            }
            Message::Stop => {
                self.shell.chat.activity = Some("stopping".into());
                return Command::perform(net::cancel_turn(self.ep.clone()), Message::Done);
            }
            Message::Chat(m) => return self.chat_message(m),
            Message::DismissCard(id) => self.shell.dismiss_card(id),
            Message::Confirm(yes) => {
                let fx = self.shell.answer_confirm(if yes { "yes" } else { "no" });
                return self.run_effects(fx);
            }
            Message::PromptAnswer(value) => return self.answer_prompt(value),
            Message::PromptSkip => return self.answer_prompt(String::new()),
            Message::BrightnessChanged(v) => self.shell.brightness = v,
            Message::BrightnessCommit => {
                return Command::perform(
                    net::set_brightness(self.ep.clone(), self.shell.brightness),
                    Message::Done,
                );
            }
            Message::Done(Ok(())) => self.shell.note.clear(),
            Message::Done(Err(e)) => self.shell.note = e,
            Message::Ctl(req) => {
                let (result, fx) = self.shell.call(&req.call, Instant::now());
                if let Some(tx) = req.reply.lock().ok().and_then(|mut g| g.take()) {
                    let _ = tx.send(result);
                }
                let mut cmds = vec![self.run_effects(fx), self.snap_chat()];
                if self.shell.page == Page::Phone {
                    cmds.push(Command::perform(
                        net::fetch_phone(self.ep.clone()),
                        Message::Phone,
                    ));
                }
                return Command::batch(cmds);
            }
        }
        Command::none()
    }

    fn view(&self) -> Element<'_, Message> {
        view::root(&self.shell)
    }
}

impl UnumShell {
    fn chat_message(&mut self, m: ChatMsg) -> Command<Message> {
        match m {
            ChatMsg::Event(ev) => {
                if let Some(p) = self.shell.chat.apply(ev) {
                    self.shell.set_prompt(p);
                    if self.shell.page != Page::Chat {
                        // The agent needs an answer while the owner is elsewhere: say so where they look.
                        self.shell.add_card(
                            "The agent has a question",
                            "Open Chat to answer.",
                            Duration::from_secs(30),
                            Instant::now(),
                        );
                    }
                }
            }
            ChatMsg::Reply(Ok(Some(reply))) => {
                self.shell.chat.finish(Some(reply));
                self.shell.prompt = None;
            }
            ChatMsg::Reply(Ok(None)) => self.reply_pending = true,
            ChatMsg::Reply(Err(e)) => {
                self.shell.chat.fail(&e);
                self.shell.prompt = None;
            }
            ChatMsg::StreamClosed(_) => {
                if self.shell.chat.busy && self.reply_pending {
                    return Command::perform(net::fetch_history(self.ep.clone()), Message::History);
                }
            }
        }
        self.snap_chat()
    }

    fn answer_prompt(&mut self, value: String) -> Command<Message> {
        match self.shell.prompt.take() {
            Some(p) => Command::perform(
                net::answer_prompt(self.ep.clone(), p.id, value),
                Message::Done,
            ),
            None => Command::none(),
        }
    }
}
