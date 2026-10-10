//! Everything drawn on screen. `root` is a pure function of the shell state, so the same code
//! draws the live window and the offscreen screenshots.

use crate::app::Message;
use crate::model::{Card, Chat, Confirm, Page, Prompt, Shell, Who};
use iced::widget::{button, column, container, row, scrollable, slider, text, text_input, Space};
use iced::{alignment, theme, Border, Color, Element, Length, Theme};

pub const BG: Color = Color::from_rgb(0.035, 0.047, 0.067);
pub const PANEL: Color = Color::from_rgb(0.075, 0.094, 0.125);
pub const ACCENT: Color = Color::from_rgb(0.36, 0.82, 0.69);
pub const MUTED: Color = Color::from_rgb(0.56, 0.62, 0.70);
pub const WARN: Color = Color::from_rgb(0.82, 0.60, 0.13);
pub const BAD: Color = Color::from_rgb(0.97, 0.32, 0.29);

pub fn chat_scroll_id() -> scrollable::Id {
    scrollable::Id::new("chat")
}

pub fn root(s: &Shell) -> Element<'_, Message> {
    let body: Element<'_, Message> = if let Some(c) = s.confirm.as_ref().filter(|_| !s.locked) {
        confirm_view(c)
    } else if s.locked {
        lock_view(s)
    } else {
        page_view(s)
    };
    container(body)
        .width(Length::Fill)
        .height(Length::Fill)
        .style(theme::Container::Custom(Box::new(RootStyle)))
        .into()
}

fn page_view(s: &Shell) -> Element<'_, Message> {
    let header = row![
        text("UNUM").size(22).style(ACCENT),
        text("SHELL").size(13).style(MUTED),
        Space::with_width(Length::Fill),
        text(&s.clock).size(20)
    ]
    .spacing(8)
    .align_items(alignment::Alignment::Center);

    let strip = container(
        row![
            text("*").size(14).style(strip_color(s)),
            column![
                text(s.strip.headline()).size(12).style(strip_color(s)),
                text(s.strip.line()).size(12).style(MUTED)
            ]
            .spacing(4),
            Space::with_width(Length::Fill),
            text(s.strip.model.clone().unwrap_or_else(|| "-".into()))
                .size(13)
                .style(ACCENT)
        ]
        .spacing(12)
        .align_items(alignment::Alignment::Center),
    )
    .padding(14)
    .width(Length::Fill)
    .style(panel());

    let body: Element<'_, Message> = match s.page {
        Page::Home => home_view(s),
        Page::Chat => chat_view(s),
        Page::Settings => settings_view(s),
        Page::Phone => phone_view(s),
        Page::Camera => info_page(
            "CAMERA",
            "Photos are taken by the agent through its camera_capture tool, one at a time, with a pause between captures. Ask it in Chat. A preview is not part of this shell yet.",
        ),
        Page::Files => info_page("FILES", "Browse files with the file manager. This shell does not have one yet."),
    };

    let nav = row![
        nav_button("Home", Page::Home, s.page),
        nav_button("Chat", Page::Chat, s.page),
        nav_button("Settings", Page::Settings, s.page)
    ]
    .spacing(10)
    .width(Length::Fill);

    let mut col = column![header, strip].spacing(12);
    for c in &s.cards {
        col = col.push(card_view(c));
    }
    col = col.push(body).push(nav);
    col.padding(18).height(Length::Fill).into()
}

fn strip_color(s: &Shell) -> Color {
    match (s.strip.s22d, s.strip.healthy) {
        (Some(false), _) | (_, Some(false)) => BAD,
        (_, Some(true)) => ACCENT,
        _ => WARN,
    }
}

fn greeting(hour: u32) -> &'static str {
    match hour {
        5..=11 => "Good morning.",
        12..=17 => "Good afternoon.",
        18..=22 => "Good evening.",
        _ => "Hello.",
    }
}

fn home_view(s: &Shell) -> Element<'_, Message> {
    let greet = column![
        text(greeting(s.hour)).size(30),
        text("Your AI, on your terms.").size(15).style(MUTED)
    ]
    .spacing(5);
    let (state, color) = if s.chat.busy {
        ("WORKING", WARN)
    } else {
        ("READY", ACCENT)
    };
    let agent = container(
        column![
            row![
                text("AGENT DESK").size(13).style(ACCENT),
                Space::with_width(Length::Fill),
                text(state).size(11).style(color)
            ],
            text(if s.chat.busy {
                "The agent is working"
            } else {
                "The agent is ready to help"
            })
            .size(22),
            text("Local connection. Private by default.")
                .size(13)
                .style(MUTED),
            button(text("OPEN CHAT  >").size(14))
                .on_press(Message::Navigate(Page::Chat))
                .padding([14, 18])
                .style(theme::Button::Primary)
        ]
        .spacing(12),
    )
    .padding(18)
    .width(Length::Fill)
    .style(panel());
    let tiles = row![
        tile("C", "Chat", Page::Chat),
        tile("P", "Phone", Page::Phone)
    ]
    .spacing(12);
    let tiles2 = row![
        tile("O", "Camera", Page::Camera),
        tile("F", "Files", Page::Files),
        tile("S", "Settings", Page::Settings)
    ]
    .spacing(12);
    let footer = if s.cards.is_empty() {
        "AGENT CARDS  |  No pending requests".to_string()
    } else {
        format!("AGENT CARDS  |  {} showing", s.cards.len())
    };
    column![
        greet,
        agent,
        text("QUICK ACCESS").size(12).style(MUTED),
        tiles,
        tiles2,
        Space::with_height(Length::Fill),
        text(footer).size(12).style(MUTED)
    ]
    .spacing(14)
    .into()
}

fn chat_view(s: &Shell) -> Element<'_, Message> {
    let mut messages = column![].spacing(12).padding(4);
    for line in &s.chat.lines {
        messages = messages.push(bubble(line.who, &line.text));
    }
    if s.chat.busy {
        if !s.chat.live.is_empty() {
            messages = messages.push(bubble(Who::Agent, &s.chat.live));
        }
        if let Some(a) = &s.chat.activity {
            messages = messages.push(text(format!("The agent is {a}...")).size(14).style(ACCENT));
        }
    }
    let send: Element<'_, Message> = if s.chat.busy {
        button(text("Stop").size(18))
            .on_press(Message::Stop)
            .padding([14, 18])
            .style(theme::Button::Destructive)
            .into()
    } else {
        button(text("Send").size(18))
            .on_press(Message::Send)
            .padding([14, 18])
            .style(theme::Button::Primary)
            .into()
    };
    let composer = row![
        text_input("Message your agent...", &s.input)
            .on_input(Message::Input)
            .on_submit(Message::Send)
            .padding(14)
            .size(16),
        send
    ]
    .spacing(8)
    .align_items(alignment::Alignment::Center);
    let mut col = column![
        row![
            text("CHAT").size(21),
            Space::with_width(Length::Fill),
            text(session_hint(&s.chat)).size(11).style(MUTED)
        ],
        scrollable(messages)
            .id(chat_scroll_id())
            .height(Length::Fill)
    ]
    .spacing(12);
    if let Some(p) = &s.prompt {
        col = col.push(prompt_view(p));
    }
    col.push(composer).into()
}

fn session_hint(c: &Chat) -> &'static str {
    if c.busy {
        "STREAMING"
    } else {
        "SESSION touch"
    }
}

fn bubble(who: Who, body: &str) -> Element<'_, Message> {
    let (label, color) = match who {
        Who::You => ("YOU", ACCENT),
        Who::Agent => ("AGENT", MUTED),
        Who::Tool => ("TOOL", WARN),
        Who::System => ("SYSTEM", BAD),
    };
    container(column![text(label).size(11).style(color), text(body).size(16)].spacing(4))
        .padding(14)
        .width(Length::Fill)
        .style(panel())
        .into()
}

fn prompt_view(p: &Prompt) -> Element<'_, Message> {
    let mut col = column![
        text(if p.header.is_empty() {
            "THE AGENT ASKS".to_string()
        } else {
            p.header.to_uppercase()
        })
        .size(12)
        .style(WARN),
        text(&p.question).size(17)
    ]
    .spacing(8);
    for o in &p.options {
        col = col.push(
            button(text(o).size(16))
                .on_press(Message::PromptAnswer(o.clone()))
                .padding([12, 16])
                .width(Length::Fill)
                .style(theme::Button::Secondary),
        );
    }
    col = col.push(
        button(text("Skip").size(14))
            .on_press(Message::PromptSkip)
            .padding([8, 14])
            .style(theme::Button::Text),
    );
    container(col)
        .padding(14)
        .width(Length::Fill)
        .style(panel())
        .into()
}

fn card_view(c: &Card) -> Element<'_, Message> {
    container(
        row![
            column![text(&c.title).size(15).style(WARN), text(&c.body).size(14)]
                .spacing(4)
                .width(Length::Fill),
            button(text("x").size(18))
                .on_press(Message::DismissCard(c.id))
                .padding([6, 14])
                .style(theme::Button::Text)
        ]
        .spacing(8)
        .align_items(alignment::Alignment::Center),
    )
    .padding(12)
    .width(Length::Fill)
    .style(panel())
    .into()
}

fn confirm_view(c: &Confirm) -> Element<'_, Message> {
    column![
        Space::with_height(Length::Fill),
        text("The agent asks you to confirm").size(15).style(WARN),
        text(&c.question).size(24),
        text(format!(
            "Answer within {} s. No answer counts as no.",
            c.remaining_s
        ))
        .size(13)
        .style(MUTED),
        Space::with_height(Length::Fixed(24.0)),
        button(
            text("Yes")
                .size(26)
                .horizontal_alignment(alignment::Horizontal::Center)
        )
        .on_press(Message::Confirm(true))
        .padding(22)
        .width(Length::Fill)
        .style(theme::Button::Positive),
        button(
            text("No")
                .size(26)
                .horizontal_alignment(alignment::Horizontal::Center)
        )
        .on_press(Message::Confirm(false))
        .padding(22)
        .width(Length::Fill)
        .style(theme::Button::Destructive),
        Space::with_height(Length::Fill),
    ]
    .spacing(14)
    .padding(24)
    .into()
}

fn lock_view(s: &Shell) -> Element<'_, Message> {
    let mut col = column![
        Space::with_height(Length::Fixed(90.0)),
        text(&s.clock).size(72),
        text(s.strip.line()).size(15).style(MUTED),
        Space::with_height(Length::Fixed(30.0)),
    ]
    .spacing(8)
    .align_items(alignment::Alignment::Center);
    if let Some(c) = &s.confirm {
        col = col.push(
            text(format!(
                "The agent is waiting for your confirmation:\n{}\nUnlock to answer.",
                c.question
            ))
            .size(16)
            .style(WARN),
        );
    }
    if !s.cards.is_empty() {
        col = col.push(
            text(format!("{} notification(s) waiting", s.cards.len()))
                .size(14)
                .style(ACCENT),
        );
    }
    col = col.push(Space::with_height(Length::Fill)).push(
        text("Swipe up from the bottom edge to unlock")
            .size(13)
            .style(MUTED),
    );
    col.padding(24).width(Length::Fill).into()
}

fn settings_view(s: &Shell) -> Element<'_, Message> {
    let st = &s.strip;
    let yn = |v: Option<bool>| match v {
        Some(true) => "yes",
        Some(false) => "no",
        None => "-",
    };
    column![
        text("Settings").size(28),
        text("DEVICE (from s22d)").size(12).style(ACCENT),
        setting_row("Battery", st.battery_label()),
        setting_row("Hottest zone", st.temp_label()),
        setting_row("Network", st.network_label()),
        setting_row("Modem", st.modem.clone().unwrap_or_else(|| "-".into())),
        setting_row("Audio muted", yn(st.muted).into()),
        container(
            column![
                row![
                    text("Brightness").size(15),
                    Space::with_width(Length::Fill),
                    text(format!("{}%", s.brightness)).size(14).style(MUTED)
                ],
                slider(1..=100u8, s.brightness, Message::BrightnessChanged)
                    .on_release(Message::BrightnessCommit)
                    .height(32)
            ]
            .spacing(8)
            .padding(14)
        )
        .width(Length::Fill)
        .style(panel()),
        text("AGENT").size(12).style(ACCENT),
        setting_row("Model", st.model.clone().unwrap_or_else(|| "-".into())),
        setting_row("OpenUnum", yn(st.healthy).into()),
        text(&s.note).size(12).style(MUTED)
    ]
    .spacing(10)
    .into()
}

fn phone_view(s: &Shell) -> Element<'_, Message> {
    let p = &s.phone;
    let or_dash = |v: &Option<String>| v.clone().unwrap_or_else(|| "-".into());
    let mut col = column![
        text("Phone").size(28),
        text("MODEM (read only)").size(12).style(ACCENT),
        setting_row("Modem", or_dash(&p.modem_state)),
        setting_row("SIM", or_dash(&p.sim)),
        setting_row("Operator", or_dash(&p.operator)),
        setting_row("Signal", or_dash(&p.signal)),
        text("Sending texts and placing calls stays with the agent, behind the owner's yes on this screen.").size(12).style(MUTED)
    ]
    .spacing(10);
    if let Some(e) = &p.error {
        col = col.push(text(e).size(13).style(WARN));
    }
    if !p.messages.is_empty() {
        col = col.push(text("RECENT MESSAGES").size(12).style(ACCENT));
        for (from, body) in &p.messages {
            col = col.push(
                container(
                    column![text(from).size(11).style(MUTED), text(body).size(15)].spacing(2),
                )
                .padding(12)
                .width(Length::Fill)
                .style(panel()),
            );
        }
    }
    scrollable(col).height(Length::Fill).into()
}

fn info_page<'a>(title: &'a str, body: &'a str) -> Element<'a, Message> {
    column![text(title).size(26), text(body).size(15).style(MUTED)]
        .spacing(12)
        .into()
}

fn tile<'a>(glyph: &'a str, label: &'a str, page: Page) -> Element<'a, Message> {
    button(
        column![text(glyph).size(28).style(ACCENT), text(label).size(14)]
            .spacing(8)
            .align_items(alignment::Alignment::Center)
            .width(Length::Fill),
    )
    .on_press(Message::Navigate(page))
    .padding(16)
    .width(Length::Fill)
    .style(theme::Button::Secondary)
    .into()
}

fn nav_button<'a>(label: &'a str, page: Page, current: Page) -> Element<'a, Message> {
    let color = if page == current {
        ACCENT
    } else {
        Color::WHITE
    };
    button(
        text(label)
            .size(14)
            .style(color)
            .horizontal_alignment(alignment::Horizontal::Center),
    )
    .on_press(Message::Navigate(page))
    .padding([14, 8])
    .width(Length::Fill)
    .style(theme::Button::Text)
    .into()
}

fn setting_row<'a>(k: &'a str, v: String) -> Element<'a, Message> {
    container(
        row![
            text(k).size(15),
            Space::with_width(Length::Fill),
            text(v).size(14).style(MUTED)
        ]
        .padding(14),
    )
    .width(Length::Fill)
    .style(panel())
    .into()
}

fn panel() -> theme::Container {
    theme::Container::Custom(Box::new(PanelStyle))
}

struct RootStyle;

impl container::StyleSheet for RootStyle {
    type Style = Theme;
    fn appearance(&self, _: &Theme) -> container::Appearance {
        container::Appearance {
            background: Some(BG.into()),
            text_color: Some(Color::WHITE),
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
            text_color: Some(Color::WHITE),
            border: Border {
                radius: 14.0.into(),
                width: 1.0,
                color: Color::from_rgb(0.13, 0.17, 0.22),
            },
            ..Default::default()
        }
    }
}
