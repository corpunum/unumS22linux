//! unum-shell: a small iced touch shell for OpenUnum on the Galaxy S22.
//!
//!   unum-shell                     run the shell (Wayland or X11 window, tiny-skia CPU renderer)
//!   unum-shell --screenshots DIR   render every screen offscreen to DIR/*.png (no window system needed)
//!   unum-shell --version

mod app;
mod ctl;
mod model;
mod net;
mod render;
mod sse;
mod view;

use iced::{window, Application, Settings, Size};
use std::path::PathBuf;

fn main() {
    let mut args = std::env::args().skip(1);
    match args.next().as_deref() {
        Some("--version") => println!("unum-shell {}", env!("CARGO_PKG_VERSION")),
        Some("--screenshots") => {
            let dir = PathBuf::from(args.next().unwrap_or_else(|| "screenshots".into()));
            if let Err(e) = screenshots(&dir) {
                eprintln!("unum-shell: {e}");
                std::process::exit(1);
            }
        }
        Some(other) => {
            eprintln!("usage: unum-shell [--screenshots DIR | --version]  (unknown: {other})");
            std::process::exit(2);
        }
        None => run(),
    }
}

fn screenshots(dir: &std::path::Path) -> Result<(), String> {
    std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    for (name, shell) in render::demo_states() {
        let path = dir.join(format!("{name}.png"));
        render::write_png(&shell, &path)?;
        println!("{}", path.display());
    }
    Ok(())
}

fn run() {
    let mut settings = Settings::with_flags(net::Endpoints::from_env());
    settings.id = Some("unum-shell".into());
    settings.antialiasing = false;
    settings.window = window::Settings {
        size: Size::new(render::LOGICAL.0, render::LOGICAL.1),
        resizable: false,
        decorations: false,
        platform_specific: window::settings::PlatformSpecific {
            application_id: "unum-shell".into(),
        },
        ..Default::default()
    };
    if let Err(e) = app::UnumShell::run(settings) {
        eprintln!("unum-shell: {e}");
        std::process::exit(1);
    }
}
