# Push-to-talk voice assistant — 2026-10-08 (Claude, s22 session)

Hold **Volume Up**. About 1 s in you hear a rising two-tone beep: the mic is
open. Speak while you keep holding, then let go. A falling beep confirms the
recording. The phone transcribes your speech on the device, asks its own
OpenUnum (session `voice`), and speaks the reply. If you let go before the
beep (or right as it plays), it records a fixed 6 s window instead.

## Pieces (all userspace, installed on the device)
| Path on the phone | Repo source |
|---|---|
| `/srv/s22/hardware/bin/s22-buttons` (hold mode added) | `tools/hardware/s22-buttons.py` |
| `/srv/s22/buttons/ptt-hold` (opt-in flag for hold mode) | — |
| `/srv/s22/buttons/hooks/assistant` → `/srv/s22/hardware/bin/s22-assistant` | `tools/hardware/s22-assistant.py` |
| `/srv/s22/hardware/bin/s22-rec` (`--stop-file`, `S22_REC_READY`) | `tools/hardware/s22-rec.sh` |
| `/opt/s22-asr` in the chroot (sherpa-onnx v1.13.8 static `sherpa-onnx-offline`, models, earcons) | `tools/hardware/asr/install-s22-asr.sh` |
| `/usr/local/bin/start-persistent-desktop` (opt-in OpenUnum boot start; marker `/srv/s22/state/openunum/autostart`) | `tools/persistence/start-persistent-desktop.py` |
| Log: `/srv/s22/buttons/assistant.jsonl` (one line per interaction) | — |
| Optional config: `/srv/s22/buttons/assistant.json` (`engine`, `fixed_s`, `max_s`, `session`, `max_spoken_chars`, ...) | — |
| Backups of the replaced files: `/srv/s22/buttons/backup-20261008/` | — |

The archives come from the official k2-fsa/sherpa-onnx GitHub releases. Each
SHA-256 matches the digest published on the release:
- `sherpa-onnx-v1.13.8-linux-aarch64-static.tar.bz2` `d1c621ed68f2ff77738505ba0137b947141c27e431c12f266a6b4ba218e78a4e`
- `sherpa-onnx-nemo-parakeet_tdt_ctc_110m-en-36000-int8.tar.bz2` `17f945007b52ccd8b7200ffc7c5652e9e8e961dfdf479cefcabd06cf5703630b` (default)
- `sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27.tar.bz2` `9ec31b342d8fa3240c3b81b8f82e1cf7e3ac467c93ca5a999b741d5887164f8d` (alternative, `S22_ASR_ENGINE=moonshine`)

## ASR choice (on the device, CPU, `taskset -c 4-7`, 4 threads, normal load ~14)
Test set: three questions synthesized with Supertonic in a female voice (sid 0) and a male voice (sid 6).
The second set is the same audio played through the speaker and recorded by
the phone's own mic (`s22-rec`, 48 kHz). For the mic test the amp was raised
to level 8 and then restored to 3.

| Model | Wall time per utterance (process start → text) | RTF | Raw TTS (6) | Mic loopback (3) |
|---|---|---|---|---|
| **Parakeet TDT-CTC 110M int8** (default) | 1.1 s | 0.03 | 6/6 content-exact* | 3/3 exact |
| Moonshine tiny v2 int8 | 0.5 s | 0.015 | 5/6 ("Giganathan's") | 3/3 exact |
| Moonshine base v2 int8 | 0.8 s | 0.03–0.09 | 5/6 | 2/3 (one empty) |
| Whisper tiny.en int8 | 0.8–1.4 s | 0.09–0.16 | 6/6 | 3/3 |

\*The TTS dropped "at" in one female sample. Every model agrees on that, so it is a TTS miss, not an ASR miss.
Parakeet is the default. It was the most robust on the raw set, and 0.6 s
is small next to the agent's turn time. The rig fallback (whisper.cpp over
ssh) is not needed and not used. Moonshine base and Whisper tiny were
removed from the phone after the test.

## End-to-end results (device, through the real hook, mic loopback)
The interaction log is `/srv/s22/buttons/assistant.jsonl`. The model is Luna (`openai/gpt-6-luna`) via the phone's OpenUnum.
- **Hold mode.** The release came 5 s after the cue. Transcript: "What is the weather like in Athens tomorrow".
  Reply spoken: "Tomorrow in Athens, expect mostly sunny skies, with a high near 26°C ...".
  Timings: cue 0.78 s after the hook started, ASR 1.0 s, agent 66 s (web search plus open-meteo), TTS and playback 11 s.
- **Early release, fixed 6 s window.** Transcript: "Remind me to call my mother at six in the
  evening". Agent 16.6 s, total 30 s. A second trigger while this one was busy was ignored and logged as `busy`.
- **Text mode** (`s22-assistant --no-speak --text ...`): "What is the capital of Greece?" got the reply "Athens is the
  capital of Greece." Agent 1.9 s.
- If OpenUnum is not running, the hook starts it during recording (~3 s).
  Errors are spoken: "Sorry, I couldn't reach the assistant."

Side effect: the reminder test created a real OpenUnum schedule on the phone
(`57add054-…`, 18:00 UTC, "remind the user: Call your mother"). Deleting it
through the API was blocked by the session's permission classifier, so it is
still there. Remove it in the WebUI (missions/schedules) if it is not wanted.

## Tests
`tools/hardware/test_s22_assistant.py` (new) and `tools/hardware/test_s22_buttons.py` (hold mode)
both run in the pinned host-regression list. `tools/persistence/test_start_persistent_desktop.py`
covers the opt-in OpenUnum autostart.
