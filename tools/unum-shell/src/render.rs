//! Offscreen rendering with iced's tiny-skia renderer: the same `view::root` the window draws,
//! rasterised without a window system, so screenshots work on a headless rig and in CI.

use crate::model::{Call, Chat, Line, Page, PhoneInfo, Prompt, Shell, Who};
use crate::sse::ChatEvent;
use crate::view;
use iced::{Color, Font, Pixels, Size, Theme};
use iced_runtime::core::{mouse, renderer};
use iced_runtime::user_interface::{self, UserInterface};
use iced_tiny_skia::graphics::Viewport;
use iced_tiny_skia::Backend;
use iced_tiny_skia::Renderer as SkiaRenderer;
use serde_json::json;
use std::path::Path;
use std::time::Instant;

/// The phone's panel is 1080x2340; at scale 2.5 that is 432x936 logical pixels.
pub const LOGICAL: (f32, f32) = (432.0, 936.0);
pub const SCALE: f32 = 2.5;

pub struct Frame {
    pub width: u32,
    pub height: u32,
    /// Straight RGBA, row-major.
    pub rgba: Vec<u8>,
}

pub fn render(shell: &Shell) -> Frame {
    // iced::Renderer is an enum over the available renderers; with the wgpu feature off it has one variant.
    let mut renderer = iced::Renderer::TinySkia(SkiaRenderer::new(
        Backend::new(),
        Font::DEFAULT,
        Pixels(16.0),
    ));
    let bounds = Size::new(LOGICAL.0, LOGICAL.1);
    let mut ui = UserInterface::build(
        view::root(shell),
        bounds,
        user_interface::Cache::default(),
        &mut renderer,
    );
    ui.draw(
        &mut renderer,
        &Theme::Dark,
        &renderer::Style {
            text_color: Color::WHITE,
        },
        mouse::Cursor::Unavailable,
    );
    let (w, h) = ((LOGICAL.0 * SCALE) as u32, (LOGICAL.1 * SCALE) as u32);
    let viewport = Viewport::with_physical_size(Size::new(w, h), f64::from(SCALE));
    let mut pixmap = tiny_skia::Pixmap::new(w, h).expect("pixmap");
    let mut mask = tiny_skia::Mask::new(w, h).expect("mask");
    let iced::Renderer::TinySkia(skia) = &mut renderer;
    skia.with_primitives(|backend, primitives| {
        backend.draw(
            &mut pixmap.as_mut(),
            &mut mask,
            primitives,
            &viewport,
            &[iced::Rectangle::with_size(Size::new(w as f32, h as f32))],
            view::BG,
            &[] as &[&str],
        );
    });
    // iced's tiny-skia backend draws for a BGRA window buffer (it swaps red and blue on the way in),
    // so swap them back. The UI is opaque, so premultiplied and straight alpha are the same.
    let rgba = pixmap
        .pixels()
        .iter()
        .flat_map(|p| {
            let c = p.demultiply();
            [c.blue(), c.green(), c.red(), c.alpha()]
        })
        .collect();
    Frame {
        width: w,
        height: h,
        rgba,
    }
}

pub fn write_png(shell: &Shell, path: &Path) -> Result<Frame, String> {
    let frame = render(shell);
    let bytes = premultiplied(&frame);
    let pixmap =
        tiny_skia::PixmapRef::from_bytes(&bytes, frame.width, frame.height).ok_or("bad pixmap")?;
    pixmap.save_png(path).map_err(|e| e.to_string())?;
    Ok(frame)
}

fn premultiplied(f: &Frame) -> Vec<u8> {
    // The frame is opaque (alpha 255), so straight and premultiplied are the same bytes.
    f.rgba.clone()
}

// ------------------------------------------------------------------ demo states for the screenshots

fn status_json() -> serde_json::Value {
    json!({
        "battery": {"percent": 83, "charging": false},
        "thermal_max_c": 41.0,
        "network": {"wlan0": "up", "usb0": "down"},
        "modem": "ONLINE",
        "display": {"state": "on", "brightness": 128, "max_brightness": 255},
        "audio": {"level": 0, "muted": true},
    })
}

fn base() -> Shell {
    let mut s = Shell::default();
    s.clock = "09:41".into();
    s.hour = 9;
    s.strip.apply_status(&status_json());
    s.strip.model = Some("gpt-6-luna".into());
    s.strip.healthy = Some(true);
    s.brightness = 50;
    s
}

fn call(s: &mut Shell, f: &str, args: &[&str]) {
    s.call(
        &Call {
            function: f.into(),
            args: args.iter().map(|a| a.to_string()).collect(),
        },
        Instant::now(),
    );
}

/// Every screen the shell can show, built through the same code paths the live shell uses
/// (touchd calls and streamed chat events).
pub fn demo_states() -> Vec<(&'static str, Shell)> {
    let mut out = vec![];

    out.push(("home", base()));

    let mut s = base();
    call(&mut s, "open", &["chat"]);
    s.chat.push(Who::You, "What is the phone doing right now?");
    s.chat.push(
        Who::Agent,
        "Battery is at 83 percent and cool. Wi-Fi is up and the modem is online.",
    );
    s.chat
        .start_turn("Take a photo of the desk and tell me what is on it.");
    s.chat
        .apply(ChatEvent::ToolStarted("camera_capture".into()));
    out.push(("chat-tool", s));

    let mut s = base();
    call(&mut s, "open", &["chat"]);
    s.chat.push(Who::You, "Summarise today's messages.");
    s.chat.start_turn("Write me a two line status report.");
    for t in [
        "Everything is running. ",
        "The battery is at 83 percent, ",
        "and the temperature is normal",
    ] {
        s.chat.apply(ChatEvent::Delta {
            token: t.into(),
            replace: false,
        });
    }
    out.push(("chat-streaming", s));

    let mut s = base();
    call(&mut s, "open", &["chat"]);
    s.chat.start_turn("Make a short video.");
    s.set_prompt(Prompt {
        id: "ask-1".into(),
        header: "Aspect".into(),
        question: "Which aspect ratio do you want?".into(),
        options: vec![
            "16:9 landscape".into(),
            "9:16 portrait".into(),
            "1:1 square".into(),
        ],
    });
    out.push(("chat-agent-question", s));

    let mut s = base();
    call(
        &mut s,
        "card",
        &[
            "Battery low",
            "The phone is at 15 percent and not charging.",
            "30",
        ],
    );
    call(
        &mut s,
        "card",
        &["Message from Anna", "Are we still on for lunch?", "30"],
    );
    out.push(("home-cards", s));

    let mut s = base();
    call(
        &mut s,
        "confirm",
        &[
            "dab12cd34",
            "Send this text to +306900000000?\n\"On my way, back in ten.\"",
            "60",
        ],
    );
    out.push(("confirm", s));

    let mut s = base();
    call(&mut s, "open", &["settings"]);
    out.push(("settings", s));

    let mut s = base();
    s.phone = PhoneInfo {
        modem_state: Some("ONLINE".into()),
        sim: Some("READY".into()),
        operator: Some("COSMOTE".into()),
        signal: Some("4".into()),
        messages: vec![
            ("+306900000001".into(), "Are we still on for lunch?".into()),
            ("+306900000002".into(), "Your parcel arrives today.".into()),
        ],
        error: None,
    };
    call(&mut s, "open", &["phone"]);
    out.push(("phone", s));

    let mut s = base();
    call(
        &mut s,
        "confirm",
        &["c77", "Reboot the phone into recovery mode?", "60"],
    );
    call(&mut s, "card", &["Note", "One notification waiting", "60"]);
    call(&mut s, "lock", &[]);
    out.push(("lock", s));

    let mut s = base();
    s.strip.s22d_down();
    s.strip.model = Some("gpt-6-luna".into());
    s.chat.lines = vec![Line {
        who: Who::System,
        text: "Message failed: connection refused".into(),
    }];
    call(&mut s, "open", &["chat"]);
    s.page = Page::Chat;
    out.push(("chat-offline", s));

    let _ = Chat::default();
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn distinct_colors(f: &Frame) -> usize {
        let mut seen = std::collections::HashSet::new();
        for px in f.rgba.chunks_exact(4).step_by(7) {
            seen.insert([px[0], px[1], px[2]]);
        }
        seen.len()
    }

    #[test]
    fn every_demo_screen_renders_a_full_panel_frame() {
        for (name, shell) in demo_states() {
            let f = render(&shell);
            assert_eq!((f.width, f.height), (1080, 2340), "{name}");
            assert_eq!(f.rgba.len(), 1080 * 2340 * 4, "{name}");
            assert!(
                f.rgba.chunks_exact(4).all(|p| p[3] == 255),
                "{name}: must be opaque"
            );
            assert!(distinct_colors(&f) > 8, "{name}: looks blank");
            // The background colour is dominant at the screen corners.
            let corner = &f.rgba[..4];
            assert_eq!([corner[0], corner[1], corner[2]], [9, 12, 17], "{name}");
        }
    }

    #[test]
    fn different_states_render_differently() {
        let states = demo_states();
        let a = render(&states.iter().find(|(n, _)| *n == "home").unwrap().1);
        let b = render(
            &states
                .iter()
                .find(|(n, _)| *n == "chat-streaming")
                .unwrap()
                .1,
        );
        let c = render(&states.iter().find(|(n, _)| *n == "confirm").unwrap().1);
        assert_ne!(a.rgba, b.rgba);
        assert_ne!(b.rgba, c.rgba);
    }

    #[test]
    fn rendering_is_deterministic() {
        let s = demo_states()
            .into_iter()
            .find(|(n, _)| *n == "settings")
            .unwrap()
            .1;
        assert_eq!(render(&s).rgba, render(&s).rgba);
    }

    #[test]
    fn writes_a_png() {
        let dir = std::env::temp_dir().join(format!("unum-shell-png-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let (name, shell) = demo_states().remove(0);
        let p = dir.join(format!("{name}.png"));
        write_png(&shell, &p).unwrap();
        let bytes = std::fs::read(&p).unwrap();
        assert_eq!(&bytes[..8], b"\x89PNG\r\n\x1a\n");
        assert!(bytes.len() > 5_000);
        let _ = std::fs::remove_dir_all(dir);
    }
}
