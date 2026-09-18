# Phase 0 Evidence Collection — 2026-09-18

## Host
- corpunumRig, Ubuntu 24.04.4 LTS, kernel 7.0.0-30-generic
- AMD Ryzen AI MAX+ 395, 124GiB RAM, 475G free on /

## Phone connection
- USB: `Bus 005 Device 005: ID 04e8:6864 Samsung Electronics Co., Ltd GT-I9070 (network tethering, USB debugging enabled)`
  (generic Samsung ADB descriptor string, not the actual model name)
- Needed: `android-tools-adb`/`android-tools-fastboot` installed via apt (not present on fresh host)
- Needed: udev rule `/etc/udev/rules.d/51-android.rules`:
  `SUBSYSTEM=="usb", ATTR{idVendor}=="04e8", MODE="0666", GROUP="plugdev"`
- User `corpunum` already in `plugdev` group
- ADB authorization required manual "Allow USB debugging" tap on phone (user confirmed)
- Device serial: `&lt;redacted&gt;`

## Device properties (VERIFIED, not assumed)
```
ro.product.model              = SM-S901B
ro.product.device             = r0s
ro.bootloader                 = S901BXXSIFYI3
ro.boot.flash.locked          = 0
ro.boot.verifiedbootstate     = orange
ro.boot.vbmeta.device_state   = unlocked
ro.boot.warranty_bit          = 1        <-- DISCREPANCY: prior notes said 0
ro.boot.knox_warranty_bit     = (empty)
sys.oem_unlock_allowed        = 1
ro.build.version.release      = 15
ro.build.display.id           = AP3A.240905.015.A2.S901BXXSIFYI3
ro.build.fingerprint          = samsung/r0sxeea/r0s:15/AP3A.240905.015.A2/S901BXXSIFYI3:user/release-keys
uname -a                      = Linux localhost 5.10.223-android12-9-30958166-abS901BXXSIFYI3 #1 SMP PREEMPT Wed Sep 3 09:06:30 KST 2025 aarch64 Toybox
```

**Confirmed**: bootloader IS genuinely unlocked (flash.locked=0, verifiedbootstate=orange,
vbmeta.device_state=unlocked). This matches the prior claim and is now independently re-verified.

**Correction to prior record**: warranty_bit is currently `1`, not `0` as previously logged.
Treat any prior warranty-bit claim as stale; this value can change from flash/recovery activity
and should be re-checked at each session.

## Root / pstore access attempt — FAILED, evidence path closed for now
- Build is `user` (not userdebug/eng): `ro.secure=1`, `ro.debuggable=0`
- `su` binary: not present (`inaccessible or not found`)
- `adb root`: refused — "adbd cannot run as root in production builds"
- `/sys/fs/pstore` — `stat` succeeds (dir exists, mode `dr-xr-x---`, owner `system:log`, size 0
  entries shown by stat only, not contents)
- `ls -la /sys/fs/pstore/` → `Permission denied` (even though shell's gid 2000 has group `log`
  membership — likely blocked by SELinux, not raw DAC)
- Direct `cat` of common ramoops filenames (`console-ramoops-0`, `dmesg-ramoops-0`,
  `pmsg-ramoops-0`, etc.) → `No such file or directory` (ambiguous: could be real ENOENT from
  wrong filename guesses, or SELinux masking EACCES as ENOENT — cannot distinguish without root)
- `/proc/last_kmsg` → `Permission denied`
- `dmesg` / `dmesg -w` → `Permission denied` (klogctl)
- `logcat -b kernel -d` → empty output, no error (buffer likely empty or also restricted)

**Conclusion**: We CANNOT currently read pstore/last_kmsg/dmesg evidence from the previous
failed native-recovery boot over stock, non-rooted ADB. This requires either:
1. A rooted/custom recovery shell (chicken-and-egg — that's what we're trying to build), or
2. Root via Magisk/exploit on current stock Android (not attempted — out of scope unless
   explicitly requested, since it's a detour from the native-Linux goal and touches system
   partitions), or
3. Reading pstore during a future controlled Lineage-recovery boot test (Phase 7/8), where the
   recovery environment itself typically runs as root with no SELinux enforcement.

No crash evidence recovered from the previous failed boot at this time. This does not block
subsequent phases — kexec/module analysis in Phase 6 will proceed via source/binary comparison
instead of runtime crash logs.

STATUS: pstore evidence = NOT RECOVERED (blocked by stock non-root build), not investigated further.
