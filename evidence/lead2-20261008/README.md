# S22 lead2, 2026-10-08: GPU fallback, phone stack, voice, TTS (device evidence)

Boot id for all trials: `25d961f5-3261-477c-84a2-af9f281ff705` (no reboot). Phone muted throughout:
CS35L41 `Left/Right Digital PCM Volume` = 0 checked before and after every audio-adjacent step;
all TTS was rendered to files (`S22_SAY_OUT`), never played.

## GPU (vendor Vulkan HAL, isolated Bionic root, `run-trial.py` harness)
Qwen3.5-2B Q4_0 copy staged in the llama-vulkan root, sha256 `cd70221b…` = resident file.

| trial | pp128 t/s | tg64 t/s | pp128 @4096 | tg64 @4096 |
|---|---|---|---|---|
| GPU default batch, FA on (cool) | 58.5 (pp-fa3) | 14.6 (v1 short) | 48.9 | **10.39** |
| CPU control same harness | 77.9 (v1 short) | 11.7 (v1 short) | 44.2 | **8.47** |
| T1b GPU `-b 32 -ub 32` (warm, G3D throttling) | 59.5 | 10.3 | 35.8 | 9.57 |
| T1b CPU `-b 32 -ub 32` (warm) | 44.6 | 8.9 | 27.0 | 8.61 |

- Decode at depth 4096: 1.23x (cool) / 1.11x (warm). Plan gate (>1.2x) passed on the cool run only: marginal.
- v1's 7.3 t/s GPU prefill was a cold/low-memory artefact (kswapd dump in that delta); warm GPU prefill is 42–65 t/s.
- Greedy text parity GPU vs CPU: byte-identical (sha256 `9593a51d…`).
- Kernel gate: no sgpu fault/reset/hang/OOM in any delta (only thermal `tmu_pm_qos` lines). Two trials stopped at
  the harness' 42 °C post-check (charging); results were already captured.
- **T2** `llama-server` (same pinned source, `-DLLAMA_BUILD_SERVER=ON`, NDK r27c, stripped sha256 `8fe73509…`):
  - first run with default `--cache-ram` leaked ~40 MB host RAM per request (1536→808 MB over 20 req) → stopped;
  - **T2b** `--cache-ram 0`: 31.7 min trial, 20/20 burst + 20/20 soak requests, MemAvailable flat at ~1530 MB,
    tg 13–15 t/s / pp ~75 t/s cool, empty GPU kernel delta (`gpu/t2b-*.receipt.json`, `gpu/t2b-soak.jsonl`).
- Promoted (owner approval 2026-10-08): keepalive service `llama` (config `tools/hardware/keepalive-config.s22.json`),
  `-c 16384 --reasoning off --cache-ram 0`. OpenUnum default stays `openai/gpt-6-luna`;
  `routing.fallbackProviders=["llama-cpp-local"]`, `llamaCppLocalBaseUrl=http://127.0.0.1:8090`.
  Forced fallback turn via OpenUnum: OK, 140 s first turn (6820-token prompt at 50.8 t/s, then 9.7 t/s);
  keepalive `pin_model` restored Luna within one 20 s pass; the next turn ran on Luna.

## Modem + s22-phoned (no SIM)
- `s22-modem-up` → `modem_state ONLINE` in 8 s; now a keepalive service (`modem`, health = cpif modem_state).
- `s22-phoned` simulate mode on the phone (Python 3.14): tx gate refusal, 112 refusal, Greek UCS2 SMS inject,
  incoming call, /events, TCP + unix socket.
- Real CP: `sim CARD_NOT_PRESENT`, LTE `reg_status denied` (no SIM, emergency-only), RSRP −114 dBm; clean shutdown.
  Running under keepalive on 127.0.0.1:8095; `tx_enabled` absent, allowlist empty.

## OpenUnum s22-phone plugin
Installed in the phone OpenUnum (v2.10.0), bridge attached to phoned (first run skipped 26 backlog events).
Luna turns: `phone_status` → "SIM card is not present, and the modem is online"; `sms_send` → refused
("tx_enabled" missing), recorded in outbox as refused.

## Voice
- `s22-converse --silent-verify` (TTS-made questions): 2 turns, one session, context carried
  ("favourite colour green" → "Your favorite color is green."), replies rendered only.
- `s22-sip` rig↔phone over Tailscale (no registrar): tones both ways (600 Hz / 1 kHz), DTMF 4,2, rx=tx, 0 lost.
  Real agent voice loop: Parakeet transcript exact, Luna reply "The capital of Greece is Athens.",
  rig whisper.cpp heard "…The capital of Greece is Athens." (asr 1.2 s, chat 25 s, tts 1.1 s).

## Paradee TTS (branch s22/paradee-tts-20261008 merged)
Installed with `install-paradee.sh` (fixed: busybox `sha256sum` has no `--quiet`). 4 threads, cores 4–7:
load 0.67–0.74 s, first audio 0.105 s, RTF 0.061–0.083. whisper.cpp on the rig: exact transcript.
`--rollback` exercised (reinstall → rollback restored `paradee.prev`). Default engine still supertonic.

## Raw files
`gpu/` receipts + stdout per trial, `device/device-logs.jsonl` (phoned status, assistant/converse/voip turns,
keepalive events).
