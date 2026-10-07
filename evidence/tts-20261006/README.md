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

## Female default voice (owner decision 2026-10-07: "pick a female one")
Supertonic's `voice.bin` holds 10 styles. A median-F0 estimate of the same
sentence gives sid 0–4 = 190/280/190/189/159 Hz (female styles F1–F5) and
sid 5–9 = 120/97/98/127/118 Hz (male). whisper transcribed sid 0, 2 and 3 exactly.
`s22-say` now defaults explicitly to **sid 0 (F1, female)**, overridable with
`S22_TTS_SID` / `--sid`. The rig sample is `~/s22-tts-samples/default-female.wav`.
Acoustic check (phone mic recording its own speaker, amp temporarily raised to level 8
and then restored to 3): whisper → "Hello Antonis, this is my new default voice,
speaking from the phone."
