# Voice loudness + fillers on every path — 2026-10-22

Owner: "the voice is getting low sound while the old voice sounds different and at a different volume";
"prerecordings should work on all modes".

## Loudness before / after (ffmpeg loudnorm `input_i`, integrated LUFS; sample peak dBFS)

| Item (path) | Before | After |
|---|---|---|
| Supertonic F1 answer (EN default; s22-assistant, converse, tts-s22/agent chat, buttons, SIP classic) | -26.5 LUFS, peak -9.3 | -16.0 to -16.6, TP -2.1 |
| Piper lessac (opt-in engine) | -21.7, -5.4 | -16.3 |
| Kitten (opt-in) | -21.5, -2.4 | -16.7 |
| Paradee (opt-in) | -26.6, -7.7 | -16 (same normaliser) |
| el-antonis Greek answer (all paths) | -21.0, -2.6 | -16.5, -2.0 |
| EN filler clips (66) | -26.3 .. -29.2, peak -11..-13 | -16.1, -2.0 .. -2.6 |
| EL filler clips (66) | -20.8 .. -24.8 | -16.0 .. -16.1 |
| listen.wav / done.wav earcons | -14.6 mean (loudest item, peak -11.2) | -16.0 |
| SIP call recording (greeting + 2 fillers, classic loop, real call) | n/a | -16.4 LUFS, TP -1.2 |
| SIP fast voice (rig Supertonic-3 audio) | rig level, unknown | leveler: active RMS steered to -16 dBFS, ceiling -2 dBFS (-30.9 in -> -17.2 out, gain capped +18 dB) |

The "different volume" was the 10-21 mix: English Supertonic answers and EN clips were 5-12 dB below the
Greek voice, the Greek clips, and the earcons.

Phone-mic loopback (s22-say, graph path, level 6, same text; unnormalised render vs normalised render):
mic speech RMS about -44 dB vs about -33 dB, i.e. +11 dB at the same amp level.
At level 2: -52 vs -46 dB (noise-floor limited).

## "Old voice": every path -> engine/voice

| Path | Engine / voice |
|---|---|
| s22-assistant (hold-Vol-up PTT, Talk button), s22-converse, volume-level beeps (s22-buttons) | s22-say: Supertonic int8 sid 0 (F1) English; Greek text -> el-antonis (Piper-medium, VoxCPM2 distill) |
| agent text_to_speech tool / agent chat voice (tts-s22 -> s22-ui -> touchd) | s22-say, engine arg default `supertonic` (touchd forces it, so Greek text must pass engine el-antonis) |
| Filler clips | rendered by s22-say: EN Supertonic F1, EL el-antonis (same as answers) |
| SIP call, classic loop (when the rig is unreachable) | WAS: sherpa Supertonic sid 0 direct (English voice for Greek text too, not normalised). NOW: s22-say render-only (same engine selection + normaliser) |
| SIP call, fast voice (normal case) | rig unum-voice: Supertonic-3 F1 (a different model from the phone's Supertonic 2026-03-06 int8), Greek included, so on calls Greek is Supertonic-3, not el-antonis. This is the audible "old voice" difference; it lives on the rig (tts_engine/tts_voice in unumvoice/config.py), the phone can only level it |
| Earcons | synthetic tones |

Chosen voices: English = Supertonic F1 (sid 0, the owner's female choice; Piper lessac is 2 dB louder/crisper but
robotic, kept only as an opt-in engine), Greek = el-antonis. Both everywhere except fast-voice calls.

## Fillers per path

See the commit message / report: assistant (verified live), converse (shared code, host tests), SIP classic
(verified live, real call), SIP fast voice (host tests only; rig service was down), agent text_to_speech/touchd
(host tests; deploy pending a touchd restart).

## Later on 10-22: the owner's own voice everywhere + Greek speech input

Owner: "the default voice must be my own voice everywhere, stop switching between voices"; "I want to speak Greek
to the phone and get the replies in Greek".

### Voice: one voice, his own (VoxCPM2 + LoRA "antonis" on the rig)

| Path | Before (this morning) | Now |
|---|---|---|
| s22-assistant / Talk button / Agent card / s22-converse / s22-buttons speech | Supertonic F1 (EN), Piper el-antonis (EL) | s22-say engine `antonis` (default): rig VoxCPM antonis, EN + EL |
| spoken thinking chunks | same as answers | same as answers (antonis) |
| filler clips (66 EN + 66 EL) | Supertonic F1 / el-antonis | re-rendered in antonis (strict: no fallback voice), -15.8..-16.8 LUFS, peak -2.0 |
| agent text_to_speech (tts-s22 -> s22-ui -> touchd) | `--engine supertonic` forced | `antonis`; a model-chosen engine is ignored unless `allow_engine_override` |
| SIP call, fast voice (rig unum-voice) | Supertonic-3 F1 + Piper el-antonis | VoxCPM antonis streamed per clause (unum-voice 780698c), Piper el-antonis fallback |
| SIP call, classic loop (rig down) | s22-say | s22-say -> antonis, or Piper el-antonis when the rig is down |
| offline / rig slow (no first audio in 2 s, clause over 3 s + 0.12 s/char) | n/a | Piper el-antonis on the phone (same voice distilled), rig skipped for 60 s |

Rig side (tools/hardware/voice/rig/install-rig.sh): `s22-voxcpm-tailnet.service` relays 100.76.5.104:8890 -> the
loopback-only voxcpm-greek :8890 (no restart of the voice server), `s22-whisper.service` serves whisper
large-v3-turbo on 100.76.5.104:8891 (Vulkan, MemoryMax 4G, `--convert`). Both bind the Tailscale address only.

English in the owner's voice (VoxCPM antonis, whisper large-v3-turbo round trip, 3 EN + 3 EL sentences): every
sentence transcribed word for word ("Hello, I am your phone assistant. The battery is at 42% and charging.").
English through the offline Piper el-antonis fallback is only partly intelligible (1 of 2 sentences correct,
"Tomorrow the weather in Athens will be sunny" -> "The mode of weather in Athens is a sign"): acceptable only as
the rare fallback; `S22_TTS_OFFLINE_EN=supertonic` trades the voice for clarity if the owner prefers.

Rig voice speed depends on the main model's GPU load: idle RTF 0.89-0.95, first audio 0.16-0.22 s; under Halogen
load (flash_serve decoding) RTF 1.3-2.1. Phone render of one clause (rig + fast -16 LUFS normalisation, 0.4-0.5 s):
2.7-4 s idle, 4-6 s loaded. Slow first chunks are now covered by "still thinking" gap fillers (same language).
Answers are spoken in chunks (short first clause <= 70 chars, each next chunk no longer than the one playing,
rendered while it plays).

### Greek speech input

The phone sends the recording to the rig's whisper (language auto-detect narrowed to el/en; a guess outside them
is redone with the likelier of the two), and falls back to Parakeet (English only) when the rig is unreachable.
Every turn carries a reply-language hint ("The owner spoke GREEK. Reply in Greek ..."); fixed phrases ("One
moment", "Sorry, I didn't catch that", ...) and fillers follow the language.

Live on the phone (assistant fed a WAV of the question synthesized in the owner's voice, speaker level 4):

| Test | Transcript (ASR, ms) | Reply | Voice | Question end -> first audio (filler) | -> answer audio |
|---|---|---|---|---|---|
| EL, rig up | "Πες μου σε μία πρόταση ποια είναι η πρωτεύουσα της Γαλλίας" (whisper el, 1241) | "Η πρωτεύουσα της Γαλλίας είναι το Παρίσι." | antonis-rig | 2.45 s (EL filler) | 11.9 s (agent 5.9 s, render 4.8 s, GPU loaded) |
| EN, rig up | "In one sentence, what is the capital of Italy?" (whisper en, 1057) | "Rome is the capital of Italy." | antonis-rig | 2.32 s (EN filler) | 8.7 s (agent 3.2 s, render 4.4 s) |
| EL, rig TTS blocked (wrong port) | same, whisper el | same Greek reply | el-antonis-fallback | 2.5 s | 14.2 s (Piper render 9.8 s, 2 EL gap fillers) |
| EN, rig TTS + ASR blocked | "In one sentence, what is the capital of Italy?" (Parakeet, 5000) | "Rome is the capital of Italy." | el-antonis-fallback | ~6 s | 15.9 s |

Calls (unum-voice client, real-time WebSocket turns): TTS first audio 392-415 ms, endpoint -> first audio
569-647 ms with a fast LLM, reply -15.7 LUFS, whisper reads the reply back word for word.
