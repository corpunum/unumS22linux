# OpenUnum on the S22 — 2026-10-06 (lead session)

## What runs where
- **Phone:** the OpenUnum server and WebUI (`~/main` at d551ed22, `git archive`,
  so only committed code). It runs in the Arch chroot (`/mnt/omarchy-trial/opt/openunum`)
  on Node v22.23.3 (official linux-arm64 tarball, SHA256 checked) with
  `pnpm install --prod`. It listens on **127.0.0.1:18880**. Use the phone
  browser, or `ssh -L 18880:127.0.0.1:18880`.
- **Brain:** the rig's Halogen (`openai/qwen3.8-flash-next-uncensored-lean`)
  at `http://100.76.5.104:8080` over Tailscale. Halogen already listens on `*:8080`, and
  the phone (100.95.191.103) reached `/v1/models`. **No rig, firewall or
  Halogen change was made.**
- **Voice:** stays on the device. The agent's `shell_run` calls `s22-say` (neural TTS, local).

## Files (tools/openunum-phone/)
- `openunum.json`: provider `llama-cpp-local` → rig Halogen;
  `autoHealEnabled:false`, `autonomyMasterAutoStart:false`, media swap
  off, browser off. The phone is a client, so no background loops compete
  for the rig's single Halogen slot.
- `s22-openunum.sh` → `s22-openunum start|stop|status|log` (manual; no boot hook).
  It sets `OPENUNUM_FIRST_TOKEN_CAP_MS=900000`. OpenUnum treats the
  100.x tailnet address as remote and allows only a 60 s first token. Halogen
  is batch-1 and is often busy with rig work, so turns failed with
  `first token timeout` until the cap was raised.
- `install-host-wrappers.sh`: `s22-host CMD` runs a command on the Alpine
  host root via `/proc/<sshd>/root`. PID 1 is native-guardian in the ramdisk
  root, so `/proc/1/root` is the wrong root. It also installs chroot wrappers
  for `s22-say`, `s22-rec`, `s22-modem`, `s22-modem-up` and `s22-display`, and
  creates the workspace dir. A missing workspace dir caused `spawn /bin/bash ENOENT`.

## Device-verified
Session `s22-smoke-4`: "Use shell_run to run `uname -m && s22-modem-up status`, then
`s22-say` a sentence with the architecture and modem state." The rig model
made two verified `shell_run` calls, and the phone spoke and replied:
"CPU architecture is aarch64 and the modem state is ONLINE." WebUI `GET /`
returns 200 (70 KB).

Seen on the way: the model once named the argument `command` instead of `cmd`
(preflight rejected it). That is a model/framework issue, not a phone issue.

## Local model on the phone (fallback)
The resident model is Qwen3.5-4B Q4_K_M on :8089 (CPU, cores 4–7): **4.9 tok/s**
generation and 12 tok/s prompt. That is too slow for agent turns (~4k-token
prompts). The Qwen3.5-2B Q4_0 already on the phone measured **6.9 tok/s**
generation and **52 tok/s** prompt (485-token prompt). A sub-1B model would be
the realistic local fallback, for intent/routing only. Halogen should stay the brain.

## Security note
OpenUnum's shell tool runs as root, and `s22-host` reaches the host root. The
WebUI is localhost-only. Expose it on the tailnet only together with the
WebUI password.
