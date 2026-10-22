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
