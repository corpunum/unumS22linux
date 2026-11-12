# HANDOVER: unumS22linux

The Samsung Galaxy S22 (Exynos 2200, vendor 5.10 kernel) running native Linux as Unum's voice, camera and UI
device. This file is what the next agent needs to continue. Keep it short and current; history goes to
`docs/HISTORY.md` and `STATUS.md`.

Owner session: `s-s22linux` (Claude Code, long-lived). Orchestrator: `corpunum-82`.
**This repo is PUBLIC:** no host names, IPs, phone numbers or other PII. Run gitleaks before every push.
Private access details (tailnet name, SSH options, allowlists) live in an untracked `local.env`
(see `s22-local.env.example`) and in the owner's private notes, never here.

## Layout on the phone
- The host runs from the Android ramdisk; the Arch userland is a chroot at `/mnt/omarchy-trial`.
- OpenUnum runs **inside the chroot** (`/opt/openunum`, branch `device/s22`) on port 18880. It cannot see `/srv/s22`.
- Host-side device tools live in `/srv/s22/hardware/bin` (from `tools/hardware`, `tools/touchui`, `tools/s22d`).
- `s22d` (Rust, `tools/s22d`) is the device API on 127.0.0.1:8766 plus `/run/s22d.sock`. OpenUnum's `s22-device`
  plugin (`device_*` tools) talks to it.
- `s22-keepalive` restarts OpenUnum, unumsearch, phoned, llama and others. It also keeps the phone muted
  (`enforce_mute`).
- Desktop: sway + pixman (`/etc/s22-desktop`), with `unum-fastshell` as the touch shell and squeekboard.

## How to work on it
- **Recovery:** use `s22-reboot recovery` only. The phone is back in about 40 s.
- **Traps:**
  - pip `ensurepip` hangs in the kernel; only a reboot clears it.
  - Never read debugfs gpio: it reboots the phone.
  - `pgrep -f` or `grep` on a name that also appears in your own ssh command line will kill your session. Use exact PIDs.
  - `/sys` is read-only inside the chroot.
  - The kernel loads firmware from PID 1's root, not from `/lib/firmware`.
- **Load average sits around 15 even when idle:** 14 vendor kernel threads (TrustZone, UFS, CHUB) stay in D-state.
  Look at CPU idle, not the load average.
- **OpenUnum updates:** device git. Push rig main to the phone repo as `base`, rebuild `device/s22` on top of it,
  and keep a rollback branch. Restart by killing the exact PID on :18880; keepalive brings it back in about 5 s.
- **s22d build:** `cargo build --release --target aarch64-unknown-linux-musl` (rustup cargo; the distro cargo is too old).
  Deploy: copy the binary to the phone, then `sh /srv/s22/hardware/install-s22d.sh --binary <bin>`
  (`--rollback` restores the old one). Since 11-11 the script swaps the binary in before it stops the old process;
  before that, keepalive could restart the OLD binary in the gap. Check with `readlink /proc/<pid>/exe`: it must not say "(deleted)".
- **Restarting a keepalive-managed service:** kill its exact PID ONCE, then wait for health. Rapid repeated kills race keepalive and hit its restart limit; :8090 was down for 4 min on 11-11.
- **Long tests over ssh:** put a file in `/run/s22-autosleep/inhibit.d/` whose content is a TTL in seconds. Otherwise the phone can suspend and drop the session.
- **Branches:** work lands on `main` only. The pre-rewrite (10-22) local branches were removed on 2026-11-11.
  Every tip was already on main.

## Health (2026-11-11 evening)
| Area | State |
|---|---|
| OpenUnum | 2.10.0, updated 11-11: device/s22 = rig main 78af91c plus the Lightpanda launcher commit. Rollback: branch `device/s22-pre-20261111b` and `openunum.json.bak-20261111-update`. One health check fails: `systemd_units_valid`. The chroot has no systemd and keepalive supervises instead, so it is a false alarm (hub U-a6a2ab67). |
| Voice (text turn, Luna) | Correct reply. The first filler is heard at 1.3 s, the reply audio starts at 7.9 s, and the turn ends at 17.5 s. The phone stays muted (level 0). |
| Camera via s22d | Fixed on 11-11. `camera_capture` meters exposure, takes a 4-frame burst and runs `s22-develop --stack` (the binary itself). It returns a 2040x1532 JPEG in about 7.5 s and copies it to the gallery. Same path as the shell's camera button. |
| UI | sway, unum-fastshell and squeekboard are running. **Owner pairing (11-12):** the shell's Local/Luna toggle sends the device credential from `/srv/s22/state/owner-session` (0600, host-only; the chroot cannot see it) as `X-OpenUnum-Owner-Session`. Only native-root peers of ctl.sock may switch models or pair (SO_PEERCRED plus `/proc/<pid>/root`), so the chroot's agents cannot borrow owner proof. Pair or re-pair, owner only (corpunum-82 runs it; the code never gets printed): `C=$(chroot /mnt/omarchy-trial sh -c "cd /opt/openunum && HOME=/root /opt/node-v22.23.3-linux-arm64/bin/node scripts/owner-pair-code.mjs --label s22-touchd --json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["code"])') && python3 /srv/s22/hardware/bin/s22-touchd pair "$C"`. Unpaired or expired (30 days): the toggle answers `repair_needed`, with no retry. s22-keepalive's model pin (the owner's own default, Luna; corpunum-82 11-12) asks touchd `model_pin` over ctl.sock: native root only, switches only on a real drift, at most once per 10 min, logs `model_pin` events. Keepalive never reads the credential. Keepalive and s22-guardian supervise each other (11-12): guardian respawns keepalive when its flock is free (backoff 30 s doubling to 600 s, `keepalive_respawn` in guardian.jsonl). To stop keepalive on purpose, create `/run/s22-hold/keepalive` first. |
| Local model | llama-server running Gemma-4 E2B with an MTP draft, `-np 2`, cache reuse; lean append-only window. Measured 11-11 with the screen off, after the governor fix: prefill 26 tok/s (it was 9), generation ~11 tok/s (it was 4.6). Six sustained turns peak at 54 °C. Turns 2-5 of one chat reach their first token in 3.4-3.9 s. `threads-batch 3` is rejected: 52 tok/s, but 77 °C sustained and a guardian hold. The proxy reads the backend argv only at start, so an A/B of backend flags needs a proxy restart. Put an autosleep inhibit file in place during long ssh tests. |
| Battery | Charge cap at 85 % is active. |
| Kernel | 11-12: the close_range fix is flashed (build-id f95682b4, RECOVERY 19aa0293…). Subprocess capture no longer hangs. Rollback and the build recipe are in FLASH_LOG.md. To rebuild, use `tools/hardware/build-recovery-kernel.sh` with `KMAKE_FLAGS="ARCH=arm64 LLVM=1"`. |
| s22d | 0.3.0, the 11-11 build (camera ISP fix); it includes torch. |

## Open work (UnumHub)
- `U-fd91ceec`: done 11-12 (cache verified, power fix, kernel flashed).
- `U-920ae886`: umbrella item for drivers, local inference speed and TTS (`docs/GAP-PLAN-20261104.md`).
- `U-63d712a5` / `874d6f85`: "phone offline" items. The phone is reachable again, so the update plan in the owner's
  device-update brief can run.
