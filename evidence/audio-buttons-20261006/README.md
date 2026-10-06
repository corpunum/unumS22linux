# Speaker output, TTS and button daemon — 2026-10-06 (lead session)

Device: SM-S901B/DS native RECOVERY, image unchanged
(`b10412715756da3cc8ee221368b49f179cc0c64ab7bd2802976480905e6d8d2f`).
No partition was written. Three recovery-target reboots: one unplanned (panic,
below) and two deliberate `s22-reboot recovery` to test boot-time firmware.

## Root causes found
1. **Missing firmware at the right time.** `firmware_class.path=/vendor/firmware`
   resolves in the AOSP ramdisk root (PID 1). The ramdisk only carries part of
   the stock audio firmware. `sectiongraph_tplg.bin` (ABOX firmware DSP graph →
   `ABOX VDSP*` controls), the CS35L41 speaker-protection images
   (`cs35l40-{rcv,bot}-dsp1-spk-prot.*`) and the CS40L26 haptics image were
   missing. The topology asks for sectiongraph at ~3 s and waits 60 s in the
   sysfs fallback. Fix (`start-persistent-desktop`): at ~10 s, copy any missing
   files from userdata `hardware/firmware/audio-fyi3` into
   `/proc/1/root/vendor/firmware` and answer pending `/sys/class/firmware/<name>`
   fallback requests for those names only. Verified after reboot: dmesg
   `abox-tplg: loaded sectiongraph_tplg.bin` at 11.3 s, card ready at about 11 s
   (it was 64 s before), 1836 controls (was 1754), haptics firmware revision 39.2.22.
2. **The Android HAL mixer state was never applied.** UAIF1 (the amp TDM link)
   had rate, width and channel set to 0. The fix uses
   `tools/hardware/audio/gen-mixer-batches.py`, which flattens the stock vendor
   `mixer_paths.xml` into `amixer -s` batches: `defaults` (320 controls) plus
   `media-speaker`, `media-handset` and the other routes. Applying `defaults`
   loads and starts the amp protection DSP (`DSP1: Execution started` on both amps).
3. **The firmware-graph DMAs (RDMA2/3, WDMA0-3/5-7) never advance.** Calliope
   accepts open/hw_params/trigger ("source assigned for the entity: RDMA2") but
   never consumes data, even after the VDSP routes are set. This is still
   unresolved. The hardware-direct **RDMA6/RDMA9 (playback) and WDMA4 (capture)
   do work**.

## Device-verified results
- `aplay` on `plughw:0,6` with route `media-speaker` completes with hw_ptr advancing.
- Speaker protection telemetry (`cirrus_bd`) after playback: max excursion
  0.188 mm / 0.236 mm, coil temperature estimate 46.6 °C / 49.5 °C. These are
  measured from the amps' own V/I sense.
- Acoustic loopback through the bottom DMIC (UAIF6 → NSRC4 → WDMA4): with a
  1 kHz tone playing, the capture FFT peak is about 80× the noise floor
  (silence RMS 0.0006, during the tone 0.050). Because of a known capture
  channel-layout mismatch, the peak appears at 500 Hz and time runs 2× fast in
  the recording.
- `s22-say "Hello Antonis. This is your S22 speaking from native Linux."`
  ran with rc 0. The mic loopback RMS envelope follows the words (0.03–0.07
  during speech, 0.001 in the gaps).
- `s22-buttons` daemon: an injected evdev volume-up press on event0 was
  logged. Volume went from 7 to 8, the amp gain was set, and "volume 8" was
  spoken. **No physical key press has been observed yet. The owner still
  needs to test the physical keys.**

## Safety incident
Unbinding/rebinding `0.abox-tplg` at runtime to reload the topology
**panicked the kernel**: `abox_tplg_restore` ran from `abox_runtime_resume`
on freed topology data. The phone rebooted itself into the same RECOVERY with
desktop, Wi-Fi and Tailscale healthy. Do not repeat this. Topology changes
need a reboot, using the boot-time firmware staging above.

## Follow-up (same day): microphone, transcription-verified speech, earpiece
- **Mic capture layout.** UAIF6 → NSRC4 → WDMA4 (pcm 16) delivers **4 × 16-bit
  slots**; the main mic is slot 0. With 2-channel capture the hardware still
  wrote four slots, which explains the 2× time and frequency distortion seen
  earlier. `s22-rec SECONDS OUT.wav` records 4 slots and writes mono 48 kHz
  from slot 0. Check: a 1 kHz tone recorded with its peak at 1001 Hz.
- **Speech garbling root cause.** RDMA6 accepts 22.05 kHz mono (espeak's
  native format), but its ASRC distorts it. `alsa-s22.conf` defines
  `s22spk`, which converts to 48 kHz stereo in software before `hw:0,6`.
- **End-to-end check with whisper.cpp base.en** on the rig, transcribing the
  phone's own mic recording of the phone's speaker:
  - `s22-say "Testing the microphone. One, two, three."` → "Testing the microphone. 1, 2, 3."
  - `s22-say "Hello Antonis. The microphone and the speaker both work now."` →
    "Hello Antonis, the microphone and the speaker both work now."
  - `s22-say --out earpiece "This is the earpiece speaking. Can you hear me?"`
    → "This is the dear beast speaking. Can you hear me?" Mic level is about
    10× lower than the loudspeaker, as expected for the earpiece.
- Closing a route now resets only that route's controls
  (`<route>.reset.amixer`), so playback and recording can run concurrently.

## Owner button test and Power fix (same day)
- Owner pressed the physical keys: `events.jsonl` logs Volume Up (short presses and a 2508 ms hold),
  Volume Down (219 ms), and four Power presses. The owner confirmed that
  volume stepping, the spoken level and the hold → assistant prompt all work. **Volume keys are accepted.**
- **Power defect:** Hyprland's DPMS off detaches the CRTC (atomic state:
  crtc enable=0, connector crtc=null). On this stack, though, no panel or DSIM
  activity follows (no sleep-in in dmesg or the panel cmd_log), and the
  backlight stays at 128. The command-mode OLED therefore keeps scanning a
  stale, half-updated frame from its own RAM — this is the corrupted half-black
  screen the owner reported.
- **Fix:** `s22-display on|off|toggle` keeps scanout running and composites
  pure black via a Hyprland `screen_shader` (OLED black = pixels off). It also
  sets the backlight to 0 and disables the `sec_touchscreen-2` touch device.
  `s22-buttons` maps a short Power press to `s22-display toggle`. The Hyprland
  `XF86PowerOff` DPMS bind is removed from the config, with a backup kept in
  `/srv/s22/lead-20261006/`.
- **Verified** with an injected Power press, both before and after a reboot:
  - Press 1: framebuffer capture is all black (max 0), backlight 0 (actual 2).
  - Press 2: normal frame (mean 84), backlight back to 128 (actual 205).
  - After the reboot the daemon and firmware staging autostarted, and
    `s22-say` was transcribed correctly by whisper.
  - The owner still needs to re-test the physical key.
- **Note:** Arch Python that spawns subprocesses (e.g. ctypes `find_library`)
  must run under `/usr/local/libexec/s22-close-range-compat`. Otherwise the
  known kernel `close_range` bug leaves unkillable spinning tasks; two
  appeared here and were cleared by the reboot.
