# GNSS investigation evidence — 2026-10-22

This is read-only evidence for `tools/hardware/gnss/GNSS.md`.
Nothing was started, loaded, written or ioctl'd on the phone.
No IPC was sent to the modem.

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
