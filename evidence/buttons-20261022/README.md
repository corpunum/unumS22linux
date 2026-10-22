# Physical buttons in unum-fastshell (2026-10-22)

The volume and Power keys are now handled inside the Rust shell (`tools/unum-fastshell/src/buttons.rs`) instead of
s22-buttons spawning amixer/s22-say/s22-touchd processes. Everything was measured on the phone (kernel 5.10.260,
load average ~15) with [`fs-button-latency.py`](../../tools/unum-fastshell/fs-button-latency.py): it injects key
events into the real key nodes and polls the effects every ~1 ms. These were injected events, not a physical finger.
"down" = injected key-down, "up" = key-up (holds of 80–120 ms).

## Before / after

| Effect | Before (s22-buttons) | After (fastshell) |
|---|---|---|
| Volume step: level written | on release, +6 ms (≈ 90 ms after down) | **on key-down, +1.6–4.3 ms** |
| Volume step: amp gain changes (during playback) | +40–54 ms after release (spawned chroot amixer, 121–140 ms after down) | **+4.3 / 6.5 ms after down** (one ALSA ioctl) |
| Volume feedback | spoken "volume N", audio starts **3.3–3.8 s** after release | **on-screen level bar 2.6–8.4 ms after down** (plane fb change); internal key → flip 11.5–16.5 ms |
| Power with the screen off → panel on | 520–560 ms after release (620–660 ms after down) | **177–211 ms after down** (wake on key-down; DPMS-on itself takes ~170 ms in the kernel) |
| Power with the screen on → lock | lock cover +10 ms after release (shell), panel off 260–268 ms after release | lock cover **+6–16 ms after release**, panel off **150–166 ms after release** |
| Power + Volume down (screenshot) | – | from screen off: panel on 177 ms, PNG written 102 ms later (28 ms when the screen was already on), flash |
| Tap → page (Settings tile, 20 taps) | p50 12.3 ms (screen-timeout build) | p50 13.4 ms, highlight p50 8.6 ms: no regression |

Files: `before-volume.jsonl`, `before-power.jsonl`, `after-1-volume-power.txt`, `after-2-wake-playback.txt`
(wake by volume key, amps during a level-1 s22-say playback), `after-3-chords.txt` (screenshot, held chord,
Power + Volume up, double press), `after-tap-latency.json`, `live-screenshot-power-voldown.png` (the file the chord
saved: the lock cover, no flash and no level overlay in it), `offscreen-*.png` (Settings with the Buttons section,
level overlay unlocked/locked).

## Verified on the phone

- Volume up/down: level file, amps during playback, overlay, wake from screen off (level written 4.3 ms after down,
  before the 180 ms DPMS-on; the first build did DPMS first and fixed after the measurement in after-1).
- Power tap lock / wake, Power twice → Camera page (log `[Wake, Camera]`), Power + Volume up ×2 (`KeepAwake` twice),
  Power + Volume down from screen off and from screen on (two PNGs in the gallery).
- Power + Volume down **held 1.4 s**: no fallback (ui-backend stayed fastshell, same shell pid); s22-buttons logged the
  release with `"chord": true`.
- Power held 1.3 s alone: `hooks/power-long` ran (hook.log), Quickshell selected, shell stopped, marker removed.
  Then s22-buttons alone (fallback): volume up 1 → 2 with the fresh level, spoken at level 2.
  `s22-desktop-profile fastshell` brought the shell back and it reclaimed the keys.

## Only in unit tests (not on the phone)

- Push-to-talk by holding Volume up (would start a recording and an agent turn); the shell uses the same hook, the
  same environment and the same `ptt-release` note as s22-buttons.
- Hold Volume down = mute/unmute; the screenshot under sway (grim); the Settings toggle by touch (rendered
  offscreen only); a real finger; Power waking from a real suspend (the PMIC IRQ path is unchanged; the shell only
  needs the key event after resume).

## Bugs found on the way

1. s22-buttons kept the level in memory. s22-keepalive's mute guard resets the saved level to 0 every ≤20 s, but
   the next press went from the stale level: the "before" run went 7 → **8**, and spoke "volume 8" at -8 dB four
   times (my mistake: I expected 0 → 1). Fixed: both s22-buttons and the shell read `/run/s22-say.level` at
   each press.
2. The shell's first build did the same (level read once at start), and ran the wake's blocking DPMS-on before the
   level write. Fixed: level read at each key-down, Level effects first.
