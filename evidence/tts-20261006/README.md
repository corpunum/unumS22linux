# On-device neural TTS — 2026-10-06 (lead session)

All synthesis runs on the phone's CPU. It uses sherpa-onnx v1.13.8
(official static aarch64 build, glibc, so it runs inside the Arch chroot)
pinned to the A710/X2 cores (`taskset -c 4-7`, 4 threads). The models are in
`/opt/s22-tts` (~390 MB), installed with `tools/hardware/tts/install-s22-tts.sh`.

| Engine (model) | Size | RTF on S22 | whisper base.en of the raw WAV |
|---|---|---|---|
| **supertonic** (int8 2026-03-06), default | 80 MB | **0.16** | exact apart from "Antonas" |
| kitten nano v0.8 int8 | 29 MB | 0.29 | exact |
| piper en_US lessac-medium | 64 MB | ~0.26 | exact apart from "Antonus" |
| kokoro v0.19 int8 | 98 MB | 1.24 (slower than real time) | exact |
| kitten micro v0.8 (tested, not kept) | 42 MB | 0.33 | exact apart from "Antonus" |

Test sentence: "Hello Antonis. This is your S22 speaking with a neural voice,
running entirely on the phone." The resident 4B model and the usual load
(~14, D-state TrustZone threads) were running at the same time. Copies of the
five WAVs are on the rig in `~/s22-tts-samples/` so the owner can listen
and pick a voice.

## s22-say
`s22-say [--engine supertonic|kitten|piper|kokoro|espeak] [--sid N] TEXT`.
The default is `supertonic` (override it with `S22_TTS_ENGINE`). If the neural
engine fails, it falls back to espeak-ng. Playback still uses the verified
`s22spk` → RDMA6 → CS35L41 path.

## Acoustic end-to-end check
The phone's own mic (`s22-rec`) recorded `s22-say "Hello Antonis. I am the S22,
now speaking with a neural voice that runs on the phone itself."` (supertonic).
whisper.cpp base.en on the rig transcribed it as:
"Hello Antonus, I am the S22. Now speaking with a neural voice that runs on
the phone itself."

Note: the button daemon's volume was at level 3 (−28 dB), which the owner
set during the button test. At that level the mic loopback is too quiet to
transcribe. The test temporarily set the amp gain to level 8 and then
restored level 3.

## NPU
Not attempted yet. NPU runtime is still unproven; see the NPU section of STATUS.md.
