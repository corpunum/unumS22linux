# unum-shell

A small iced 0.12 shell for OpenUnum, with Home, Chat, and Settings screens plus placeholder quick-access destinations.

## Build and test

```sh
cargo test
cargo run
```

The crate pins iced to `=0.12.1` and disables its default `wgpu` feature. Iced 0.12 has no `tiny-skia` top-level feature: with `default-features = false`, `iced_renderer` selects its CPU TinySkia compositor, while `tokio` and `image` remain explicitly enabled for the shell.

## PNG screenshot strategy

No screenshot dependency or external service is required. On a graphical Linux session, launch the shell and use the desktop's built-in screenshot facility (for example, GNOME Screenshot) to save a PNG. For scripted Wayland captures, `grim unum-shell.png` can capture the active output when `grim` is already installed. The application currently has no built-in screenshot UI/API; this keeps the MVP renderer CPU-only and avoids introducing platform-specific dependencies.
