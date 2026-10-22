# Audio

Part of the [porting guide](../PORTING.md). Speaker and microphone work on the S22 through
Samsung's ABOX audio DSP, including its firmware "graph" path that Android uses. Earpiece over the
graph path and call audio are not done ([README](../../README.md#what-works-and-what-doesnt-yet)).
This took the longest of any subsystem: the sound card registered on 2026-09-22, the first sound
came out on 2026-10-06, and the DSP graph path worked on 2026-10-21 ([HISTORY.md](../HISTORY.md),
[STATUS.md](../../STATUS.md)).

## 1. The sound card needs its firmware at the right time

The audio topology asks for `sectiongraph_tplg.bin` about 3 s after boot and then waits 60 s in the
sysfs fallback. The boot ramdisk carried only part of the stock audio firmware, so the card came up
late and incomplete; the speaker amps' protection DSP firmware and the haptics firmware were missing
too.

Fix: at about 10 s, copy any missing files from our userdata copy into PID 1's `/vendor/firmware`
and answer pending `/sys/class/firmware/<name>` requests **for those names only**. After that the
card was ready at about 11 s instead of 64 s, with more controls
([evidence/audio-buttons-20261006](../../evidence/audio-buttons-20261006/README.md), root cause 1).

**Don't** unbind and rebind the topology driver at runtime to reload firmware: on this kernel it
used freed data and **panicked the kernel** (the phone rebooted by itself). Topology changes need a
reboot ([evidence/audio-buttons-20261006](../../evidence/audio-buttons-20261006/README.md), "Safety incident").

## 2. Android's mixer defaults were never applied

Android's audio HAL applies the controls in `mixer_paths.xml` before anything plays. Without them,
the amps' link had rate, width and channels set to 0. We flatten the stock file into `amixer -s`
batches: one `defaults` batch plus one per route (`media-speaker`, `media-mic`, …) with a matching
`.reset` batch, using [`gen-mixer-batches.py`](../../tools/hardware/audio/gen-mixer-batches.py)
([tools/hardware/audio/](../../tools/hardware/audio/)). Applying `defaults` started the speaker
protection DSP on both amps; the amps' own telemetry then recorded speaker excursion and coil
temperature during playback ([evidence/audio-buttons-20261006](../../evidence/audio-buttons-20261006/README.md)).

This works for any phone whose vendor ships `mixer_paths.xml`.

## 3. Some outputs worked directly, the DSP graph did not, until we read the stock library

The hardware-direct DMA outputs (RDMA6 for playback, WDMA4 for capture) worked first, after fixing
two format details: the capture interface delivers four 16-bit slots (main mic in slot 0), and the
speaker path distorted 22.05 kHz audio, so we resample to 48 kHz stereo in software
([evidence/audio-buttons-20261006](../../evidence/audio-buttons-20261006/README.md), "Follow-up").

The firmware-graph DMAs (RDMA2/3) accepted streams but never moved data, for weeks. The answer
came from the stock **`libaudioproxy2.so`** (Exynos AudioProxy, which holds the real ABOX/PCM
logic; the main HAL is a thin layer over it). Its `get_pcm_device_number()` returns 100/102/105 for
playback and 110–116 for capture. Those are **virtual "calliope" frontends**. RDMA2 is not a
frontend at all: it is a source selector *inside* the firmware graph
(`calliope_2 → VPCMOUT_DAI2 → VDSP3 → RDMA2 → speaker`). Android never opens RDMA2; it plays into
`calliope_2`, and the kernel's DPCM then opens the backend DMAs by itself. We had been writing into
the graph's output end and waiting for a firmware interrupt that only comes when the frontend is
open ([evidence/audio-20261021](../../evidence/audio-20261021/README.md), TL;DR).

Fix: play into `hw:0,102` or `hw:0,100`, record from `hw:0,110`, with the stock mixer routes and
HAL-sized periods (480–960 frames). Proof: the phone's own mic, recording through the graph, heard a
1 kHz test tone from the speaker at about 1000× the noise floor
([evidence/audio-20261021](../../evidence/audio-20261021/README.md)).

Things that break it (all found the hard way):

- periods of 6000 frames lost audio and, with one pipeline, **crashed the DSP**; keep ≤ 960 frames;
- mmap on a calliope PCM leaks the device until reboot
  ([CONTRIBUTING.md](../../CONTRIBUTING.md#4-safety-rules-non-negotiable));
- two tools applied the once-per-boot `defaults` batch under different locks; a concurrent first
  use re-applied defaults mid-capture and broke recording. They now share one lock
  ([evidence/audio-20261021](../../evidence/audio-20261021/README.md)).

## How to approach audio on another phone

1. Get the card registered: modules, topology firmware and its timing (watch `dmesg` for firmware
   fallback waits).
2. Apply `mixer_paths.xml` defaults, then one route at a time, with a reset for each.
3. Find out which PCM devices Android really opens. Read the audio HAL and the libraries it loads
   (on Exynos: AudioProxy) for device-number tables, with `readelf`, `strings` and a disassembler
   ([backups-and-vendor-files.md](backups-and-vendor-files.md#reading-binaries-without-running-them)).
4. Verify acoustically, not by "the stream ran": record the phone's own speaker with its own mic,
   check the tone's frequency and level, or transcribe speech with Whisper
   ([evidence/audio-buttons-20261006](../../evidence/audio-buttons-20261006/README.md)).
5. Keep the volume low during tests and restore the owner's setting afterwards
   ([evidence/audio-20261021](../../evidence/audio-20261021/README.md)).

On the phone: `s22-say` speaks, `s22-rec` records, and `audio-abox-recover.sh` recovers the DSP
after a crash ([STATUS.md](../../STATUS.md#audio-firmware-graph-works--2026-10-21)).
