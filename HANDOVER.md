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
  (`--rollback` restores the old one).
- **Branches:** work lands on `main` only. The pre-rewrite (10-22) local branches were removed on 2026-11-11.
  Every tip was already on main.

## Health (2026-11-11 evening)
| Area | State |
|---|---|
| OpenUnum | 2.10.0; the device base is 37 commits behind rig main. One health check fails: `systemd_units_valid`. The chroot has no systemd and keepalive supervises instead, so this is a false alarm. |
| Voice (text turn, Luna) | Correct reply. The first filler is heard at 1.3 s, the reply audio starts at 7.9 s, and the turn ends at 17.5 s. The phone stays muted (level 0). |
| Camera via s22d | The capture works, but the develop step is broken: s22d runs the Rust `s22-develop` binary through `python3`, so the agent's `camera_capture` returns a dark, undeveloped quick PNG at 510x383. The shell's camera button (touchd: meter, burst, develop) is fine. |
| UI | sway, unum-fastshell and squeekboard are running. |
| Local model | llama-server with Gemma-4 E2B and an MTP draft, `-np 2`, cache reuse. |
| Battery | Charge cap at 85 % is active. |
| Kernel | Still the 09-24 build. The approved recovery-kernel flash (close_range fix) has not been done. |
| s22d | 0.3.0 (11-04 build); it includes torch. |

## Open work (UnumHub)
- `U-fd91ceec`: S22 deploy and kernel flash. Prompt-cache verification is still open; the flash is still to do.
- `U-920ae886`: umbrella item for drivers, local inference speed and TTS (`docs/GAP-PLAN-20261104.md`).
- `U-63d712a5` / `874d6f85`: "phone offline" items. The phone is reachable again, so the update plan in the owner's
  device-update brief can run.
