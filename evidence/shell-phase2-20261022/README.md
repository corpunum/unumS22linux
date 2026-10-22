# unum-fastshell phase 2 — 2026-10-22

Live camera page, keyboard feedback, Settings through the s22d device API, and the Home Agent card.
Everything below was measured on the phone (SM-S901B, native root) unless marked "tests only".

**Conditions.** The phone was hot all afternoon (BIG 76–84 °C, load average ~15): a `gst-inspect-1.0 x264enc`
that this worker started at 12:27 as a read-only probe for a video encoder is stuck in the kernel
(`alloc_contig_range`, CMA full), ignores SIGKILL and spins one core (see "Incident"). Every number here was taken
with that load and with thermal throttling, so they are pessimistic.

## 1. Camera page — verified on the phone

`s22-campreview` grew a service mode (`--until-signal`, JSON event lines on stdout, `--still-dir`: SIGUSR1 bursts
written by a helper thread while streaming, SIGUSR2: MJPEG AVI recording, `src/avi.rs`). The shell runs it as a
child only while the Camera page shows (unlocked, panel on) and blits the newest shared-memory frame.

| What | Result |
|---|---|
| Tap Camera → first frame | 123–140 ms rear (child start → first frame); front 351 ms |
| Viewfinder on the panel | **29.9 fps** (frames flipped in the last 3 s), publish → flip p50 25.9 / p95 30.1 ms; render p50 17 ms (the rotated 1080x1438 blit) |
| Shutter → thumbnail on the panel | **820, 855, 873, 886 ms** (4 raws in RAM 103–106 ms, raw write 11–20 ms to tmpfs, `s22-develop --stack` 534–581 ms on the X2) |
| Video | MJPEG AVI 382x510 (no H.264 encoder exists on the phone: no x264/ffmpeg/gst encoder plugin; the spike's x264 was a temporary binary): 7.23 s, 215 frames, **29.7 fps**, JPEG 17 ms p50 on its own core, 2.5 MB. Decodes with ffmpeg. |
| Front/back switch | stop ~70–100 ms + start; the stream restarts with the other sensor's rotation |
| Leaving the page / Power lock | SIGTERM, clean teardown, inhibit file removed (unit tests and the log) |
| Autosleep | `/run/s22-autosleep/inhibit.d/fastshell-camera` exists while streaming or developing |

Orientation: rear rotated 90° clockwise, front 270° + mirrored preview (photos unmirrored). With a real scene (a fork
on a table under a window; the room's ceiling and a shelf from the front camera) both previews look upright, but
nothing in view had readable text, so mirroring is not proven. Settings → "Camera … — hold to rotate" and
`/srv/s22/state/fastshell/camera.json` change it per sensor.

Screenshots (the shell's own canvas, half size): `live-camera-rear-recording.png` (VIDEO, recording 00:06, thumbnail
of the last photo), `live-camera-front-thumbnail.png` (front, "saved cam-…-front.jpg").

## 2. Keyboard feedback — verified on the phone

- Popup: the pressed key drawn enlarged above the finger, always on (`live-keyboard-popup.png`, "d" held).
  Touch → flip with the popup 5.5–6.8 ms ("press" in latency.jsonl).
- Haptic tick per key: s22-haptic's `played` counter rose 11 → 17 for 6 key presses; +2 for Volume up + Volume down
  through the shell's button path. Vibration off (Settings) → no datagram (unit test). How the tick feels was not
  checked by a person.

## 3. Settings through the device API — verified on the phone

All rows call s22d over `/run/s22d.sock` (`POST /v1/device/<id>`, caller `shell`); every value shown is s22d's
read-back (`device.status`, `volume.get`, `screen_timeout.get` on open, `device.status` again after each change).

| Row | Tested on the phone |
|---|---|
| Vibration | off → `haptics/config.json` `{"enabled":false}`, on again (`live-settings-vibration-off.png`) |
| Calls (gate) | off → `calls.json` `{"enabled":false}`, on again |
| Wi-Fi | off (`wifi.set` 200, enabled:false), on again (enabled:true, not connected: no known network) |
| Volume slider | drag → `volume.set` 5, 7 (s22-say level 4 → 7), restored to 4 |
| Brightness slider | tap → `brightness.set` 21 %, restored to 25 % |
| Bluetooth, mobile data, auto-sleep, mute, screen timeout | rendered with live state; **not toggled on the phone** (Bluetooth up takes ~2 min; mobile data off is the only uplink) — unit tests only |

## 4. Home Agent card — verified on the phone (except a real spoken sentence)

`s22-assistant --card listen` / `--card turn` (fillers, spoken thinking and the −16 LUFS path unchanged).

| What | Result |
|---|---|
| Hold (injected 2.5 s hold) | "listening" 1.4 s after the press (s22-rec start, cue_ms 1385); the release came before cue + 1 s, so the fixed 6 s window was recorded (the existing PTT rule); nobody spoke: transcript empty → "Didn't catch that" |
| Turn | `agent_say` (ctl) "What is two plus two?…" → thinking → reply "Four." → speaking → done in 55.6 s (local Gemma E2B, hot phone) |
| Queue | a second utterance while the first ran showed "queued (1)" and ran right after (51.6 s) |
| STOP while thinking | group SIGTERM 18 ms before the child's exit, OpenUnum cancel 200 at +85 ms; `/api/chat/pending` for `voice`: "Stopped: the turn was cancelled…"; the queued turn was dropped |
| STOP while a filler played | s22-say, `aplay` and s22-assistant all gone 0.4 s after the tap; s22-say's lock free |

Screenshots: `live-home-agent-card-first.png`, `live-home-agent-card-busy.png` (the guardian's heat card on top).
Not verified: a real spoken sentence end to end (nobody near the phone), tool names live from a turn (the local model
called no tool), confirm cards over the card (the s22d confirm path is unchanged and unit-tested).

## Tap → page

| Target | press p50 / max | page p50 / max |
|---|---|---|
| Apps button (Home → switcher), 20 taps | 4.1 / 10.1 ms | **10.5** / 17.2 ms |
| Settings tile, 20 taps | 7.0 / 11.4 ms | 15.8 / 28.4 ms (the longer page; 3 device calls start on open, off the UI thread) |

Files: `tap-latency-apps-button.txt`, `tap-latency-settings-tile.json`.

## Incident

At 12:27 my first read-only probe ran `chroot … gst-inspect-1.0 x264enc` to look for an encoder. It never returned:
it is in the kernel (`alloc_contig_range`; CMA full), state R, SIGTERM/SIGKILL ignored (pid 3636, 1h17 of CPU by
13:45). It keeps a big core busy (load ~15), blocks suspend and keeps the phone at 76–84 °C (the guardian stopped
non-essential services at 84 °C). Only a reboot clears it; not done by me (other workers on the phone). Lesson: no
GStreamer plugin scans on this kernel; check for encoder files with `ls` only.

## Tests

`cargo test` in tools/unum-fastshell (66: camera state machine, blit tables, Settings/device API state, agent card
queue/stop, haptics, key popup) and tools/hardware/camera/s22-campreview (5, AVI layout, half-size rotation);
`tools/hardware/test_s22_assistant.py` (card modes: 6 new); the host regression runner.
