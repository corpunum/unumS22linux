# Modem and GNSS

Part of the [porting guide](../PORTING.md). The S22's Exynos (Shannon) modem works for SIM, SMS,
mobile data and 5G NSA through our own daemon; calls don't work yet. The GNSS core runs Samsung's
own engine in a sandbox and tracks satellites, with no position fix yet
([README](../../README.md#what-works-and-what-doesnt-yet)).

## Modem

**Read first, it matters most here:** the modem keeps its calibration and identity (IMEI) in EFS.
The modem *writes* NV data to the phone during boot. On Android that goes to EFS. Here it goes to a
**private copy** of EFS on userdata; the real EFS is never opened
([evidence/modem-20261006](../../evidence/modem-20261006/README.md)).

### Booting the modem with the stock `cbd`

Samsung's `cbd` loads the modem firmware from the `radio` partition and boots the CP. We run the
stock binary in a small Bionic root (`/srv/s22/android-rt`) with the stock arguments from its `.rc`
file, pointing `-n` at the private EFS copy ([evidence/modem-20261006](../../evidence/modem-20261006/README.md)).
It failed with "Permission denied" until we read why:

- `cbd` drops root by itself to the radio user (seen in the disassembly);
- on Android, **ueventd** has already made `/dev/umts*` group `radio` and init made
  `/sys/power/wake_lock` writable for radio. On our phone they were root-only;
- `cbd` keeps group 0 after dropping root, so group permissions alone were not enough.

The fix is session-only device ownership set by
[`s22-modem-up.sh`](../../tools/hardware/modem/s22-modem-up.sh) before starting `cbd`, the radio
partition read-only. Its "NV validation TIMEOUT" message is harmless: it waits 5 s for an Android
property that never comes, then continues.

### Our own client instead of Samsung's RIL

The CP kept restarting at `PHONE_START`. Reading the kernel driver showed it sends `INIT_END` only
when **both** `umts_ipc0` (control) and `umts_rfs0` (file service) are open; Android's RIL opens
them. Our Python [`s22-modem.py`](../../tools/hardware/modem/s22-modem.py) opens both and speaks
Samsung's SIPC frames; it read the firmware version, a masked IMEI, SIM status and signal, and
answers the CP's NV writes into the private copy with the right checksum
([evidence/modem-20261006](../../evidence/modem-20261006/README.md)).

That grew into **`s22-phoned`**, a stdlib-only daemon with an HTTP API for SIM PIN, SMS, calls and
mobile data ([tools/hardware/phoned/README.md](../../tools/hardware/phoned/README.md)). Message
layouts came from the open-source libsamsung-ipc (Replicant, and a fork for Shannon LTE modems) and
from live captures ([RECON.md](../../RECON.md)). Its safety gates are worth copying:

- sending SMS or dialling is refused unless a `tx_enabled` file and an exact-number allowlist exist;
- emergency numbers and short numbers are always refused; rate limits per hour;
- a SIM PIN is tried at most once per run, never for PUK, and never logged;
- no automatic network-mode change; experimental frames only from a short allowlist. (One
  exception, on by default since it moved the radio to LTE: phoned resends the stock initial-attach
  APN frame at every modem start; `--no-lte-attach` turns it off, [HISTORY.md](../HISTORY.md).)

Results: SIM ready and SMS ready ([HISTORY.md](../HISTORY.md)); mobile data on LTE and 5G NSA,
97 Mbit/s down measured ([evidence/modem-5g-20261021](../../evidence/modem-5g-20261021/README.md));
the phone stays reachable on mobile data alone and wakes from sleep for incoming packets
([evidence/network-independence-20261022](../../evidence/network-independence-20261022/README.md)).
Call audio is not done.

### For your phone

- Find the modem boot daemon in `/vendor/bin` and its `.rc` service line (arguments, user, groups),
  and the `ueventd` lines for its device nodes.
- Find which device nodes the RIL opens; the kernel driver's source tells you what the CP waits for.
- Check whether an open IPC library already speaks your modem's protocol (libsamsung-ipc for older
  Samsung modems; Qualcomm modems use different protocols and tools).
- **Point every NV/EFS write at a copy.** Back up EFS read-only first, and never write it back.

## GNSS (GPS)

The read-only investigation is in [GNSS.md](../../tools/hardware/gnss/GNSS.md) with its evidence in
[evidence/gnss-20261022](../../evidence/gnss-20261022/README.md). What it found:

- GNSS runs on the Exynos 2200's own "Kepler" core. The kernel side (`/dev/gnss_ipc`) is already
  loaded at boot; the core was simply never powered.
- The stock engine is `/vendor/bin/hw/gpsd`. Its core firmware is **embedded in the binary**, and
  it needs a client (the HAL) to start navigating.
- By disassembly, gpsd has **two paths that reach outside GNSS**: an ioctl that resets the whole
  phone through the watchdog, and an ioctl on the modem that can swallow a SIM-changed event.

How it runs safely ([GNSS.md](../../tools/hardware/gnss/GNSS.md#0-status-2026-10-22-night-kepler-runs-satellites-tracked-no-fix-yet-indoors)):

- a **seccomp filter** makes those two ioctls, reboot, kexec and module loading fail, with a
  self-test that proves it on the phone;
- a minimal root whose `/dev` contains only `gnss_ipc` and the basic nodes, **no modem nodes**, in a
  private mount namespace with `/system` and `/sys` read-only;
- a tiny `LD_PRELOAD` property shim (gpsd waits forever for an Android boot property) and stubs for
  two missing libraries;
- our own client loads Samsung's HAL in-process (no binder) and redacts coordinates by default;
- the config turns off network assistance (SUPL) and downloads.

A 21-minute syscall audit showed 0 attempts at the blocked paths, and the modem, SIM and Wi-Fi were
unaffected ([evidence/gnss-20261022](../../evidence/gnss-20261022/README.md)). Indoors it tracked
two satellites, not enough for a fix.

**Gotcha:** start gpsd directly, not as `linker64 gpsd`. It loads its firmware "from the end of its
own executable", so through the linker it found no firmware and the core cycled into a fault
([GNSS.md](../../tools/hardware/gnss/GNSS.md)).

**For your phone:** the general pattern is the useful part. When a vendor daemon is unavoidable,
read it for side effects first, then run it with only the device nodes it needs and a seccomp filter
that blocks every dangerous call you found.
