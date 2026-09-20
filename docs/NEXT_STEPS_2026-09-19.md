# S22 native Linux: pre-flash findings and proposed next steps

Status: research and read-only device inspection completed on 2026-09-19.
No device writes, reboot commands, flashes, or workload runs were performed.
V24b remains untested. This document proposes the next physical experiment;
it does not record that experiment as completed.

## Verified baseline

Read all 913 pre-existing lines of EXPERIMENTS.md before investigating.
The working copy was clean on master. Live root ADB identifies SM-S901B/r0s,
bootloader S901BXXSIFYI3, LineageOS 23.2-20260915-NIGHTLY-r0s, kernel
5.10.260-g4e5c5ad7d950. BORE still ends at record 511, the recovery boot selected
by key on 2026-09-19 at 17:55:45 according to the device RTC.

The local rollback image and the entire live RECOVERY partition have the same
SHA256:

```
b5bf01c4a47091eb95078fc69b133b44c2b453b31c23433594c5b605e3747b55
```

The handoff and some historical documents omit the final `5`, producing a
63-character string rather than a complete SHA256. The local file matches the
currently working partition; a fresh check of the published download manifest
was not performed in this session.

RECOVERY resolves to /dev/block/sda16, is 100663296 bytes, is not mounted, and
blockdev --getro reports 0. The current recovery root filesystem and /tmp are
RAM-backed. This makes an ADB-based RECOVERY write a plausible way to avoid a
Download Mode round trip. Actual write success has not been tested.

## V24b packaging checks

Image SHA256 matches the handoff:

```
5e58e5e5e8511c54fc197da523189db8a6fe8e92f01d66616562891d79395db2
```

Both reference and V24b are 100663296 bytes. Fresh unpacking confirmed identical
kernel, DTB, and recovery DTBO bytes. Load addresses, header version 2, page size
2048, OS version 16.0.0, patch level 2026-09, and command line match the reference.
Ramdisk size and its dependent payload offsets differ, as expected; saying every
header field is identical would be inaccurate.

The image-contained /init hashes to
`d3596dca9c07c857fb8425021f68917d66900ec5b8b44beef6788813da12a92a`, matching the
local static aarch64 executable initramfs/cinit13. This verifies binary identity,
not a fresh reproducible rebuild from source.

avbtool verifies both images' internal recovery hash descriptors and NONE vbmeta
structures. For V24b, avbtool requires a filename recovery.img when following
the descriptor; a temporary symlink to the original image supplied this name.
Algorithm NONE supplies no cryptographic signer authentication. These checks
do not prove bootability or another partition's chain-of-trust acceptance.

## What software reboot actually does

Source revisions were taken from lineage/build-20260915/build-manifest.xml:

- system/core: 0e96ff13d90df568ddd788bb02b3ddb6e3641528
- packages/modules/adb: e7ed41a85f5a0a8c9db0ed7ba21c6ebd277b5db6
- bootable/recovery: d4ef5569dda1c5066f90efd25455b589ced6073e
- Local kernel HEAD: 4e5c5ad7d950e4de0688b5663965f2075654b2ad

Ordinary `adb reboot recovery` reaches sys.powerctl. In the matching init source,
the recovery branch reads the BCB and writes boot-recovery if the command is
empty. The live first 32 MISC bytes are zero. No device-specific override of the
default BCB offset was found in the two device repositories inspected.
[Matching init source](https://github.com/LineageOS/android_system_core/blob/0e96ff13d90df568ddd788bb02b3ddb6e3641528/init/reboot.cpp).

The BCB writer opens MISC, seeks to the configured offset, writes the structure,
and fsyncs. It has no Samsung signing step. Unlike the failed Odin incident,
this is an in-place runtime update, preserving the rest of the partition.
The earlier incident therefore does not prove that normal runtime BCB updates
are rejected, but neither does it establish their safety on this phone.
[Matching BCB writer](https://github.com/LineageOS/android_bootable_recovery/blob/d4ef5569dda1c5066f90efd25455b589ced6073e/bootloader_message/bootloader_message.cpp).

There is a separate kernel mechanism: the loaded sec_reboot module handles the
recovery string by setting the PMU recovery reason 0x12345674. Its priority 130
handler runs before PSCI's priority 129 restart. Download and bootloader strings
also have explicit cases. This supports a candidate direct Linux RESTART2
request with target recovery, without a userspace BCB write. It does not prove
the entire subsequent recovery boot will refrain from normal MISC bookkeeping.
[Kernel implementation](https://github.com/LineageOS/android_kernel_samsung_s5e9925/blob/4e5c5ad7d950e4de0688b5663965f2075654b2ad/drivers/samsung/sec_reboot.c).

`adb shell reboot recovery` and `setprop sys.powerctl reboot,recovery` are not
BCB-free alternatives: they reach the same init path. The matching standalone
/system/bin/reboot supports -p, not a force/direct-syscall -f option.

No software reboot test was run. Consequently, standard ADB recovery boot and
the direct RESTART2 candidate remain unproven on this exact bootloader.

## Proposed experiment, in order

1. Resolve the write-scope ambiguity below. Preserve host-side copies of the
   latest BORE records and current cache evidence before any reboot. Use a raw
   read or an ext4 read-only mount with noload for inspection, not a mount that
   replays the journal. Preserve existing v24 files rather than deleting them.
2. Prove recovery-to-recovery target selection with the known-good image still
   installed. Under a strict prohibition on initiating BCB writes, prepare a
   small static helper using the normal Linux reboot syscall with RESTART2 and
   target recovery. Review its source before execution. Verify sec_reboot is
   loaded/bound and persistent filesystems are quiescent. This helper would skip
   Android's orderly service shutdown, so it is intended only for this RAM-based
   recovery environment, not a running Android installation.
3. After the control boot, capture BORE immediately and measure whether root ADB
   returns without interaction. The current /adb_keys points to an unavailable
   /product key file; ro.adb.secure.recovery is currently 0, consistent with the
   manual Enable ADB action. Persistence across a reboot has not been proven.
   A successful recovery boot may still need Advanced -> Enable ADB.
4. If target selection works, stage the unchanged V24b in recovery's /tmp, verify
   the staged hash, write only /dev/block/by-name/recovery, flush, and hash the
   full partition before reboot. This avoids Download Mode for the incoming
   flash. Any mismatch stops the test before reboot and calls for restoring the
   known-good image. Never use an auto-matched partition name or a whole-disk
   target.
5. Use the proven selector, then observe USB state and host timestamps without
   issuing resets. Allow at least 120 seconds without manual intervention to
   cover the requested 60 seconds and uncertainty in boot/module-load time.
   Do not interpret a logo change as a recovery boot or as a crash.
6. V24b deliberately requests an untargeted reboot after reaching approximately
   kernel uptime 45 seconds, or later if setup overruns. It does not start adbd
   or configure a usable USB network service. Automatic return to ADB or Download
   Mode is not guaranteed. Arrange physical rescue availability for this test.
7. If rescue is needed, enter Download Mode and restore the verified reference
   to RECOVERY only. Re-enter recovery, re-enable ADB if necessary, and capture
   BORE before extra reboots. Then collect v24_log.txt and v24_boot_count.txt
   from cache while preserving their existing contents.

BORE proves the bootloader selected recovery and bounds the interval until a
later reset. It does not prove /init executed for that whole interval. Pair it
with the versioned cache log, boot counter, and monotonic heartbeat timestamps.
The final self-reboot intent line alone does not prove the syscall completed;
sync, unmount, and device shutdown occur afterward. If there are no surviving
logs, classify execution as unknown instead of declaring an immediate PID1 crash.

V24b's 45-second self-reboot means this test can establish an uninterrupted
diagnostic attempt, not 60 seconds of continuous Linux uptime. Keep this image
unchanged for the first test. A later longevity build would be a separate test.

## Minimum manual fallback

The installed samloader's flash command supports --no-reboot but exposes no
recovery-target option. ADB is unavailable in Download Mode. There is no verified
Download-to-RECOVERY software command in the inspected tool/source evidence.

With USB connected to the PC, leave Download Mode with Volume Down + Power;
as the screen goes black, switch immediately to Volume Up + Power to request
recovery. Once selected, release the buttons and leave the test alone.
This is the proposed single transition sequence, with boot-mode confirmation
deferred to BORE. The reboot-time Volume Up + Power requirement is documented
for r0s and S901BXXSIFYI3 in the
[LineageOS device definition](https://github.com/LineageOS/lineage_wiki/blob/main/_data/devices/r0s.yml).
If the timing is missed, do not repeat rapid forced resets during a potentially
running experiment. Allow the observation interval before recovery intervention.

Rollback command, only once the phone is detected in Download Mode:

```bash
/home/corpunum/s22-linux/tools/samloader/samloader flash --verbose --no-reboot -p RECOVERY /home/corpunum/s22-linux/lineage/build-20260915/recovery.img
```

Online searches included XDA, but did not locate a reliable exact-bootloader
account establishing a better transition. Generic Samsung instructions are not
proof for this unit. No speculative Odin target values should be sent.
If any flash command is rejected by automatic approval review, stop and hand
the exact intended command to the owner; do not change tools to bypass rejection.

## Route to a useful Linux handheld

The most practical native route is the proven Lineage/Samsung downstream kernel
and matching modules with a conventional ARM64 Linux userspace. Removing Android
userspace does not require replacing that kernel with mainline first. No working
mainline r0s port was established by this investigation; samsung-r0q references
describe a different, Snapdragon device.

| Milestone | Evidence now | Next proof required |
|---|---|---|
| CPU and RAM | Native aarch64 root shell; MemTotal 7448240 kB, about 7.1 GiB | Native Linux PID1, package tools, networking, sustained workload |
| Display and touch | Working recovery UI; DSI panel connected with 1080x2340 modes | Non-Android DRM/KMS scanout and input in a simple compositor |
| GPU | sgpu loaded; DRM card0 and render nodes present; downstream amdgpu-derived driver | Native userspace driver enumeration, rendering correctness, then compute |
| NPU | npu loaded and /dev/vertex10 present | Matching compiler/runtime, firmware interface, and a real supported model |

After V24b produces evidence, prioritize USB networking plus SSH, watchdog and
thermal stability, and a normal Linux PID1. Use a small initramfs and a RAM or
network root filesystem initially. RECOVERY is only 96 MiB; a useful desktop and
models need storage outside that boot image. Internal persistent installation
and making Linux the default normal-power-on target are later scope decisions.

For the first user environment, a minimal Debian ARM64 or Alpine system is a
reasonable bring-up choice; Arch Linux ARM remains suitable for the desired final
desktop. Distro choice does not solve the device-driver interface. Establish
simple software-rendered Wayland output first if supported by the display driver,
then acceleration, then Hyprland and selected Omarchy components.

Omarchy has announced active ARM work, so assuming it remains exclusively x86_64
would now be stale. This is not an S22 installer or proof of phone support.
[Omarchy ARM announcement](https://omarchy.org/news/2026/09/introducing-omarchy-m/).

For GPU exploration, there is an existing Xclipse 920 RADV experiment whose author
reports initialization and shader progress but broken presentation and hangs.
It is a useful lead to inspect, not a known-working driver to install blindly.
[Xclipse RADV project](https://github.com/mxxme-dev/radv-xclipse-patches).
Stock RADV documentation alone does not establish compatibility with Samsung's
modified platform driver. RDNA ancestry does not establish ROCm support either.

Agent orchestration, Python, Git, and CPU inference are useful early targets.
Benchmark a small quantized model only after cooling and power behavior are
understood. GPU inference depends on proving a supported compute backend first;
llama.cpp documents CPU and Vulkan build paths, but no performance claim for
this phone is made here.
[llama.cpp build documentation](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md).

The device's proprietary-file list names Samsung ENN and GPU userspace libraries.
Samsung's public ENN delegate is described for Android apps, not a ready native
r0s Linux runtime. Separate Linux examples for an Exynos Auto V920 development
board do not prove Exynos 2200 compatibility. Treat NPU support as independent
research, not a dependency of the first usable Linux system.
[Samsung ENN delegate](https://github.com/Samsung/ENNDelegate).

## Scope clarification before execution

The inherited wording says only RECOVERY may be written, but unchanged V24b mounts
the separate CACHE partition read-write and persists the requested log/counter.
Ordinary Android recovery reboot can also update MISC. A raw kernel selector
avoids initiating that userspace BCB write but cannot promise that stock recovery
never performs its own housekeeping after boot.

Clarify whether the rule means RECOVERY-only image flashing, while allowing the
requested cache logging and normal OS bookkeeping, or literally no persistent
writes elsewhere. Under the latter interpretation, unchanged V24b cannot be run
as requested. Neither interpretation permits manually flashing MISC, PIT, EFS,
IMEI/radio calibration, BL/SBL, or TrustZone.
