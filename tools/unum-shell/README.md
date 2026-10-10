# unum-shell

The S22's touch shell in Rust: iced 0.12 with the tiny-skia CPU renderer (no GPU, no wgpu), about 14 MB resident, 0.02 % CPU idle.
It is an opt-in profile next to the Quickshell touch shell, which stays installed and is the rollback.

| Feature | How |
|---|---|
| Streaming chat | `GET /api/chat/stream?sessionId=touch` (typed SSE events: `content_delta`, tool calls, snapshots) opened first, then `POST /api/chat`. The answer appears as it is written; the POST carries the final reply. Stop button: `POST /api/chat/cancel`. A missing stream degrades to plain request/response. |
| Agent questions | The `user_prompt_requested` event (`ask_user`) becomes buttons in Chat, answered with `POST /api/chat/answer`. A card tells you when you are on another page. |
| Cards and owner confirmation | Driven by `s22-touchd`, the same `ui card` / `ui confirm` commands that drive Quickshell (and `s22-ui`, the `s22-ui` plugin, and `s22d`'s risky endpoints). The answer is written to `/run/s22-touch/confirm/<id>.json` as `{"id","answer","ts"}`, atomically. Unanswered questions answer `timeout`. |
| Gestures | touchd's edge-swipe daemon calls `home` / `back` / `switcher` on the shell; unum-shell answers them (`switcher` reports "unsupported", as the app switcher is not ported). |
| Status strip | `s22d` `GET /v1/status` (battery, hottest zone, Wi-Fi) plus OpenUnum's model and health, every 15 s; skipped while the screen is off. |
| Pages | Home, Chat, Settings (device rows, brightness slider via `s22d`), Phone (read-only modem and SMS from `s22d`), Lock cover. Camera and Files are text-only pages. |
| Keyboard | Entering Chat calls squeekboard's `SetVisible` over `gdbus`, like the Quickshell shell (iced 0.12 has no text-input-v3). |

## How touchd reaches it

`s22-touchd` has a UI backend setting: `/srv/s22/state/touchui/ui-backend` containing `quickshell` (default) or `unum-shell`
(or `S22_UI_BACKEND`). With `unum-shell` it starts `/usr/local/bin/unum-shell` in the chroot instead of Quickshell, and forwards every
`ui` function to `shell.sock` in its run directory (one JSON line `{"fn","args"}` in, `{"ok","result"}` out). Changing the file switches
shells within a few seconds. Config (env): `S22_SHELL_API` (default `http://127.0.0.1:18880`), `S22_SHELL_S22D` (`http://127.0.0.1:8766`),
`S22_TOUCH_RUN` (`/run/s22-touch`).

## Build and test

```sh
export PATH=$HOME/.cargo/bin:$PATH
cargo test --locked --manifest-path tools/unum-shell/Cargo.toml        # 52 tests, no window system needed
flock /home/corpunum/.cache/rig-heavy.lock \
  env CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc \
  cargo build --locked --release --target aarch64-unknown-linux-gnu --manifest-path tools/unum-shell/Cargo.toml
cargo run --release -- --screenshots tools/unum-shell/screenshots      # offscreen PNGs of every screen
```

The aarch64 glibc binary (4.2 MB) is built by CI and uploaded as the artifact `unum-shell-aarch64-unknown-linux-gnu`; it runs in the phone's
Arch chroot. Tests: model rules (cards, confirm protocol, touchd calls, chat streaming), SSE parsing (split chunks, CRLF, multi-byte text),
HTTP clients against mock servers (a whole streamed turn, queued 202, failures), the `shell.sock` server, and the offscreen renderer.

## Screenshots

`screenshots/*.png` are rendered by the real `view` code through iced's tiny-skia renderer, offscreen, at the panel's 1080x2340 (scale 2.5), from
states built with the same touchd calls and stream events the live shell handles: `home`, `home-cards`, `chat-tool`, `chat-streaming`,
`chat-agent-question`, `chat-offline`, `confirm`, `lock`, `settings`, `phone`. `live-window-xvfb-confirm.png` is a capture of the running
binary in a real window (Xvfb), after driving it through `shell.sock`. They show the layout; they were not taken on the phone.

## Measured on the rig (x86_64 release, X11 under Xvfb, not the phone)

| | |
|---|---|
| Idle CPU, 60 s, all polling on | 0.02 % of one core |
| RSS after start / after 60 s idle | 13.7 MB / 13.8 MB (peak 14.0 MB, with cards and a question shown) |
| Threads | 34 (tokio and iced workers, all idle) |

The phone uses Wayland (sway-pixman) and an ARM CPU; expect the same order of magnitude, but measure it after deploying (`ps -o rss,pcpu`).

## Deploy (phone owner-agent; order: s22d, s22-device plugin, then this)

```sh
# native root, as root; the aarch64 binary comes from the CI artifact
sh tools/unum-shell/install-unum-shell.sh --binary /path/to/unum-shell   # copy only, stays on Quickshell
sh tools/unum-shell/install-unum-shell.sh --activate                     # touchd switches shells
sh tools/unum-shell/install-unum-shell.sh --rollback                     # back to Quickshell
```

Install the updated `s22-touchd` first (`tools/touchui/install-touchui.sh`): the old one does not know the profile and keeps Quickshell.
Verify after `--activate`: the home screen appears; `s22-ui card Hi hello` shows a card; `s22-ui confirm "Test?"` shows Yes/No and returns the
tapped answer; an edge swipe goes home; the status strip shows battery from s22d; Chat streams a reply. Under sway, `app_id` is `unum-shell`
(fullscreen it with `for_window [app_id="unum-shell"] fullscreen enable` if needed). Rollback leaves nothing behind but the binary.

## Known gaps

- Not run on the phone: Wayland behaviour, touch focus for the text field under sway (the Quickshell shell needed an Exclusive-focus workaround), and the keyboard reveal.
- No app switcher (`switcher` is reported unsupported), no terminal/agent launchers, no camera preview, no voice.
- Font coverage on the phone decides how glyphs look; the shell uses plain letters instead of symbols to stay safe.
