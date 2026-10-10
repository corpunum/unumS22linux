use iced::{
    alignment, executor, theme,
    widget::{button, column, container, row, scrollable, text, text_input},
    Application, Border, Command, Element, Length, Settings, Size, Subscription, Theme,
};
use serde::Deserialize;
use std::time::Duration;

const BG: iced::Color = iced::Color::from_rgb(0.035, 0.047, 0.067);
const PANEL: iced::Color = iced::Color::from_rgb(0.075, 0.094, 0.125);
const ACCENT: iced::Color = iced::Color::from_rgb(0.36, 0.82, 0.69);
const MUTED: iced::Color = iced::Color::from_rgb(0.56, 0.62, 0.70);
const API: &str = "http://127.0.0.1:18880";
const DEVICE: &str = "http://127.0.0.1:8766";

#[derive(Debug, Clone, Deserialize, Default)]
struct DeviceStatus {
    #[serde(default)]
    battery: Option<serde_json::Value>,
    #[serde(default)]
    thermal: Option<serde_json::Value>,
    #[serde(default)]
    network: Option<serde_json::Value>,
}
#[derive(Debug, Clone)]
enum Page {
    Home,
    Chat,
    Phone,
    Camera,
    Files,
    Settings,
}
#[derive(Debug, Clone)]
enum Message {
    Tick,
    Status(Result<DeviceStatus, String>),
    Navigate(Page),
    Input(String),
    Send,
    Sent(Result<String, String>),
    Focus,
    Noop,
}
#[derive(Debug, Clone)]
struct ChatLine {
    who: String,
    body: String,
}
struct Shell {
    page: Page,
    input: String,
    lines: Vec<ChatLine>,
    status: String,
    model: String,
    network: String,
    battery: String,
    temperature: String,
    busy: bool,
    note: String,
}

impl Application for Shell {
    type Executor = executor::Default;
    type Message = Message;
    type Theme = Theme;
    type Flags = ();
    fn new(_: ()) -> (Self, Command<Message>) {
        let s = Self {
            page: Page::Home,
            input: String::new(),
            lines: vec![ChatLine {
                who: "LUNA".into(),
                body: "Your agent, right here. What should we work on?".into(),
            }],
            status: "Connecting to OpenUnum…".into(),
            model: "Luna".into(),
            network: "—".into(),
            battery: "—".into(),
            temperature: "—".into(),
            busy: false,
            note: String::new(),
        };
        (s, Command::perform(fetch_status(), Message::Status))
    }
    fn title(&self) -> String {
        "unum-shell · OpenUnum".into()
    }
    fn theme(&self) -> Theme {
        Theme::Dark
    }
    fn subscription(&self) -> Subscription<Message> {
        iced::time::every(Duration::from_secs(15)).map(|_| Message::Tick)
    }
    fn update(&mut self, msg: Message) -> Command<Message> {
        match msg {
            Message::Tick => return Command::perform(fetch_status(), Message::Status),
            Message::Status(Ok(s)) => {
                self.status = "OPENUNUM · ONLINE".into();
                self.battery = value_label(s.battery, "—");
                self.temperature = value_label(s.thermal, "—");
                self.network = value_label(s.network, "—");
            }
            Message::Status(Err(e)) => {
                self.status = "DEVICE STATUS · OFFLINE".into();
                self.note = e;
            }
            Message::Navigate(p) => {
                self.page = p;
                self.note.clear();
            }
            Message::Input(s) => self.input = s,
            Message::Send => {
                let prompt = self.input.trim().to_string();
                if !prompt.is_empty() && !self.busy {
                    self.lines.push(ChatLine {
                        who: "YOU".into(),
                        body: prompt.clone(),
                    });
                    self.input.clear();
                    self.busy = true;
                    return Command::perform(send_chat(prompt), Message::Sent);
                }
            }
            Message::Sent(Ok(reply)) => {
                self.lines.push(ChatLine {
                    who: "LUNA".into(),
                    body: reply,
                });
                self.busy = false;
            }
            Message::Sent(Err(e)) => {
                self.lines.push(ChatLine {
                    who: "SYSTEM".into(),
                    body: format!("Message failed: {e}"),
                });
                self.busy = false;
            }
            Message::Focus | Message::Noop => {}
        }
        Command::none()
    }
    fn view(&self) -> Element<'_, Message> {
        let header = row![
            text("◉  UNUM").size(22).style(ACCENT),
            text("SHELL").size(13).style(MUTED),
            iced::widget::Space::with_width(Length::Fill),
            text("09:41").size(17)
        ]
        .spacing(8)
        .align_items(alignment::Alignment::Center);
        let status = container(
            row![
                dot(),
                column![
                    text(&self.status).size(12).style(ACCENT),
                    text(format!(
                        "{}   ·   {}   ·   {}",
                        self.battery, self.temperature, self.network
                    ))
                    .size(12)
                    .style(MUTED)
                ]
                .spacing(4),
                iced::widget::Space::with_width(Length::Fill),
                text("LUNA").size(13).style(ACCENT)
            ]
            .spacing(12)
            .align_items(alignment::Alignment::Center),
        )
        .padding(16)
        .width(Length::Fill)
        .style(panel_style());
        let body: Element<'_, Message> = match self.page {
            Page::Home => self.home_view(),
            Page::Chat => self.chat_view(),
            Page::Settings => self.settings_view(),
            Page::Phone => placeholder(
                "PHONE",
                "Calls and messaging are handled by OpenUnum's gated phone tools.",
            ),
            Page::Camera => placeholder(
                "CAMERA",
                "Camera control is not available in this shell MVP.",
            ),
            Page::Files => placeholder("FILES", "Browse files with the system file manager."),
        };
        let nav = row![
            nav_button("⌂", "HOME", Page::Home),
            nav_button("◉", "CHAT", Page::Chat),
            nav_button("▦", "SETTINGS", Page::Settings)
        ]
        .spacing(10)
        .width(Length::Fill);
        container(column![header, status, body, nav].spacing(14).padding(18))
            .width(Length::Fill)
            .height(Length::Fill)
            .style(iced::theme::Container::Custom(Box::new(RootStyle)))
            .into()
    }
}
impl Shell {
    fn home_view(&self) -> Element<'_, Message> {
        let greet = column![
            text("Good morning.").size(30),
            text("Your AI, on your terms.").size(15).style(MUTED)
        ]
        .spacing(5);
        let agent = container(
            column![
                row![
                    text("✦  AGENT DESK").size(13).style(ACCENT),
                    iced::widget::Space::with_width(Length::Fill),
                    text("READY").size(11).style(ACCENT)
                ],
                text("Luna is ready to help").size(22),
                text("Local connection · Private by default")
                    .size(13)
                    .style(MUTED),
                button(text("OPEN CHAT  →").size(14))
                    .on_press(Message::Navigate(Page::Chat))
                    .padding([12, 18])
                    .style(theme::Button::Primary)
            ]
            .spacing(12),
        )
        .padding(18)
        .width(Length::Fill)
        .style(panel_style());
        let tiles = row![
            tile("◉", "Chat", Page::Chat),
            tile("☎", "Phone", Page::Phone)
        ]
        .spacing(12);
        let tiles2 = row![
            tile("▧", "Camera", Page::Camera),
            tile("▤", "Files", Page::Files),
            tile("⚙", "Settings", Page::Settings)
        ]
        .spacing(12);
        column![
            greet,
            agent,
            text("QUICK ACCESS").size(12).style(MUTED),
            tiles,
            tiles2,
            iced::widget::Space::with_height(Length::Fill),
            text("AGENT CARDS  ·  No pending requests")
                .size(12)
                .style(MUTED)
        ]
        .spacing(14)
        .into()
    }
    fn chat_view(&self) -> Element<'_, Message> {
        let mut messages = column![].spacing(12).padding(4);
        for line in &self.lines {
            let c = if line.who == "YOU" { ACCENT } else { MUTED };
            messages = messages.push(
                container(column![
                    text(&line.who).size(11).style(c),
                    text(&line.body).size(16)
                ])
                .padding(14)
                .width(Length::Fill)
                .style(panel_style()),
            );
        }
        if self.busy {
            messages = messages.push(text("Luna is thinking…").size(14).style(ACCENT));
        }
        let composer = row![
            text_input("Message your agent…", &self.input)
                .on_input(Message::Input)
                .on_submit(Message::Send)
                .padding(14)
                .size(16),
            button(text("↑").size(24))
                .on_press(Message::Send)
                .padding([6, 18])
                .style(theme::Button::Primary)
        ]
        .spacing(8)
        .align_items(alignment::Alignment::Center);
        column![
            row![
                text("CHAT").size(21),
                iced::widget::Space::with_width(Length::Fill),
                text("VOICE  ·  SOON").size(11).style(MUTED)
            ],
            scrollable(messages).height(Length::Fill),
            composer
        ]
        .spacing(12)
        .into()
    }
    fn settings_view(&self) -> Element<'_, Message> {
        column![
            text("Settings").size(28),
            text("DEVICE STATUS").size(12).style(ACCENT),
            setting_row("Battery", &self.battery),
            setting_row("Thermal", &self.temperature),
            setting_row("Network", &self.network),
            setting_row("Agent model", &self.model),
            text("Status is read-only. Hardware controls stay with s22d.")
                .size(13)
                .style(MUTED),
            text(&self.note).size(12).style(MUTED)
        ]
        .spacing(12)
        .into()
    }
}
fn value_label(v: Option<serde_json::Value>, fallback: &str) -> String {
    v.map(|x| {
        if x.is_string() {
            x.as_str().unwrap_or(fallback).to_string()
        } else {
            x.to_string()
        }
    })
    .unwrap_or_else(|| fallback.into())
}
async fn fetch_status() -> Result<DeviceStatus, String> {
    reqwest::get(format!("{DEVICE}/v1/status"))
        .await
        .map_err(|e| e.to_string())?
        .error_for_status()
        .map_err(|e| e.to_string())?
        .json()
        .await
        .map_err(|e| e.to_string())
}
async fn send_chat(prompt: String) -> Result<String, String> {
    let client = reqwest::Client::new();
    let v: serde_json::Value = client
        .post(format!("{API}/api/sessions/touch/messages"))
        .json(&serde_json::json!({"message":prompt}))
        .send()
        .await
        .map_err(|e| e.to_string())?
        .error_for_status()
        .map_err(|e| e.to_string())?
        .json()
        .await
        .map_err(|e| e.to_string())?;
    Ok(v.get("response")
        .or_else(|| v.get("message"))
        .or_else(|| v.get("content"))
        .and_then(|x| x.as_str())
        .unwrap_or("Message sent. Waiting for the agent response endpoint contract.")
        .to_string())
}
fn dot() -> Element<'static, Message> {
    text("●").size(14).style(ACCENT).into()
}
fn tile<'a>(icon: &'a str, label: &'a str, page: Page) -> Element<'a, Message> {
    button(
        column![text(icon).size(26).style(ACCENT), text(label).size(14)]
            .spacing(8)
            .align_items(alignment::Alignment::Center)
            .width(Length::Fill),
    )
    .on_press(Message::Navigate(page))
    .padding(15)
    .width(Length::Fill)
    .style(theme::Button::Secondary)
    .into()
}
fn nav_button<'a>(icon: &'a str, label: &'a str, page: Page) -> Element<'a, Message> {
    button(
        column![text(icon).size(19), text(label).size(10)]
            .spacing(3)
            .align_items(alignment::Alignment::Center)
            .width(Length::Fill),
    )
    .on_press(Message::Navigate(page))
    .padding(8)
    .width(Length::Fill)
    .style(theme::Button::Text)
    .into()
}
fn setting_row<'a>(k: &'a str, v: &'a str) -> Element<'a, Message> {
    container(
        row![
            text(k).size(15),
            iced::widget::Space::with_width(Length::Fill),
            text(v).size(14).style(MUTED)
        ]
        .padding(14),
    )
    .width(Length::Fill)
    .style(panel_style())
    .into()
}
fn placeholder<'a>(title: &'a str, body: &'a str) -> Element<'a, Message> {
    column![text(title).size(26), text(body).size(15).style(MUTED)]
        .spacing(12)
        .into()
}
fn panel_style() -> iced::theme::Container {
    iced::theme::Container::Custom(Box::new(PanelStyle))
}
struct RootStyle;
impl container::StyleSheet for RootStyle {
    type Style = Theme;
    fn appearance(&self, _: &Theme) -> container::Appearance {
        container::Appearance {
            background: Some(BG.into()),
            text_color: Some(iced::Color::WHITE),
            ..Default::default()
        }
    }
}
struct PanelStyle;
impl container::StyleSheet for PanelStyle {
    type Style = Theme;
    fn appearance(&self, _: &Theme) -> container::Appearance {
        container::Appearance {
            background: Some(PANEL.into()),
            text_color: Some(iced::Color::WHITE),
            border: Border {
                radius: 14.0.into(),
                width: 1.0,
                color: iced::Color::from_rgb(0.13, 0.17, 0.22),
            },
            ..Default::default()
        }
    }
}
fn main() {
    let _ = DEVICE;
    let settings = Settings {
        window: iced::window::Settings {
            size: Size::new(430.0, 932.0),
            resizable: false,
            decorations: false,
            ..Default::default()
        },
        antialiasing: false,
        ..Default::default()
    };
    if let Err(e) = Shell::run(settings) {
        eprintln!("unum-shell: {e}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn status_unknown_fields_do_not_break_client() {
        let s: DeviceStatus = serde_json::from_str(
            r#"{"battery":{"percent":83},"thermal":{"cpu":42},"network":"wifi","future":true}"#,
        )
        .unwrap();
        assert_eq!(value_label(s.network, "—"), "wifi");
        assert_eq!(value_label(s.battery, "—"), "{\"percent\":83}");
    }
    #[test]
    fn missing_status_fields_are_graceful() {
        let s: DeviceStatus = serde_json::from_str("{}").unwrap();
        assert_eq!(value_label(s.battery, "—"), "—");
    }
}
