# Bluetooth discovery — 2026-10-06 (lead session)

## The running kernel has the raw-HCI socket fix
The running kernel's GNU build ID is `b2dda820b18d410d9bf12f1bd2584567d545991d`
(`/sys/kernel/notes`; 5.10.260 built Sep 24 09:09). That is the HCI-only RECOVERY
candidate carrying `tools/hardware/bt-hci-socket-restore.patch`. In this
session, raw HCI sockets were created, bound to hci0, filtered,
used to send commands and closed, many times over three runs, with no
warning or panic.

## Runs (owner-approved BT trials; each run is bounded to the probe's 20 s window)
1. Unpinned reviewed plain-H4 bridge probe (`bt-hci-plain-h4-20260927`):
   `baud_probe_result=-110`. After the 115200→3M baud switch the first byte was
   garbled (`non_event_byte=fc`), so hci0 was never created. The probe powered
   the chip off cleanly.
2. Pinned to the X2 core (cpu7) with SCHED_FIFO 50 (raw syscall; musl's
   wrapper returns ENOSYS): the baud switch succeeded and hci0 was up 3.4 s after start.
   Kernel auto power-on (`HCIDEVUP` → EALREADY). Controller: HCI/LMP 12
   (BT 5.3), manufacturer 29 (Qualcomm), subver 0x687c, the Linux-generated
   private address `22:22:FD:…`. Legacy LE scan was refused with status 0x0C
   (the kernel already uses extended scanning). The classic inquiry found 2 devices.
3. With the LE extended scan added (`0x2041/0x2042`, passive, receive only)
   plus the inquiry: **6 LE devices + 2 BR/EDR devices** (RSSI −78…−95 dBm;
   one device seen on both). `bridge_result=0 commands=50 events=170
   queued=0`, `baud_probe_result=0`, `probe_rc=0`.
After each run: hci0 was gone, the chip was powered off and Wi-Fi was still up. The
kernel log had no Bluetooth errors. Addresses in the scan output are masked.

## Tool
`s22-bt-scan [--le-seconds N] [--inquiry-units N]`
(`tools/hardware/bt/s22-bt-scan.sh` + `s22-bt-scan.py`). It refuses to run if hci0
is already present. It does no pairing, connection or advertising.

## Next
A persistent controller (`bluetoothd`, pairing, audio) needs the bridge to run
without the hard-coded 20 s limit (`s22_bridge_run(fd, 20000, …)`, max 60 s).
That is a probe rebuild using the private patch/NVM headers on the rig, plus a
supervised long-running mode.

## Persistent controller (same night)
- New `tools/hardware/bt/bt-qca6490-hci-hold.c` does the same power/patch/NVM/3M/
  runtime-reset sequence by including the reviewed sources, which are left
  unchanged and still hash-pinned. Its own copy of the bridge loop runs until
  SIGINT/SIGTERM instead of 20 s. It is a static aarch64 build with the private
  headers. The binary is only on the phone (`/srv/s22/bt-hold/`, 0700) and is not committed.
- `s22-bt up|down|status` (manual, no boot hook, pinned X2/SCHED_FIFO like the scan).
- Device: hci0 was up after 4 s and **stayed registered for more than 70 s**. Two scans 40 s
  apart each found 6 LE + 2 BR/EDR devices. `s22-bt down` gave `bridge_result=0
  commands=141 events=311 queued=0`, `pty_cleanup_ioctl_result=0`, `baud_probe_result=0`;
  hci0 was gone, the chip was off and Wi-Fi was still up.
- `s22-bt-scan.py` now only runs HCIDEVDOWN if it brought the controller up itself.
- No pairing (that needs the owner). BlueZ (`bluetoothd`) is not installed in the
  chroot yet. It is needed for pairing and audio later.
