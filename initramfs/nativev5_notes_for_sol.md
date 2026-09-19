# Prompt for ChatGPT (Sol) — second opinion needed

## Context
I'm porting a Samsung Galaxy S22 (SM-S901B, Exynos 2200/s5e9925, codename r0s) recovery
partition to run a custom minimal Linux init instead of Android's AOSP init, as a step
toward eventually replacing Android entirely with native Linux. Bootloader is unlocked
(verified: flash.locked=0, verifiedbootstate=orange, vbmeta.device_state=unlocked).

## What's proven working (via a real successful boot tonight)
Flashed the CURRENT OFFICIAL LineageOS recovery.img (build 20260915, kernel 5.10.260) to
the RECOVERY partition and it booted successfully with full USB (dwc3 gadget), display
(DSI panel, 1080x2340@120Hz), and touch, plus a root ADB shell. Confirmed via dmesg,
lsmod, /sys/class/udc, /sys/class/drm working correctly.

## What I'm building
A custom `/init` (busybox ash script) to replace AOSP's `/init`, using the SAME proven
kernel + DTB + recovery_dtbo, with the goal of loading real kernel modules
(regulators, IOMMU, USB PHY phy-exynos-usbdrd-super.ko, DWC3 dwc3-exynos-usb.ko, USB
gadget function drivers) via busybox `insmod`, then configuring a USB ACM gadget via
configfs, and dropping to an interactive shell over USB serial. No Android userspace at
all (no zygote/ART/SurfaceFlinger/init.rc).

Toolchain: busybox 1.36.1 cross-compiled statically (glibc, not musl) for aarch64 via
`aarch64-linux-gnu-gcc` (Ubuntu 13.3.0), confirmed static via `file` (statically linked
ELF). Boot image built with AOSP `mkbootimg.py` (header_version 2, matching Lineage's
own recovery header exactly: pagesize 0x800, kernel_offset 0x10008000, ramdisk_offset
0x11000000, tags_offset 0x10000100, dtb_offset 0x11f00000, cmdline " bootconfig"), then
an AVB hash footer added via `avbtool.py add_hash_footer --algorithm NONE
--rollback_index 0 --partition_size 100663296 --partition_name recovery` (matching
LineageOS's own BoardConfig: `BOARD_AVB_RECOVERY_ALGORITHM := NONE`).

## The bug
EVERY custom-init attempt crash-loops identically: boot shows the Samsung bootloader's
own static splash ("Samsung Galaxy / Secured by Knox / Powered by Android"), then resets
and repeats forever. No visible kernel console output ever appears (screen never
transitions away from the bootloader's own static splash to anything Linux-drawn).

### Isolation testing done tonight, in order:
1. **V1 attempt** (not detailed here, historical) - static Alpine/BusyBox init, no
   module loading at all → crashed same way (splash, ~60s, reset loop) - this was from
   a PRIOR session on different hardware/tooling.
2. **V2**: custom init loading ALL 324 real kernel modules (in the exact
   `modules.load` order LineageOS itself uses) via sequential `insmod`, then configfs
   USB gadget setup → crash loop (after first fixing a missing-AVB-footer issue that
   caused an earlier, DIFFERENT failure mode: `SVB Fail! recovery: No footer detected`,
   shown clearly on-screen before the kernel was even reached - that was fixed by adding
   the avbtool footer as described above).
3. **V3**: same approach but trimmed to only 12 modules (just the USB-critical chain:
   4 regulators, 2 IOMMU, phy-exynos-usbdrd-super, dwc3-exynos-usb, 4 USB
   gadget/notify/typec modules) → IDENTICAL crash loop, same visual signature.
4. **Control test**: took Lineage's OWN unmodified kernel+ramdisk+dtb+recovery_dtbo,
   repacked through OUR OWN mkbootimg+avbtool pipeline (not Lineage's official prebuilt
   image) with NO content changes at all → **booted successfully**. This proves our
   image-packaging process itself (mkbootimg args, AVB footer application) is correct.
5. **V4**: took the EXACT working ramdisk from the successful Lineage boot (full
   original tree: real sepolicy, lib64, etc. all still present, completely untouched),
   and changed ONLY the `/init` symlink to point to a busybox-based script instead of
   the real AOSP init binary → **crash loop again**, same signature.

### Working theory
Since V4 proves the packaging/kernel/DTB/ramdisk-structure are all fine, and the ONLY
variable left is "AOSP's compiled init binary vs our busybox ash script as PID 1", my
leading theory: **our init script exits at some point, which panics the kernel**
("Attempted to kill init!" is the standard Linux behavior when PID 1 dies), and this
device's boot-time watchdog/panic-reboot policy silently resets on any panic, producing
what LOOKS like a crash loop but is really just repeated panics.

The suspected concrete cause: the script's last line was `exec $BB sh` (where
`$BB=/native_busybox`, invoked as `busybox sh` per busybox's documented multi-call
argument form). If that `exec` fails for any reason without the script noticing (ash
does not treat a failed `exec` as fatal by default - it just logs an error to stderr and
continues past that line), the script reaches EOF and the shell process exits normally,
killing PID 1.

## Questions for you
1. Does this theory hold up? Is there a more likely explanation for "screen never
   shows ANY Linux-originated console output, ever, across 3 different content
   variations, all ending in the same reset loop, on a device where the exact same
   kernel/DTB/ramdisk-structure otherwise proven to work"?
2. Specifically about the shebang/PID1-as-script mechanism: is there a known pitfall in
   using a symlinked shebang script (`/init -> native_init`, first line
   `#!/native_busybox sh`) as the kernel's initramfs `/init` on Android-derived kernels,
   that wouldn't show up when the SAME busybox binary/script logic is tested via
   `qemu-aarch64-static` on a x86 host (which I did - basic `sh -c echo` and multi-call
   applet dispatch worked fine under emulation)?
3. Is there a more direct way to get actual kernel panic/oops text off this device
   without physical UART access? I have root ADB access to the WORKING (AOSP-init)
   recovery build, and confirmed `/sys/fs/pstore/` exists but is empty after each crash
   (checked both after the V2 and after the V4 incident). CONFIG_PSTORE_RAM is enabled
   in this kernel's defconfig. Is there a reason ramoops wouldn't survive a
   panic-driven watchdog reset specifically (as opposed to a clean reboot), or should I
   be looking somewhere else / configuring something differently to capture it?
4. Given all this, what would you actually try next, in priority order?

## Files available if you want to inspect them directly (ask and I'll paste content)
- The exact V4 `/init` script (~70 lines of busybox ash)
- `modules.load` order (324 real Samsung/Exynos kernel module filenames)
- Full `dmesg.txt` from the successful AOSP-init boot (2989 lines)
- mkbootimg/avbtool exact command lines used (above, verbatim)
