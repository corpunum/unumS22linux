# unum-shell

A native Rust/iced phone-shell MVP for the Galaxy S22 Linux userland. It uses iced 0.12.1 with the CPU TinySkia renderer (no wgpu), a dark touch-sized layout, Home/Chat/Settings, quick-access placeholders, and OpenUnum's local chat API. Device status is read-only and degrades to placeholders when `s22d` is unavailable.

## Build and test

On the rig, with Rust installed:

```sh
export PATH="$HOME/.cargo/bin:$PATH"
cargo test --locked
cargo build --locked --release
```

For the phone's Arch Linux ARM userland (aarch64, glibc):

```sh
export PATH="$HOME/.cargo/bin:$PATH"
export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc
cargo build --locked --release --target aarch64-unknown-linux-gnu
```

The binary is `target/aarch64-unknown-linux-gnu/release/unum-shell`. Keep this build under the rig's heavy-build lock when sharing the rig. `iced` is pinned to `=0.12.1` with default features disabled; iced 0.12 selects its TinySkia CPU compositor in that configuration.

## Try under sway (rig/headless)

The phone display is 1080x2340 at scale 2. The window is sized 430x932 logical pixels. With sway and grim installed, launch from the phone chroot inside the existing sway session:

```sh
cargo run --release
# in another terminal, capture the visible output:
grim screenshots/home.png
```

A headless compositor can be used for a smoke launch/capture on a rig that has sway, grim, and the pixman backend:

```sh
WLR_BACKENDS=headless WLR_RENDERER=pixman sway
# in the sway session:
unum-shell
grim screenshots/home.png
```

Use the app navigation buttons to capture Chat and Settings as well. Screenshots should be captured from the actual app under sway; do not substitute mockups. The rig used for this change did not have sway/grim installed, so no rendered PNGs are included in this PR. Headless screenshot verification remains outstanding.

## Phone deployment (not performed; no phone access)

From the repository checkout on the phone, build natively or copy the aarch64 binary above into the Arch chroot, then launch it from the active sway session in place of Quickshell. OpenUnum is expected at `127.0.0.1:18880`; the device daemon is expected at `127.0.0.1:8766` and is optional for startup. The current chat UI posts to `POST /api/chat` with `sessionId: "touch"` and `message`; this MVP waits for the request response rather than rendering streamed SSE events. Agent notifications/confirm cards and the s22 touch control-socket integration are not wired yet.

Before trying the replacement, record the existing Quickshell launch command/service and ensure the current touch shell files/config remain intact. To roll back, stop `unum-shell` and start the existing Quickshell supervisor/service using the recorded command; do not remove or overwrite the existing shell. Verify the old home screen and touch navigation return. Deployment and rollback must be tested on the phone before treating the replacement as production-ready.

## Scope and known limits

- Home, chat composition/request/reply, read-only device status, and Settings are implemented.
- Phone, Camera, and Files are quick-access placeholders only.
- Voice is a stub; chat is request/response (not streaming).
- UI notifications/agent cards, `ui_confirm` / `ui_show_card` protocol, socket gestures, actual clock, OpenUnum agent model/guardian status, and live phone-input focus are not integrated yet.
- No phone deployment, idle CPU/RSS measurement, per-screen screenshots, or rollback test was possible on the rig; these are explicit follow-up gates.
