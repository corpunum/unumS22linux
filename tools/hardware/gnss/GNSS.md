# GNSS on the S22 under native Linux — findings and plan

Date: 2026-10-22.

## 0. Status (2026-10-22, night): Kepler runs, satellites tracked, no fix yet (indoors)

**Running:** the stock engine and our client run under Linux.
- **Engine:** `gpsd` loads its embedded Kepler patch and brings the core to OPERATIONAL.
- **Client:** `gnss-probe` loads Samsung's HAL in-process, connects to gpsd (abstract unix socket `@GNSSND`) and starts a standalone session.

**Results:**
- Engine name `S.LSI,K401,SPOTNAV_4.6.2_7_E23`, capabilities 0x3c7, hw_id 0x18200251.
- In a 5-minute run the phone sat indoors. First SV after 10.6 s. Then **GPS 15 (C/N0 33–36 dB-Hz, el 89°) and GLONASS 21 (37–43 dB-Hz)**, both with ephemeris.
- Only 2 SVs, so **no position fix**. A fix needs at least 4, which needs the phone at a window or outside.
- Modem, SIM, SMS, Wi-Fi and Tailscale were unaffected (checked before, during and after).

**How it is made safe:**

| Piece | What it does |
|---|---|
| `src/gnss-guard.c` | A seccomp filter for everything in the GNSS root. `ioctl 0x4b10` (SET_WATCHDOG_RESET: whole-phone reset), `ioctl 0x6f27` (CP status, clears the SIM-changed flag), reboot, kexec and module loading fail with EPERM. `--selftest` proves it on the phone. |
| `/srv/s22/gnss-rt` | The minimal root. `/dev` holds only null, zero, random, urandom and gnss_ipc: **no umts_*, no modem nodes**. `/system` is android-rt read-only, `/sys` is read-only, and it runs in a private mount namespace. |
| `src/gnss-hold-reset.c` | `GNSS_IOCTL_RESET`: Kepler back to HOLD_RESET after gpsd stops (closing the fd does not do it). |
| `src/propshim.c` + `linux.prop` | LD_PRELOAD property getter. Stock gpsd waits forever for `service.bootanim.exit=1`, and there is no property service here. |
| `src/stub-libandroid_net.c`, `src/stub-libvndksupport.c` | Stubs for the two libraries android-rt lacks. |
| `src/gnss-probe.cpp` | The HIDL client (IGnss@2.1 passthrough). It prints SV/C/N0, status and NMEA. **Coordinates are redacted** unless `--show-location`. |
| `gnss-rt.sh` | `check` / `start` / `probe` / `stop` / `status`. |

**Gotchas found on the way:**
- **Start gpsd directly** (its PT_INTERP is `/system/bin/linker64`), not as `linker64 gpsd`. gpsd loads the Kepler patch "from end of /vendor/bin/hw/gpsd", meaning its own executable. Under `linker64 gpsd` it found no patch ("Unable to locate patch for K401"), and Kepler cycled ONLINE→fault→HOLD_RESET.
- **The config:** `gps.linux.cfg` = stock `gps.cfg` with these changes:
  - `Aiding_AidingType=0` (no SUPL);
  - `Sif_UseFwXtraInterface=0` (no SGEE download);
  - `debug_enable=1` with logs in `/data/vendor/gps`;
  - `soft_dog_scan_dump=0`.

  The CP mailbox and DCXO settings are stock.
- **gpsd writes `rawbinlog.out`** (about 4 MB per 5 min of tracking) into `/srv/s22/gnss-rt/data/vendor/gps`. Delete it between runs.

**Run it:**

```sh
sh /srv/s22/gnss-rt/gnss-rt.sh start                 # gpsd under strace + guard; Kepler -> ONLINE
sh /srv/s22/gnss-rt/gnss-rt.sh probe --seconds 600 --nmea
sh /srv/s22/gnss-rt/gnss-rt.sh stop                  # exact PID, then Kepler HOLD_RESET
```

Build on the rig: `src/build.sh`. It uses NDK r27c plus AOSP `prebuilts/vndk/v30` generated HIDL headers, and links against the phone's own libc++ (std::__1) and libhidlbase.
Evidence: [`evidence/gnss-20261022/`](../../../evidence/gnss-20261022/README.md) (`run-*.txt`).

**Next:**
1. Put the phone by a window or outside and run `probe --seconds 900`.
2. If the fix is slow, inject a coarse position and time (the client already injects the time), or enable SUPL (privacy trade-off).
3. Wrap it as a service and an OpenUnum tool.

The sections below are the original read-only investigation, kept for reference.

## 1. Short answer

- **Chip:** GNSS runs on the Exynos 2200's own GNSS core, called **"Kepler"** (Cortex-M cores).
  Its RF chip is a Samsung **S5520** (`gnss_rfic_chip_id=55200001` in `gps.cfg`).
  The phone has **no Broadcom BCM477xx** and no `/dev/ttyBCM`.
  The **Shannon CP does not compute positions**. It only supplies assistance data (time, frequency and cell info).
- **Kernel side is ready.** `gnssif.ko` and `gnss_mbox.ko` load at boot today. They create
  `/dev/gnss_ipc` (misc 10:73). The link is shared memory (6 MB reserved at `0x8A1600000`) plus a mailbox at `0x14c70000`.
  Kepler is **OFFLINE**: it has never been powered, and all its IRQ counts are 0.
- **Userspace:** the stock **`/vendor/bin/hw/gpsd`** (4.3 MB, Bionic) is the engine. It carries the SiRF/CSR "SLL" core and SUPL,
  and the **Kepler firmware is embedded in the binary**. There is no separate `*.bin` in `/vendor/firmware`.
  gpsd loads the firmware itself through ioctls on `/dev/gnss_ipc`.
  The Android HAL (`vendor.samsung.hardware.gnss@2.1-impl.so`, passthrough HIDL) is the client that asks gpsd for fixes.
- **Without a client, gpsd will not navigate.** So "run gpsd" alone gives no fix. We also need a small client.
- **No fix was obtained.** It is feasible, but it needs two pieces of new code: an ioctl guard shim and a client.
  It also needs one supervised power-on of Kepler. None of that is zero-risk, so it was not run (see §5).

## 2. Findings with evidence

### 2.1 Hardware and kernel (verified)

| Item | Value | Evidence |
|---|---|---|
| Device node | `/dev/gnss_ipc`, `crw------- 10,73` | `phone-readonly.txt` |
| Modules | `gnssif` (69 KB), `gnss_mbox`; `CONFIG_EXYNOS_GNSS_IF=m` | lsmod; `kernel-gnssif.txt` |
| Driver version | `GNSSIF-20230328R1`, `shmem,name = "KEPLER"` | `stock-boot-gnss-kmsg.txt`, DT |
| Reserved memory | `gnss_rmem` 0x8A1600000, size 0x600000; code ≤ 0x380000, IPC at +0x380000 | DT, sysfs `reg`, boot log |
| Mailbox | `14c70000.gnss_mailbox`, GIC 81 | `/proc/interrupts` |
| IRQs | `kepler_active_handler`, `kepler_wdt_handler`, `kepler_sw_init_handler`: all count 0 | `/proc/interrupts` |
| State | `/sys/devices/platform/gnssif/gnss_status` = `OFFLINE` | sysfs |
| Linux GNSS class | `CONFIG_GNSS=y`, major 234, but `/sys/class/gnss` is empty (nothing registers there) | `/proc/devices`, ls |
| Supplies/GPIOs | The DT `gnssif` node has **no regulator and no GPIO**. Power goes through PMU registers (`pmu-gnss.c`) inside the driver. | DT dump |
| Panic on Kepler fault? | No. `kepler_wdt_isr` only logs and clears. The stock cmdline has `enable_detecting_gnss_assert=false`. | source, boot log |
| close() side effect | `misc_release` does **not** power Kepler off. A killed gpsd leaves Kepler running until `GNSS_IOCTL_RESET`. | `gnss_io_device.c:288` |

ioctl table (`'K'` = 0x4b, `drivers/soc/samsung/gnssif/gnss_prj.h`):
RESET 0x4b00, LOAD_FIRMWARE 0x4b01, REQ_FAULT_INFO 0x4b02, REQ_BCMD 0x4b03, READ_FIRMWARE 0x4b04,
CHANGE_SENSOR_GPIO 0x4b05, CHANGE_TCXO_MODE 0x4b06, SET_SENSOR_POWER 0x4b07, **SET_WATCHDOG_RESET 0x4b10**,
READ_SHMEM_SIZE 0x4b11, READ_RESET_COUNT 0x4b12, RELEASE_RESET 0x4b13, POWER_ON 0x4b14, LOAD_DATA 0xc0104b15.

**Danger:** `SET_WATCHDOG_RESET` calls `dbg_snapshot_expire_watchdog()`, which **reboots the whole phone**. The DSS module is loaded.

### 2.2 Stock userspace (verified from the FYI3 vendor image)

| Stock file | Role |
|---|---|
| `/vendor/etc/init/init.gps.rc` | Makes `/dev/gnss_ipc` 0660 system:system and creates `/data/vendor/gps{,/sgee}`. It starts `service gpsd /vendor/bin/hw/gps.sh`, user `gps`, groups `system inet net_raw wakelock`, cap `BLOCK_SUSPEND`. |
| `/vendor/bin/hw/gps.sh` | `exec /vendor/bin/hw/gpsd -c /vendor/etc/gnss/gps.cfg` (`gps.debug.cfg` when silent logging is on) |
| `/vendor/bin/hw/gpsd` | The engine. NEEDED: libc++, liblog, libm, **libandroid_net**, libssl, libcrypto, libz, libdl, libc. Interpreter `/system/bin/linker64`. |
| `/vendor/etc/gnss/gps.cfg` | Config. Key lines: `Aiding_AidingType=2` (SUPL), `Sif_UseFwXtraInterface=2`, `Chip_Configuration_RefClkControl=0x24` (DCXO shared with the CP), `Chip_Configuration_Io_Options=0x1` (CP mailbox), `#user_ports_tcp_name=Autotest` / `#user_ports_tcp_port=7555` (commented test port). |
| `/vendor/etc/gnss/ca.pem` | TLS roots for SUPL |
| `/vendor/bin/hw/vendor.samsung.hardware.gnss@2.1-service` | 11 KB; only `registerPassthroughServiceImplementation`. Starts as user system, groups system gps. |
| `/vendor/lib64/hw/vendor.samsung.hardware.gnss@2.1-impl.so` | The HAL. Exports `HIDL_FETCH_ISehGnss`, `…ISehGnssConfiguration`, `…ISehGnssExtra`, `…ISehAGnssRil`. Same SLL/LAL code base as gpsd; has `127.0.0.1` and `7557` strings (inferred: its link to gpsd). |
| `/vendor/lib64/libwrappergps.so` | Optional, dlopened by gpsd. Gets CP aiding (freq/time aiding, cell info, LPP) **through the stock RIL** (`libsecril-client`). It cannot work here, because s22-phoned owns the FMT channel. |
| `/vendor/lib64/hw/android.hardware.gnss@2.1-impl.so` | AOSP-name passthrough impl, also present |
| Absent | `/vendor/firmware/os.checked.bin` and `/vendor/bin/hw/alive_image.bin`. Both are only optional overrides named in gpsd. No `lhd`, no `glgps`, no `gps.xml`. |

### 2.3 gpsd paths that reach outside GNSS (verified by disassembly)

See `gpsd-risky-paths.txt`.
1. **Phone reboot path.** gpsd's "soft_dog" alarm handler can log `KEP soft_dog SCANDUMP` and then
   call ioctl `0x4b10` (SET_WATCHDOG_RESET). That reboots the phone. Two things gate it: a global flag and the config key
   `soft_dog_scan_dump`. The gate is not fully understood, so we must block this ioctl ourselves.
2. **CP status path.** gpsd opens `/dev/umts_boot0` read-only and calls `IOCTL_GET_CP_STATUS` (0x6f27).
   In cpif this ioctl also **clears `sim_state.changed`**, so it could swallow a SIM event meant for cbd/phoned.
   If the node is missing, gpsd only logs "modem state unavailable".

### 2.4 What is missing on the phone today (verified)

- android-rt (`/srv/s22/android-rt`) has the linker and most libraries. It **lacks `libandroid_net.so` and `libvndksupport.so`**.
- There is no `/dev/binder` or `/dev/hwbinder`, although the kernel supports binderfs. This does not matter for the passthrough route below.
- android-rt's `/dev` is a full copy of `/dev`. It **contains `umts_boot0`**, so gpsd must not run in android-rt as it is.
- There is no Android property service. gpsd reads some `persist.vendor.gnsslog.*` and similar properties; without them it falls back to defaults (inferred).

### 2.5 The modem path (inferred, not an alternative for a fix)

The CP has no position engine that we can reach.
The stock stack uses the CP only for these:
- frequency and precise-time aiding;
- cell info;
- control-plane LPP/SUPL messages.

All of them go through `libwrappergps` → `libsecril-client` → RIL (`oem_unsol_gps_*`).
On our side this would mean teaching s22-phoned to proxy the IPC GPS messages.
That only speeds up the time to first fix. It is not needed for a standalone fix.
A "network location" from the CP (MO-LR) needs operator support and Kepler measurements anyway.

**Coarse fallback with no hardware risk:** take the serving cell from s22-phoned (or a Wi-Fi scan) and look it up in
an open database such as BeaconDB. That gives ~100 m – 2 km accuracy, but it sends cell and Wi-Fi IDs to a third party.
It is the owner's call.

## 3. Chosen path

**Run stock gpsd with a guard, and drive it through the stock HAL in passthrough mode with our own small client.**

```
gnss-probe (our Bionic C++ client, NDK)
  └─ dlopen HAL in-process: IGnss::getService("default", /*getStub=*/true)
       └─ vendor.samsung.hardware.gnss@2.1-impl.so ──(127.0.0.1 / unix socket, inferred)──► gpsd
                                                                                          │ LD_PRELOAD ioctl-guard.so
                                                                                          ▼
                                                                     /dev/gnss_ipc → gnssif → Kepler + S5520
```

Why this path:
- It needs no binder and no hwservicemanager, because the passthrough HAL loads in-process.
- It uses a documented AOSP API (`IGnss@2.1`: `setCallback_2_1`, `start`, `injectTime`, `gnssSvStatusCb`, `gnssNmeaCb`).
- Samsung's firmware and gpsd stay untouched.

Plan B: uncomment `user_ports_tcp_*` and talk to gpsd's test port. That port probably speaks SiRF OSP binary.
The protocol is not verified, so Plan B takes more reverse engineering.

## 4. Step-by-step

Every step assumes: no reboot, no flashing, no EFS/NV access, Wi-Fi/Tailscale untouched, cbd/phoned untouched.
Stage everything under `/srv/s22/gnss-rt` (the root fs is small).

| # | Step | Commands (proposed, NOT run) | Risk | Rollback |
|---|---|---|---|---|
| 0 | Extract the stock files on the host | `sudo losetup -r -f --show rootfs/live-vendor-readonly.img`, then `sudo mount -t f2fs -o ro,norecovery /dev/loopN /mnt/v`. Copy `bin/hw/gpsd`, `etc/gnss/*`, `lib64/hw/*gnss*`, `lib64/*gnss*`, `lib64/vendor.samsung.hardware.gnss@2.*.so`. Unmount and run `losetup -d`. | None (host, read-only loop) | umount |
| 1 | Build `ioctl-guard.so` (NDK r27c at `rootfs/gpu-compat-20260921/toolchain/android-ndk-r27c`). Its `ioctl()` returns `-1/EPERM` and logs for 0x4b10 (WATCHDOG_RESET) and 0x6f27 (CP status); everything else goes to `dlsym(RTLD_NEXT,"ioctl")`. Also build a tiny static `gnss-hold-reset` (Arch gcc, aarch64) that does `open("/dev/gnss_ipc"); ioctl(fd, 0x4b00)`. | `aarch64-linux-android33-clang -shared -fPIC ioctl-guard.c -o ioctl-guard.so -ldl` | None (build only) | — |
| 2 | Add a stub `libandroid_net.so` if gpsd's imports need it: check `readelf --dyn-syms gpsd \| grep android_`; stubs return -1/ENOSYS. Or copy the real lib from the stock system image (`super`). | NDK build | None | — |
| 3 | Build the minimal root `/srv/s22/gnss-rt`: `system/` = bind-mount of `/srv/s22/android-rt/system` (read-only), `vendor/` = staged files, `data/vendor/gps/` (empty, 0771), `etc/` with resolv.conf. In `dev/`, bind-mount **only** `/dev/gnss_ipc`, `/dev/null`, `/dev/urandom` and `/dev/__properties__` if present. **No `umts_*` nodes.** `proc` and `sys` read-only. | `mount --bind -o ro …` ; `unshare -m chroot …` | None until a process opens gnss_ipc | `umount -R /srv/s22/gnss-rt` |
| 4 | Linker smoke test without hardware | `chroot /srv/s22/gnss-rt /system/bin/linker64 --list /vendor/bin/hw/gpsd` | None | — |
| 5 | **First Kepler power-on (supervised).** Copy `gps.cfg` to `gps.linux.cfg` and set `Aiding_AidingType=0`, `debug_enable=1`, `debug_dir=/data/vendor/gps`, `soft_dog_scan_dump=0`. Run gpsd for 60 s with no client, under strace if available. Watch `dmesg \| grep gif:`, `cat /sys/devices/platform/gnssif/gnss_status` and `/proc/interrupts \| grep kepler`. | `LD_PRELOAD=/vendor/lib64/ioctl-guard.so chroot … /system/bin/linker64 /vendor/bin/hw/gpsd -c /vendor/etc/gnss/gps.linux.cfg &` then later `kill <exact PID>` | **Low–medium.** Powers the GNSS block and loads signed firmware into its carve-out. Possible results: Kepler fault (GNSS only); DCXO is shared with the CP (stock runs both together, so low). The watchdog reboot is blocked by the shim. Do it while the owner is not mid-call or mid-transfer. | `kill <PID>`, then `gnss-hold-reset` to put Kepler back in reset. Verify `gnss_status` ≠ ONLINE. |
| 6 | Build `gnss-probe` (passthrough HIDL client). It needs the AOSP headers for `android.hardware.gnss@1.0/1.1/2.0/2.1` (hidl-gen output) and `libhidlbase`. Link against the device's own `android.hardware.gnss@*.so` and `libhidlbase.so`. It calls `getService("default", true)`, `setCallback_2_1`, `injectTime(now)`, `start()`, and prints NMEA, SV status and location to stdout. **The client must redact the location** (only print fix yes/no, satellite count and accuracy) unless the owner asks otherwise. | NDK + AOSP `hardware/interfaces/gnss` and `system/libhidl` headers | None (build) | — |
| 7 | Stage `libvndksupport.so` and the other HAL NEEDED libraries (libbase, libcutils, libutils, libhidlbase, vendor.samsung.hardware.gnss@2.0/2.1.so). Then `linker64 --list` the HAL impl. | as step 4 | None | — |
| 8 | **Fix attempt.** Phone outdoors or by a window. Start gpsd as in step 5, then `gnss-probe --seconds 600`. Expect SVs within about 1 min, then a cold-start fix in 1–10 min with no aiding. | as above | Same as step 5 | Same as step 5 |
| 9 | Optional, for a faster fix: set `Aiding_AidingType=2` (SUPL over Wi-Fi/data with ca.pem), or inject time and a coarse location from the client. Later: a phoned proxy for CP freq/time aiding. | — | SUPL sends coarse cell/location data to the SUPL server (privacy) | revert cfg |

## 5. Why nothing was run today

- gpsd alone does not navigate, and the client does not exist yet (steps 1, 6 and 7 are new code).
- Running stock gpsd unguarded can reach the **watchdog-reboot ioctl** and the **CP status ioctl**. The rules forbid both risks.
- Powering Kepler on is a first-time hardware event on this Linux, with a shared DCXO. It is not "clearly zero-risk", so it is only documented.
- Read-only ioctls (READ_SHMEM_SIZE, READ_RESET_COUNT) were also skipped; they add nothing.

## 6. Verified vs inferred

| Claim | Status |
|---|---|
| Integrated Exynos GNSS "Kepler", `/dev/gnss_ipc`, modules loaded, OFFLINE, no IRQs yet | Verified (phone) |
| No regulator or GPIO in the DT; power goes through PMU in the driver | Verified (DT/source) |
| Stock init: gps.sh → gpsd -c gps.cfg as user gps; HAL is a passthrough service | Verified (vendor image) |
| Kepler firmware is embedded in gpsd (no firmware file exists) | Verified that no file exists. Inferred embedded from the strings `lal_codeload_add_embedded`, "Reading default CFG from embedded" and the 4.3 MB size. |
| gpsd watchdog-reboot and umts_boot0 paths | Verified (disassembly plus kernel source) |
| HAL impl talks to gpsd over 127.0.0.1:7557 or a unix socket | Inferred (strings); confirm with strace in step 8 |
| gpsd tolerates no property service, no RIL and a missing umts_boot0 | Inferred; confirm in step 5 |
| `libandroid_net` symbols can be stubbed | Inferred; check in step 2 |
| The CP cannot give a standalone fix | Inferred (stock design and RIL message names) |

## 7. Next three steps

1. Build `ioctl-guard.so` and `gnss-hold-reset`. Then build `/srv/s22/gnss-rt` with no umts nodes, and run the linker `--list` checks (steps 1–4, zero risk).
2. Get the AOSP gnss HIDL headers and build `gnss-probe` (step 6). Stage the HAL libraries (step 7).
3. With the owner's OK, do the supervised first power-on (step 5), then the fix attempt by a window (step 8).
