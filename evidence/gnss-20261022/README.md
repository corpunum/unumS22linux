# GNSS investigation evidence — 2026-10-22

The files in the first table are read-only evidence for `tools/hardware/gnss/GNSS.md`. While they were captured, nothing was started, loaded, written or ioctl'd on the phone, and no IPC was sent to the modem.
The supervised power-on came later and is described at the end.

| File | What it holds | Source |
|---|---|---|
| `phone-readonly.txt` | `/dev/gnss_ipc`, lsmod, sysfs `gnss_status`, reserved memory, IRQ counts, binder state, which gpsd libraries the android-rt root lacks | ssh to the phone (ls/cat/grep only) |
| `stock-vendor-gnss-files.txt` | Size and sha256 prefix of every stock GNSS file, the firmware check, and the VINTF entries | FYI3 vendor image `rootfs/live-vendor-readonly.img`, loop-mounted on the host with `losetup -r` and `mount -o ro,norecovery`, then unmounted |
| `stock-gnss-configs.txt` | Full `init.gps.rc`, the HAL `.rc`, `gps.sh`, `gps.cfg`, and the debug-config diff | same image |
| `gpsd-analysis.txt` | gpsd NEEDED libraries, path strings, Kepler/codeload strings, ioctl numbers at call sites; HAL impl exports | `readelf`, `strings`, `llvm-objdump` on the host |
| `gpsd-risky-paths.txt` | The two gpsd paths that reach outside GNSS: an AP watchdog reboot and a CP status ioctl | disassembly |
| `kernel-gnssif.txt` | The gnssif ioctl table, the watchdog ioctl handler, the DT nodes, and the kernel config | `lineage/android_kernel_samsung_s5e9925` |
| `stock-boot-gnss-kmsg.txt` | Stock boot kernel lines for the gnssif/mbox probe | `evidence/native-linux-20260919/preflight/cache-recovery/` |

No IMEI, serial number, phone number or location appears in these files.
The phone was up 1 h with load average about 14 at capture time; the cause is unrelated to this work.

## Supervised power-on and satellite tracking (2026-10-22, about 01:30–01:56 EEST)

The owner gave permission, through the coordinator, to power Kepler on.
The phone sat indoors on its usual spot. Tools and the runner are in `tools/hardware/gnss/` (see GNSS.md §0).

| File | What it holds |
|---|---|
| `run-gpsd.txt` | guard self-test on the phone; gpsd codeload → OPERATIONAL; the first attempt's codeload failure (`linker64 gpsd` cannot find its embedded patch); kernel state lines |
| `run-strace-summary.txt` | gpsd syscall audit over 21 min: **0** attempts at ioctl 0x4b10/0x6f27, `umts_*` or reboot; the only device it opened was `/dev/gnss_ipc`; DNS went to 0.0.0.0:53 (no resolver in the root, nothing listens), so no data left the phone |
| `run-probe.txt` | two gnss-probe runs (5 min, 15 min), every 6th SV report: GPS 15 near zenith (el 90→83°, C/N0 22–36 dB-Hz) and GLONASS 21 (C/N0 36–43 dB-Hz), both with ephemeris; 0 fixes (1–2 SVs indoors) |

Health checks:

| Check | Result |
|---|---|
| Before power-on | modem ONLINE, SIM READY, home_3gpp/LTE, SMS ready, Wi-Fi UP, Tailscale up, hci0 present, `s22-reboot` present |
| During (after 5 min of tracking) | modem ONLINE, SIM READY, home_3gpp/LTE, SMS ready |
| After `gnss-rt.sh stop` | Kepler HOLD_RESET, no gpsd, no namespace mounts left behind; modem ONLINE / SIM READY / home_3gpp LTE / SMS ready / ipc_open; Wi-Fi UP; Tailscale up; hci0 present with the BT holder running |

No reboot: uptime ran continuously, 1:32 → 1:59. No calls and no SMS were made.

gpsd log files and `rawbinlog.out` stay on the phone (`/srv/s22/state/gnss`, `/srv/s22/gnss-rt/data/vendor/gps`). They are not committed, because they are raw engine logs.
