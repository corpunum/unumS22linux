# Running unum-fastshell on another Linux phone

unum-fastshell was written for one phone, a Galaxy S22 (SM-S901B, Exynos 2200)
running our own native root ([README](README.md)). This page describes what it
needs from a phone and how to run it on one of the usual mainline-Linux phone
distributions (postmarketOS, Mobian, Arch Linux ARM, and similar).

Short version: the shell is a single static binary that draws straight into
KMS and reads evdev. Any phone with a working DRM/KMS display, a multitouch
evdev touchscreen and seatd should show it and respond to touch. Most of the
pages expect services that only exist on our S22 (an OpenUnum agent,
s22-touchd, s22d, s22-phoned). Without those services the pages still open,
but they are empty or show "absent".

## What it needs

| Need | Why | On a typical mainline phone |
|---|---|---|
| DRM/KMS with dumb buffers and legacy page-flip | The shell CPU-renders into two dumb buffers and flips them | Any msm/mediatek/rockchip/sun4i/exynos DRM driver. Not fbdev-only phones. |
| A multitouch (protocol B) evdev touchscreen | Touch, edge swipes | Standard |
| seatd (`libseat` server) | The shell opens the DRM card as a seat client, so it can share the panel with a compositor | `seatd` package; or run your compositor with seatd |
| root, or a user in the seat group with read access to the touchscreen | It reads `/dev/input/eventN` directly, without grabbing it | Add the user to the `input` group or run it from a system service |
| A 1080 x 2340 portrait panel, ideally | The layout is fixed at that size (see [Known limits](#known-limits)) | Other sizes are centred and cropped or letterboxed |

It does not need a GPU, Wayland, Mesa, or any Samsung binaries.

## Build

```sh
export PATH=$HOME/.cargo/bin:$PATH
cd tools/unum-fastshell
cargo test --offline --release      # unit tests, including the config/detection ones
cargo run --release -- dump /tmp/shots   # PNG of every screen, no panel needed
```

### Cross-compiling for an arm64 phone

The S22 build is a static aarch64 musl binary, linked with `rust-lld`, so no
cross C toolchain is needed:

```sh
rustup target add aarch64-unknown-linux-musl
CARGO_TARGET_AARCH64_UNKNOWN_LINUX_MUSL_LINKER=rust-lld \
  cargo build --offline --release --target aarch64-unknown-linux-musl
# -> target/aarch64-unknown-linux-musl/release/unum-fastshell (about 1.3 MB, static)
```

The static musl binary runs unchanged on postmarketOS (musl), Mobian (glibc)
and Arch Linux ARM (glibc). Building natively on the phone also works
(`cargo build --release`); it takes a while on a phone CPU.

For 32-bit ARM phones use `armv7-unknown-linux-musleabihf` the same way; that
target has not been tried.

## Configure

All settings are optional. The built-in defaults are the S22's, and the
auto-detection always tries the S22 value first, so on the S22 nothing
changes without a config file.

The shell reads a JSON file from `$FASTSHELL_CONFIG`, else from the first of
`/srv/s22/state/fastshell/config.json` and `/etc/unum-fastshell.json` that
exists. Missing keys keep their default; unknown keys are ignored; a file that
does not parse is logged and the defaults are used. The older environment
variables (`FASTSHELL_CHROOT`, `S22_SHELL_API`, ...) still win over the file.

At start-up the shell logs what it picked, for example:

```
fastshell: config /etc/unum-fastshell.json: drm /dev/dri/card0, touch /dev/input/event2, battery /sys/class/power_supply/bq27411-0, backlight /sys/class/backlight/backlight, thermal ["cpu0-thermal", "gpu-thermal"]
```

A minimal config for a postmarketOS phone with no chroot and no S22 services:

```json
{
  "chroot": "",
  "seatd_socket": "/run/seatd.sock",
  "run_dir": "/run/unum-fastshell",
  "state_dir": "/var/lib/unum-fastshell",
  "pid_file": "/run/unum-fastshell.pid",
  "display_state_file": "/run/unum-fastshell.display",
  "say_lock": "/run/unum-fastshell.say.lock",
  "files_dir": "/home/user",
  "tz": "Europe/Berlin",
  "tz_fallback": "CET-1CEST,M3.5.0,M10.5.0/3",
  "net_usb": ["usb0"],
  "net_cell": ["wwan0", "rmnet_data0"]
}
```

### Reference

| Key | S22 default | Auto-detection and notes | Env override |
|---|---|---|---|
| `drm_card` | `auto` | First `/sys/class/drm/cardN` that has a connector whose `status` is `connected` (on the S22: `card1`, because `card0` is the GPU with no connectors). Falls back to `/dev/dri/card1`. Within the card the first connected connector and its current (else preferred) mode are used. | `FASTSHELL_DRM_CARD` |
| `touch_device` | `auto` | First `eventN` whose name is `touch_name`; else the first one with `ABS_MT_POSITION_X/Y` and `INPUT_PROP_DIRECT` (or `BTN_TOUCH` when the kernel has no `properties` file), which skips touchpads and keys. | `FASTSHELL_TOUCH` |
| `touch_name` | `sec_touchscreen` | Preferred touchscreen name | |
| `touch_swap_xy`, `touch_invert_x`, `touch_invert_y` | `false` | For digitisers mounted rotated or mirrored relative to the panel | |
| `battery` | `auto` | `/sys/class/power_supply/battery` if it has `capacity`; else the first supply with `type` = `Battery`. Reads `capacity`, `status`, `temp` (tenths of a degree). | |
| `backlight` | `auto` | `/sys/class/backlight/panel`; else the first backlight. Only read, for the slider position. Setting brightness goes through s22-touchd (see below). | |
| `thermal_types` | `["BIG","MID","LITTLE","G3D"]` | Thermal zones with these `type`s are shown. If none exists, up to four zones whose type mentions cpu, gpu, soc, big, little or cluster. | |
| `net_wifi`, `net_usb`, `net_cell` | `["wlan0"]`, `["ecm0"]`, `["rmnet0"]` | Interfaces checked (operstate `up` or `unknown`) for the status-bar link word | |
| `net_vpn` | `tailscale0` | Adds "ts" when the interface exists; `""` turns it off | |
| `chroot` | `/mnt/omarchy-trial` | Root of the sway desktop the shell hands the panel to. `""` = sway (if any) runs on the host and helper commands run without `chroot`. | `FASTSHELL_CHROOT` |
| `seatd_socket` | `""` = `<chroot>/run/seatd.sock` | | |
| `run_dir` | `""` = `<chroot>/run/s22-touch` | Where `shell.sock` is created and s22-touchd's `ctl.sock` is expected | `S22_TOUCH_RUN` |
| `state_dir` | `/srv/s22/state/fastshell` | `latency.jsonl`, `screen-timeout.json` | `FASTSHELL_STATE` |
| `pid_file`, `display_state_file`, `say_lock` | `/run/s22-fastshell.pid`, `/run/s22-display-state`, `/run/s22-say.lock` | | `FASTSHELL_SAY_LOCK` (say_lock) |
| `api` | `http://127.0.0.1:18880` | OpenUnum agent (chat, model, health) | `S22_SHELL_API` |
| `s22d`, `phoned` | `:8766`, `:8095` on loopback | S22 device daemon and modem daemon | `S22_SHELL_S22D`, `S22_SHELL_PHONED` |
| `session` | `touch` | OpenUnum chat session id | `S22_SHELL_SESSION` |
| `assistant`, `assistant_log` | `/srv/s22/hardware/bin/s22-assistant`, `/srv/s22/buttons/assistant.jsonl` | The Talk button's command and where its last answer is read from | `FASTSHELL_ASSISTANT` |
| `ui_backend_file` | `/srv/s22/state/touchui/ui-backend` | "Classic shell" writes `quickshell` here | |
| `files_dir` | `/root` | Start directory of the Files page | |
| `tz`, `tz_fallback` | `Europe/Athens`, the Athens POSIX rule | musl needs zoneinfo; if neither `/usr/share/zoneinfo/<tz>` nor the chroot's copy exists, the POSIX rule is used | `TZ` |

`screen-timeout.json` in `state_dir` (and `FASTSHELL_IDLE_CFG`) works as on the
S22: `{"unlocked_s":60,"locked_s":15}`, 0 = never.

## Run it

### postmarketOS / Mobian / Arch Linux ARM, without another compositor

1. Stop the phone's own UI (Phosh, Plasma Mobile, Sxmo), for example
   `rc-service tinydm stop` on postmarketOS or `systemctl stop phosh` on
   Mobian. The shell needs the DRM master.
2. Start seatd if it is not running: `seatd -g video &` (or the distribution's
   service).
3. Write the config above to `/etc/unum-fastshell.json`.
4. As root: `unum-fastshell 2>/tmp/fastshell.log`.

With no other seat client, seatd enables the shell straight away. The shell
also tries to switch to itself through `wtype` under sway at start-up and every
6 s until it is active; without sway those attempts fail quietly.

To get back, stop the shell (Ctrl-C or SIGTERM) and start the phone's UI again.
**Keep SSH or a serial console open while you try it**: if the touchscreen is
not detected, the shell has no way to exit from the screen.

### Next to a sway session (as on the S22)

On the S22, sway runs under seatd as session 1 and the shell is a second seat
session. The shell switches seat sessions with `wtype -k XF86Switch_VT_<n>` sent
to sway, and Terminal / "Sway desktop" hand the panel back to sway. To copy
that layout: run sway and the shell against the same seatd, set `chroot` to `""`
if sway runs on the host, and make sure `<chroot>/run/s22-desktop/session`
holds the `WAYLAND_DISPLAY` and `SWAYSOCK` values (two lines). This is what
s22-touchd and `s22-desktop-profile` arrange on the S22; on another phone you
have to arrange it yourself.

## What is optional

| Feature | Needs | Without it |
|---|---|---|
| Home, clock, battery, thermal, link, lock cover, keyboard, Files, edge swipes, screen timeout | Nothing else | Works |
| Agent chat, model/health cards, sessions, running missions | An OpenUnum server at `api` (`/api/chat`, `/api/chat/stream` SSE, health/model endpoints) | Chat shows a connection error; cards stay empty |
| Agent cards, confirm prompts, Power key, brightness, model switch, camera capture | s22-touchd on `<run_dir>/ctl.sock` and a client of `<run_dir>/shell.sock` (JSON-line protocol, see [README](README.md#how-it-works) and `src/ctl.rs`) | `shell.sock` still serves; anything can send `card`, `confirm`, `display`, `lock` lines to it. Brightness and camera do nothing. |
| Phone page, 5G/4G pill | s22-phoned at `phoned` | "Loading…" / no pill |
| Settings toggles and sliders | s22d's device API on `/run/s22d.sock` (`/v1/device/...`) | Rows say "device API (s22d) not reachable"; the screen-timeout buttons write the shell's own file |
| Camera page | `s22-campreview` (S22 V4L2 path) and `s22-develop` | "camera failed: …" in the viewfinder |
| Agent card | `s22-assistant --card listen\|turn` (JSON lines on stdout) | "Listening failed" |
| Haptics | a datagram socket at `/run/s22-haptic.sock` | silent |
| Talk | The `assistant` command (records, transcribes, asks the agent, speaks the answer, appends a JSON line to `assistant_log`) | The command fails; no answer card is shown |
| Terminal, Sway desktop | sway + foot + wtype + squeekboard, sharing the seat | The panel is handed to a session that may not exist; switch back with SIGTERM or the seat |

## Known limits

- **The layout is fixed at 1080 x 2340 portrait.** Every page uses those
  constants (`ui::W`, `ui::H`). On a different panel the canvas is centred: a
  taller or wider panel gets black bands, a smaller one is cropped at the
  edges, and touch is mapped to match. The S22 path (same size) is unchanged.
  A truly resolution-independent layout would mean scaling every constant in
  `src/ui.rs`; that has not been done.
- XRGB8888 dumb buffers and the legacy (non-atomic) KMS API. DPMS for screen
  off uses the connector's legacy `DPMS` property.
- Only the first finger is used; a second finger cancels the gesture.
- Hardware keys: the shell reads Power and volume itself (`src/buttons.rs`),
  finding the key devices by their key capabilities, so a phone whose kernel
  exposes `KEY_POWER`/`KEY_VOLUMEUP`/`KEY_VOLUMEDOWN` through evdev should work
  as is. Volume changes the S22's speech level (`/run/s22-say.level`) and
  push-to-talk runs the configured `assistant`; on another phone point those at
  your own audio and voice tools. Bindings are listed in Settings -> Buttons.
- The font is Latin + Greek only (no emoji, CJK, Arabic...).
- Tested only on the S22. Everything in this file about other phones is what
  the code is written to do, not something we have seen working on them.
