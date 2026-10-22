# S22 haptics (vibration motor)

Status 2026-10-22: working. Tool: `tools/hardware/s22-haptic.py`
(installed on the phone as `/srv/s22/hardware/bin/s22-haptic`).

## Hardware / driver

* Cirrus CS40L26 haptic driver at i2c `17-0040` (IRQ `cs40l26`), wrapped by
  Samsung's `sec_vibrator_inputff`.  It appears as an input device named
  `sec_vibrator_inputff` (`event6` on the tested boot; look it up by name).
  Force feedback capabilities: `FF_PERIODIC`, `FF_CUSTOM`, `FF_GAIN`;
  there is no `FF_RUMBLE`, no `/sys/class/leds/vibrator`, no `timed_output`.
* The DSP firmware already loads at driver probe (log:
  `CS40L26 loaded with 143 RAM waveforms`, firmware revision 39.2.22 per the
  audio-buttons evidence).  Nothing to stage.
* Waveform selection: upload an `FF_PERIODIC` effect with
  `waveform=FF_CUSTOM` and `custom_data = int16[2] {bank, index}`
  (bank 0 RAM wavetable, 1 ROM, 2 buzz).  Play with `EV_FF/<id>/1`, stop
  with value 0.  The input device has only a handful of effect slots
  (a second client uploading three effects got `ENOSPC` while the daemon held
  three), so the daemon owns the slots and the direct mode is a fallback only.
* Measured waveform durations (driver log HAPTIC -> STOPPED), RAM bank:
  idx 34/38 = 6 ms, 40 = 8 ms, 37/39 = 9 ms, 8 = 19 ms, 3 = 41 ms,
  1 = 73 ms, 7 = 184 ms, 16 = 507 ms, 17 = 694 ms; ROM 8/10 = 24/23 ms.
  Chosen: tick = RAM 39 (9 ms), buzz = RAM 3 (41 ms), long = RAM 16 (stopped
  at the requested length).  Indices are overridable in config.json.

## Interface for callers (the shell)

Cheapest: one datagram to the unix socket `/run/s22-haptic.sock` (mode 0666),
fire-and-forget, ~0.1 ms of caller time (the CLI measured 0.3-0.7 ms including
Python startup-free sendto).

    tick                     ~9 ms click (keyboard taps)
    buzz                     ~41 ms
    pattern ON,OFF,ON,...    ms, starts with ON; each ON <=15 ms -> tick,
                             <=60 -> buzz, else long waveform stopped on time
    stop                     cancel pattern, stop motor
    status                   (send from a bound socket to get the JSON reply)

Rust sketch: `UnixDatagram::unbound()?.send_to(b"tick", "/run/s22-haptic.sock")`.
Ignore errors (no daemon = no vibration; never block the UI).

CLI (same commands): `s22-haptic tick|buzz|pattern 40,80,40,80,200|stop|status`,
`s22-haptic latency`, `--direct` to bypass the daemon.  Without the daemon the
CLI opens the device itself (about 200 ms because of effect upload).
Start the daemon: `/srv/s22/hardware/bin/s22-haptic serve` (not yet wired into
boot persistence; a reboot needs it started again).

## Safety

* `/srv/s22/state/haptics/config.json`: `{"enabled": true}`.  `false` (or an
  unreadable / non-boolean value) makes every command a no-op; a missing file
  means enabled.  Re-read on change, no restart needed.
* Optional overrides (non-negative ints): `max_segment_ms` 500,
  `max_pattern_ms` 3000, `max_segments` 16, `min_interval_ms` 20,
  `window_ms` 10000, `window_max_plays` 150, `window_max_on_ms` 3000,
  `tick_index`, `buzz_index`, `long_index`.
* Over-limit requests are dropped silently (counted in `status`).

## What was verified (honestly)

* Driver state: every command produced `playback_effect` -> `VIBE_STATE_HAPTIC`
  -> `MBOX_COMPLETE/STOPPED` in the kernel log and the `cs40l26` IRQ counter
  rose; with `enabled:false` the counter did not move and `dropped_disabled`
  counted the requests.
* Latency (driver log, play write -> HAPTIC state): median 5.2 ms
  (2.4-11.3 over 5).  Client side: 0.3-0.7 ms to hand the command to the daemon.
* Physical motion: the sensor hub (LSM6DSO accel) was started
  (`fs_ready` after copying the verified firmware into
  `/proc/1/root/vendor/firmware/sensorhub/`, same as SENSORHUB_WORKING) and
  sampled at its default ~5.5 Hz.  Idle noise is +-11-23 counts; a 300 ms
  pattern gave dz -231, RAM 7 (184 ms) gave dz +190 and -264.  Short
  taps (9-41 ms) fall between samples at that rate and were mostly not seen
  (poll_delay writes hang the kernel driver in a busy loop; do not write it).
  So the motor is proven to move the phone for >=180 ms events; tick/buzz
  strength and feel are NOT measured and still need a hand test.
