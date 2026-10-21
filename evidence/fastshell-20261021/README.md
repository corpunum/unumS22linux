# unum-fastshell on the phone: the real fast touch shell (2026-10-21)

Phase 2 after the [fastui spike](../fastui-20261021/README.md). Tool: [`tools/unum-fastshell/`](../../tools/unum-fastshell/README.md).

Everything ran on the Galaxy S22: kernel 5.10.260, desktop sway-pixman, OpenUnum on `:18880` with gpt-6-luna. Taps and Power presses were **injected** into the real evdev nodes. No physical finger was used.

## Result: fastshell is the default touch shell

| Criterion (from the task) | Measured | Holds |
|---|---|---|
| Tap → page ≤ 25 ms p50 | See the latency table below. | yes |
| Chat streaming works | A 300-word answer arrived as 441 `content_delta` events and was drawn as it streamed ([sequence](chat-streaming-sequence.png)). | yes |
| Agent cards work | `s22-touchd ui card` showed a card. `s22-ui confirm` returned `{"answer": "yes"}` after an injected tap on Yes ([confirm](live-confirm.png)). | yes |
| Lock / unlock works | Injected Power press → s22-buttons → `hooks/power` → touchd → shell: lock cover, connector DPMS Off, CRTC `active=0`.<br>Second press: DPMS On with the lock cover ([lock](live-lock.png)). Swipe up: unlocked ([unlocked](live-unlocked.png)). | yes |
| Recovery to sway works | Power held 1.3 s → `hooks/power-long`: ui-backend set to quickshell, shell stopped by exact PID, sway on plane-0, Quickshell up 2 s later ([fallback](live-fallback.png)).<br>`s22-desktop-profile fastshell` brought the shell back within 1 s. | yes |
| Survives `s22-reboot recovery` | Rebooted at 23:14. The shell was on plane-0 about 20 s after boot with no manual step.<br>Tap test again: page p50 18.4 ms. Chat turn OK, cards OK ([chat](after-reboot-chat.png), [home](after-reboot-home.png)). | yes |

## Latency

Same script ([`fs-tap-latency.py`](../../tools/unum-fastshell/fs-tap-latency.py)), same phone, same session, Settings tile, 60 ms hold:
- "fb change" is plane-0's fb id changing in DRM debugfs;
- "internal" is the shell's own evdev timestamp → DRM flip-complete event.

| | fastshell | Quickshell |
|---|---|---|
| Touch-down → highlight (fb change), p50 | **11.1 ms** (max 13.8, n 20) | 25.9 ms (n 9, 1 missed) |
| Touch-up → page (fb change), p50 | **12.1 ms** (max 20.1) | 63.2 ms (max 265) |
| Touch-down → page incl. the 60 ms hold, p50 (the spike's "120 ms" metric) | 74.1 ms | 123.4 ms (the spike baseline was 120 ms) |
| Internal touch → flip-complete, page, p50 / p95 | **18.4 / 26.9 ms** | – |
| Internal touch → flip-complete, highlight, p50 | 17.1 ms | – |
| After the reboot: touch-up → page, p50 | 18.4 ms (n 10) | – |

Note on the baseline: the spike's 120 ms "tap → page" was measured from touch-down and includes the 60 ms hold. Measured from the release, Quickshell is about 63 ms and fastshell about 12 ms.

Files:
- [`fastshell-tap-latency.json`](fastshell-tap-latency.json)
- [`quickshell-tap-latency.json`](quickshell-tap-latency.json)
- [`fastshell-tap-latency-after-reboot.json`](fastshell-tap-latency-after-reboot.json)
- [`fastshell-internal-latency.jsonl`](fastshell-internal-latency.jsonl) (every press, page and key, touch → flip)

## Cost

| | Value |
|---|---|
| Idle (10 s) | 0.4 % of one core, 1 panel flip (the clock), **RSS 10.9 MB** (Quickshell 78–91 MB) |
| Kinetic fling in a long chat (3 s) | 153 panel flips (51 fps average including the slow-down), frame p50 10.7 ms, p95 26.2 ms, 31 % of one core |
| Frame cost, live | render p50 8.4 ms, copy p50 4.0 ms |
| Scroll frame | render p50 3.6 ms |
| Offscreen full frame on the phone (`bench`) | 0.5–2.1 ms per page |
| Binary | 1.3 MB static |

More in [`measurements.jsonl`](measurements.jsonl).

## Screens (real scanout captures, `drm-capture.py`)

- [home](live-home.png): taken before the TZ fix, so the clock shows UTC
- [settings](live-settings.png), [files](live-files.png), [phone](live-phone.png), [apps](live-switcher.png), [camera](live-camera.png)
- [chat](live-chat2.png), [confirm](live-confirm.png)
- [terminal under sway with squeekboard](live-terminal2.png), [back from the terminal](live-back-from-terminal.png)
- the Quickshell home at the same time: [qs-home](qs-home.png)
- [offscreen render of every screen](offscreen-sheet.png), including the Greek keyboard

## Measured vs assumed

| Measured | Not measured, or assumed |
|---|---|
| Everything in the tables above | **Real finger latency.** Injected events skip the touch controller's scan time. |
| | **Photon time and power draw.** |
| | **The Talk (voice) button.** It runs `s22-assistant` exactly like the volume-up hook does. It was not pressed, because it plays audio and another worker was running audio loopback tests. |
| | **Camera capture.** The page calls the same touchd `camera_capture` as Quickshell; no picture was taken. |
| | **The in-shell model switch and the classic-shell button.** |

## Bugs found and fixed on the way

1. **A stream opened before the POST replays the previous turn.** It sends a snapshot with `done` and the previous `completed.reply`, then the server closes it. The shell showed the previous answer, and no deltas ever arrived. tools/unum-shell has the same ordering.

   Fix: POST first, open the stream about 300 ms later, and reopen it while the turn runs. A `done` snapshot is ignored until the new turn has been seen. Snapshot `liveContent` resyncs the text after a reconnect, and the POST reply is authoritative.
2. **musl on the native root has no zoneinfo.** The clock showed UTC. The shell now uses the chroot's zoneinfo by absolute path.
3. **squeekboard had disappeared at some point**, probably during an earlier seat switch. The original PID 1672 was gone, with no crash line in desktop.log. Terminal now starts it again if it is missing.
4. **s22-touchd counted clean restarts as crashes.** Six deploy restarts triggered the Quickshell fallback; the fallback itself worked. Clean exits after more than 30 s no longer count.

## Phone state at the end

- Default touch shell: fastshell (`/srv/s22/state/touchui/ui-backend` = `fastshell`). `/etc/s22-desktop` = `sway-pixman`, unchanged.
- Installed:
  - `/srv/s22/hardware/bin/unum-fastshell`;
  - an updated `/srv/s22/hardware/bin/s22-touchd` and `s22-desktop-profile`;
  - `/srv/s22/buttons/hooks/power-long`.
- Backup of the replaced files: `/srv/s22/state/fastshell/backup-20261021-195544/`. Staging tools: `/srv/s22/state/fastshell/tools/`.
- Root filesystem: my temporary captures under `/root` briefly filled it (OpenUnum's guard posted "S22 disk / low: 7 MB free"). They were deleted and the staging dir was moved to `/srv`. Free space is back to 20 MB, as at the start.
- No flashing and no package installs. One `s22-reboot recovery`, at 23:14. Audio scripts and the voice loop were untouched.
