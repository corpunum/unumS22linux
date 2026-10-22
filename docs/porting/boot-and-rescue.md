# Boot and rescue

Part of the [porting guide](../PORTING.md). How the S22 boots our own Linux from its RECOVERY
partition, what failed first, and how we kept a way back at every step.

## The idea in one paragraph

Samsung phones have a separate RECOVERY partition (96 MiB on the S22) that the bootloader starts
on a key combination or a software "reboot to recovery". We put our own boot image there: the
**LineageOS kernel, DTB and recovery DTBO unchanged**, the AOSP first-stage init unchanged, plus a
small wrapper and our own PID 1. The normal BOOT partition and Android stay as they were, so the
phone can still be re-flashed from Download Mode if our image fails
([NATIVE_LINUX.md](../NATIVE_LINUX.md), [RECOVERY_COMPARISON.md](../RECOVERY_COMPARISON.md)).

Before you copy this: phones with **A/B slots** or **boot-as-recovery** (no separate RECOVERY)
don't have this spare partition, and need a different route
([PHONE_PORTABILITY.md](../PHONE_PORTABILITY.md#what-transfers-from-this-project)).

## Step 1: a control test with the unmodified LineageOS recovery

Flash the official LineageOS recovery for your exact model and confirm it boots. Then repack the
same kernel, ramdisk, DTB and DTBO **with your own tooling** and confirm that boots too. This
proves your packaging pipeline before you change anything ([EXPERIMENTS.md](../../EXPERIMENTS.md),
"Control test" and V11 rows).

What we had to match exactly (read from Lineage's `BoardConfigCommon.mk` and the real image,
[RECOVERY_COMPARISON.md](../RECOVERY_COMPARISON.md)):

- boot image header version 2 for recovery, page size, load addresses, OS version/patch level;
- LZ4 ramdisk compression;
- an **AVB hash footer** (`avbtool add_hash_footer --algorithm NONE`). Without it the bootloader
  stops with `SVB Fail! recovery: No footer detected` before the kernel starts
  ([EXPERIMENTS.md](../../EXPERIMENTS.md), "Confirmed bugs" 1);
- the exact partition size.

## Step 2: don't replace first-stage init (what failed)

Our first plan was to make `/init` our own program. Every variant crash-looped with no output: a
busybox script, a tiny static C binary, with 324, 12 or zero modules
([EXPERIMENTS.md](../../EXPERIMENTS.md), "Isolation results"). Independent reviews later found why
our logging could never have worked, which is worth knowing for any phone:

- this kernel has **no devtmpfs** (`CONFIG_DEVTMPFS is not set` in the embedded config), so
  `mount devtmpfs` fails and nothing creates device nodes; AOSP first-stage init mounts tmpfs on
  `/dev` and creates the nodes itself;
- `/dev/block/by-name/*` links are created by ueventd, not the kernel;
- the watchdog module has dependencies (`dss.ko`, `exynos-pmu-if.ko` from `modules.dep`), so a
  bare `insmod` silently fails;
- Samsung keeps crash logs in `debug_snapshot` reserved memory (`/proc/last_kmsg`), not ramoops,
  and its content was found corrupted on this phone.

([EXPERIMENTS.md](../../EXPERIMENTS.md), "Update — Opus 5 independent review" and the
Opus/Astra review rounds.)

**Lesson:** read the *embedded* kernel config of the kernel you actually boot (IKCONFIG), not a
defconfig from a source tree, and validate your logging with a known crash before trusting
"no log" as evidence.

## Step 3: what works: keep AOSP first stage, intercept `selinux_setup`

The working design ([tools/native-handoff/README.md](../../tools/native-handoff/README.md)):

1. `/init` is a byte-identical copy of the original AOSP first-stage init. It loads the full
   vendor module set in the vendor's order and sets up `/dev`, just as it does for Android.
2. The shipped `/system/bin/init` is renamed to `init.android`; our
   [`init-wrapper.c`](../../tools/native-handoff/init-wrapper.c) takes its place. It passes every
   call through **except** the second-stage `selinux_setup` call, and only when a build-time
   `/native-enable` marker exists.
3. At that point [`native-guardian.c`](../../tools/native-handoff/native-guardian.c) becomes
   PID 1. It owns the hardware watchdog and starts
   [`native-start`](../../tools/native-handoff/native-start), which unpacks an Alpine root,
   brings up USB Ethernet and SSH.
4. If native startup fails early, the guardian can fall back to the original AOSP
   `selinux_setup` (Android recovery). After the grace period a live native session is never
   handed back to Android.

First real boot: SSH answered about 15 s after kernel start, `/proc/1/exe` was the guardian and no
Android init, adbd or service manager was running
([EXPERIMENTS.md](../../EXPERIMENTS.md), "Native handoff V1";
[evidence/native-linux-20260919](../../evidence/native-linux-20260919/)).

One rescue path failed on the way: a deliberate hot switch from native USB back to Android
recovery broke USB enumeration for that boot. Since then a running native session stays native,
and that path is documented as unproven ([NATIVE_LINUX.md](../NATIVE_LINUX.md#boot-image-and-evidence)).

## Getting the image onto the phone without Download Mode

Once you have root on the phone (Lineage recovery ADB, later native SSH), you can write the
RECOVERY block device directly and read the whole partition back to compare hashes; we did that
for V1 and later images, and it avoids Download Mode on the way *in*
([EXPERIMENTS.md](../../EXPERIMENTS.md), "Native handoff V1";
[S22_LUNA_DRIVER_COMPLETION_MISSION.md](../S22_LUNA_DRIVER_COMPLETION_MISSION.md#41-reuse-the-existing-in-session-deployment-route)).
It does **not** help if the new image fails before SSH: then you need the key combination and
Download Mode.

To get root ADB in Lineage recovery without touching any key, we added our host's public ADB key to
the recovery ramdisk (two changed records, nothing else)
([tools/headless-recovery/README.md](../../tools/headless-recovery/README.md)).

## Rebooting into recovery from software

Samsung's kernel has a `sec_reboot` notifier that selects the boot target from a Linux
`RESTART2` argument. Our static `restart2` helper calls it with the literal `recovery` target and
never writes the Android boot-control record in MISC
([tools/headless-recovery/README.md](../../tools/headless-recovery/README.md)). On the phone this is
`s22-reboot recovery` ([NATIVE_LINUX.md](../NATIVE_LINUX.md#connect-and-use)).

Don't use a hand-written MISC/BCB record. Ours failed Samsung's integrity check
([FLASH_LOG.md](../../FLASH_LOG.md)).

## How we know which mode actually booted

Samsung's bootloader keeps a boot-reason log ("BORE"). Read only its start:
`head -c 4096 /proc/boot_reset | tr -d '\000'`. It records the selected boot mode, not proof that
your init ran ([NATIVE_LINUX.md](../NATIVE_LINUX.md#boot-image-and-evidence)). Never judge the boot
mode from the splash screen. On our native root the kernel's BORE reader needed a
`/dev/block/by-name/debug` link, created from sysfs `PARTNAME` metadata
([EXPERIMENTS.md](../../EXPERIMENTS.md), "Native handoff V1").

## Rescue checklist (have all of these before every image experiment)

| What | Ours |
|---|---|
| Verified known-good recovery image + hash | Lineage `recovery.img`, SHA256 `b5bf01c4…7b55` ([NATIVE_LINUX.md](../NATIVE_LINUX.md#rollback)) |
| Stock firmware for your bootloader version | FYI3 boot/recovery/vendor_boot/dtbo/vbmeta with hashes ([FLASH_LOG.md](../../FLASH_LOG.md)) |
| A tool to flash from Download Mode on your host | `samloader` (`tools/samloader/`), ([NATIVE_LINUX.md](../NATIVE_LINUX.md#rollback)) |
| The key combinations, written down | Recovery: Volume Up + Power with USB; Download: Volume Up + Volume Down, then USB ([DEVICE.md](../../DEVICE.md#special-boot-modes)) |
| A way to tell the mode | BORE log, not the screen |
| A list of partitions you will never write | PIT, EFS, IMEI/NV, MISC, bootloader, TrustZone ([DEVICE.md](../../DEVICE.md#do-not-touch)) |

## Normal power-on boot is a separate, unsolved step

Persistent files do not change which partition the bootloader picks after a full power-off. Our
one attempt to put Linux in BOOT gave no USB connection at all and was rolled back from Download
Mode. The suspected cause is a compression mismatch when the bootloader concatenates our gzip
ramdisk with the vendor's legacy-LZ4 ramdisk; a corrected LZ4 candidate was built but never
flashed ([tools/native-boot/README.md](../../tools/native-boot/README.md),
[PERSISTENCE.md](../PERSISTENCE.md#cold-boot-is-a-separate-gate)). If you try this, validate the
*whole concatenated ramdisk stream*, not each piece, and keep RECOVERY as your rescue.

`kexec` is not enabled in this kernel's config, so "boot Linux from Linux" is not available out of
the box either ([KEXEC_FEASIBILITY.md](../KEXEC_FEASIBILITY.md)).
