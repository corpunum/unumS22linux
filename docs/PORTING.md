# Porting guide: native Linux on your own Galaxy S22 (or another Android phone)

This guide explains how this project got a Galaxy S22 to boot its own Linux with no Android
services, and how you could try the same on your own phone. It describes **what we actually did**,
step by step, and links to the file in this repo that backs each claim. Where something was not
tested, it says so.

It is not an installer. There is no image you can download and flash. Every phone needs its own
images, built from that phone's own firmware.

Chapters:

| Chapter | What it covers |
|---|---|
| [Boot and rescue](porting/boot-and-rescue.md) | Running your own init from the RECOVERY partition, keeping a way back |
| [Backups and vendor files](porting/backups-and-vendor-files.md) | Raw partition backups; where drivers, firmware, HALs and `.rc` files live in the stock image |
| [Working with coding agents](porting/agent-workflow.md) | How we prompted agents, the adversarial review loop, evidence rules, dead-man switches |
| [Wi-Fi and Bluetooth](porting/wifi-bluetooth.md) | Vendor `wlan.ko`, firmware timing, a userspace Bluetooth holder |
| [Audio](porting/audio.md) | Firmware staging, `mixer_paths.xml`, the DSP graph found in the stock audio library |
| [GPU and NPU](porting/gpu-npu.md) | Samsung's Vulkan/OpenCL and NPU stack inside an isolated Bionic runtime |
| [Modem and GNSS](porting/modem-gnss.md) | Our own modem daemon, the stock GPS engine in a sandbox |
| [Camera](porting/camera.md) | Raw sensor capture without the ISP, and a software ISP |
| [Display, input and power](porting/display-input-power.md) | Panel stride, touch, buttons, suspend-to-RAM, the touch shell |
| [What the scripts assume](porting/device-assumptions.md) | S22 paths, device names and addresses in this repo, and what to change |

## Who this is for

- You own a **Galaxy S22 SM-S901B/DS** and want to repeat this. You are closest; most of the
  method and many of the scripts apply, but you still build your own images from your own phone.
- You own **another Android phone** (another Exynos Samsung, or something else) and want to use
  the *method*: boot your own userspace on the vendor kernel, then reuse the phone's own drivers
  one subsystem at a time.
- You are comfortable with a Linux shell, SSH and reading logs, or you work with a coding agent
  that is, and you accept that the phone may need a physical recovery.

The owner of this project does not write code. Agents (Claude, GPT models through
[OpenUnum](https://corpunum.com), a local Qwen) did the engineering, with other agents reviewing
each step ([README](../README.md#the-story)). So "can't code" is not a blocker. "Can't recover a
phone that doesn't boot" is.

## Read this first: hard warnings

These come from things that happened here or that this project refused to do. Please read all of
them before you unlock anything.

1. **Unlocking the bootloader changes the phone for good.** Our phone reports
   `ro.boot.warranty_bit = 1` and `verifiedbootstate = orange` after unlocking and flashing
   ([DEVICE.md](../DEVICE.md), [evidence/previous_failed_boot](../evidence/previous_failed_boot/README.md)).
   On Samsung that warranty bit is the Knox flag (general knowledge, not something this repo
   tested): expect lost warranty and Knox-dependent features. Unlocking can also erase the phone
   ([PHONE_PORTABILITY.md](PHONE_PORTABILITY.md#practical-selection-and-acceptance)).
2. **You will lose the Android data.** Android's userdata is encrypted (F2FS, file-based and
   metadata encryption, wrapped keys). It is not a spare Linux disk, and a raw copy of it is
   ciphertext, not a usable backup of your photos ([PERSISTENCE.md](PERSISTENCE.md),
   [tools/persistence/README.md](../tools/persistence/README.md#critical-limitation)). Back up
   your personal files from Android, by normal means, **before** you start.
3. **Never write these partitions:** PIT, EFS / sec_efs, IMEI / modem NV, MISC, the bootloader
   partitions and TrustZone / security partitions ([DEVICE.md](../DEVICE.md#do-not-touch),
   [CONTRIBUTING.md](../CONTRIBUTING.md#4-safety-rules-non-negotiable)). EFS holds your IMEI and
   radio calibration; losing it can make the phone unable to use a mobile network, and it cannot
   be downloaded again. Our modem daemon writes the modem's NV data **only into a private copy**
   of EFS ([evidence/modem-20261006](../evidence/modem-20261006/README.md)).
4. **One MISC write already nearly bricked this phone.** Flashing a hand-made "boot to recovery"
   record into MISC failed Samsung's integrity check and left the phone unable to boot until the
   factory MISC was flashed back ([FLASH_LOG.md](../FLASH_LOG.md)). Don't write boot-control
   records by hand.
5. **A failed boot image can leave you with no USB at all.** Our one attempt at a Linux BOOT image
   gave no SSH, no ADB and no Download Mode connection; getting back needed the phone to be in
   Download Mode (seen on its screen by the owner) and a re-flash of the original BOOT
   ([tools/native-boot/README.md](../tools/native-boot/README.md),
   [STATUS.md](../STATUS.md#earlier-boot-attempt-history-superseded-snapshots)). Have a recovery
   path that does not depend on your new image.
6. **Keep a verified way back before every image experiment.** We kept a hash-verified LineageOS
   recovery image and the exact stock firmware for the phone's current bootloader version, plus a
   tool to flash them from Download Mode ([NATIVE_LINUX.md](NATIVE_LINUX.md#rollback),
   [FLASH_LOG.md](../FLASH_LOG.md)). "Verified" means you checked the hash and, when you can,
   read the partition back after writing it.
7. **Some hardware operations crash or reboot the phone.** Known ones here: rebinding the audio
   topology driver at runtime (kernel panic), NPU models with a DSP section (watchdog reboot),
   large audio DSP periods (DSP crash), mmap on the DSP PCMs
   ([CONTRIBUTING.md](../CONTRIBUTING.md#4-safety-rules-non-negotiable)). The GNSS engine has a
   code path that reboots the whole phone ([GNSS.md](../tools/hardware/gnss/GNSS.md#23-gpsd-paths-that-reach-outside-gnss-verified-by-disassembly)).
   Your phone will have its own. Expect them, and write each one down when you find it.
8. **Vendor files are not yours to redistribute.** Firmware, HAL libraries and partition dumps are
   Samsung's (or your phone maker's). This repo publishes code, patches, hashes and analysis notes,
   never the blobs ([PUBLICATION.md](PUBLICATION.md#deliberately-local-only)). **Extract them
   from your own device or its official firmware.** Don't ask others to send you theirs, and
   don't post yours.
9. **Phones have identities.** IMEI, serials, SIM IDs, Wi-Fi names, IP and Tailscale addresses and
   SSH host keys identify you and the device. Redact them before you publish logs; this repo
   rewrote its own history once to remove some ([PUBLICATION.md](PUBLICATION.md#privacy-review-2026-09-21)).
10. **Telephony is regulated.** Our modem daemon refuses emergency numbers and refuses to send SMS
    or dial unless an explicit allowlist and enable file exist
    ([tools/hardware/phoned/README.md](../tools/hardware/phoned/README.md#safety-gates-and-state-files)).
    A phone running experimental Linux is **not a phone you can rely on for emergency calls**;
    calls don't work here at all yet ([README](../README.md#what-works-and-what-doesnt-yet)).

None of this is meant to scare you off. The phone in this repo went through a long series of failed boots
and several kernel panics and is fine. It survived because every step had a way back.

## Compatibility tiers

Pick your tier honestly. The further down, the less of this repo transfers, and the more you are
doing new work.

| Tier | Your phone | What transfers |
|---|---|---|
| **1. Same model** | Galaxy S22 **SM-S901B/DS** (`r0s`, Exynos 2200) | The whole method and most scripts, after the changes in [What the scripts assume](porting/device-assumptions.md). You still build every image from your own phone's firmware and a matching LineageOS recovery. Check your bootloader version first: ours was `S901BXXSIFYI3` ([DEVICE.md](../DEVICE.md)); a different one means different vendor files. |
| **2. Same SoC family** | S22+ `SM-S906B` (`g0s`), S22 Ultra `SM-S908B` (`b0s`) | Same Exynos 2200 kernel source tree with separate configs ([PHONE_PORTABILITY.md](PHONE_PORTABILITY.md#closest-s22-relatives)). The GPU/NPU/modem/GNSS approach probably carries over; panel, touch, pen, DTB and partition sizes differ. **Hypothesis only**, never tested here. Never flash S22 images onto them. |
| **2b. Snapdragon S22** | Snapdragon S22 models (US/other regions) | A different SoC. None of the Exynos driver work applies (no Xclipse GPU, Exynos NPU, Shannon modem or ABOX DSP). Only the method transfers. Check that the bootloader can be unlocked at all: SIM-unlocked is not bootloader-unlocked ([PHONE_PORTABILITY.md](PHONE_PORTABILITY.md#practical-selection-and-acceptance)). |
| **3. Other Samsung / Exynos** | e.g. Exynos S9 (`starlte`) and newer Exynos Galaxy phones | The method, and the *kinds* of pieces to look for (Samsung RIL/cbd, ABOX, ENN, gpsd). Paths, ioctls and firmware names will differ. Not qualified by our survey ([OTHER_PHONES.md](OTHER_PHONES.md)). |
| **4. Any other Android phone** | Unlockable phone with a LineageOS (or similar) build | The method: boot your own userspace on the vendor kernel from a spare boot partition, keep a rescue path, then bridge vendor drivers one at a time. Everything device-specific is new work. Phones with A/B slots or boot-as-recovery need a different boot route ([PHONE_PORTABILITY.md](PHONE_PORTABILITY.md#what-transfers-from-this-project)). |
| **5. Phone with a mainline Linux port** | OnePlus 6/6T, POCO F1, Fairphone 4/5, Pixel 3a, PinePhone, Librem 5 … | **Use that port** (postmarketOS, Mobian etc.) instead of this method. It has an open kernel and maintained drivers; our vendor-kernel handoff is unnecessary and potentially destructive there ([OTHER_PHONES.md](OTHER_PHONES.md)). Our Rust touch shell can still run on it ([tools/unum-fastshell/PORTING.md](../tools/unum-fastshell/PORTING.md)). |

If you are buying a phone *for* Linux, tier 5 is the sensible choice. Our own shortlist is OnePlus
6/6T, POCO F1 or Fairphone 4 ([OTHER_PHONES.md](OTHER_PHONES.md)).

## The method, in order

Each step has a chapter. Do them in order and don't start the next until the previous one has
evidence (a log, a measurement, a screenshot) that it works.

### 0. Know your exact phone

Record model, codename, bootloader version, unlock state and the kernel version. Ours, as read
from Android properties, is in [DEVICE.md](../DEVICE.md). Find an official LineageOS (or
equivalent) build for **exactly** your model: we used its kernel, DTB/DTBO and recovery image as
the base, because they were already known to boot and their sources are public
([RECOVERY_COMPARISON.md](RECOVERY_COMPARISON.md), [NATIVE_LINUX.md](NATIVE_LINUX.md)).
Download the **stock firmware for your current bootloader version** from Samsung's servers
(we used `samloader-rs`) and keep it with its hashes ([FLASH_LOG.md](../FLASH_LOG.md)).

### 1. Back up, and prove you can restore

Flash the stock LineageOS recovery first and confirm it boots ("control test"). From there, take
raw, hash-verified backups of the partitions you will need to read later (vendor/super, vendor_boot,
boot, recovery, dtbo …), copied off the phone. See
[Backups and vendor files](porting/backups-and-vendor-files.md).

### 2. Boot your own userspace from RECOVERY, with a rescue

The important lesson from the first two days (builds V2 to V24 in the log): **don't replace
Android's first-stage init.** The builds that replaced `/init` appeared to crash-loop, but we never
got a log to prove why, and later found our own logging and some image headers were broken. What
finally worked keeps the real AOSP
first-stage init (it loads the ~324 vendor modules and sets up `/dev`) and intercepts only the
next step, `selinux_setup`, to start our own PID 1 ([EXPERIMENTS.md](../EXPERIMENTS.md),
[tools/native-handoff/README.md](../tools/native-handoff/README.md)). Our PID 1 owns the watchdog,
starts SSH over USB Ethernet, and keeps a rescue. See [Boot and rescue](porting/boot-and-rescue.md).

### 3. Make it persistent

We first kept changes in an overlay on the small CACHE partition, then (with explicit owner
approval, after backups) converted userdata to ext4 for the main Arch Linux root and models
([PERSISTENCE.md](PERSISTENCE.md), [tools/native-handoff/README.md](../tools/native-handoff/README.md#optional-cache-persistent-variant)).
The persistent desktop is optional: if it fails, the phone falls back to a rescue desktop, and SSH
never depends on it ([tools/persistence/README.md](../tools/persistence/README.md#installed-persistent-desktop)).

### 4. Network and SSH

USB Ethernet (ECM gadget) with SSH first, because it needs no radio drivers. Wi-Fi came next
(vendor module and firmware), then Tailscale, and much later mobile data with automatic failover
([NATIVE_LINUX.md](NATIVE_LINUX.md#storage-and-networking),
[evidence/network-independence-20261022](../evidence/network-independence-20261022/README.md)).
Use a pinned host key; never turn off host-key checking ([PUBLICATION.md](PUBLICATION.md)).

### 5. One subsystem at a time, each with evidence

For every subsystem we followed the same loop:

1. **Read before touching.** Find what Android uses for it in the stock image: the kernel modules
   and their load order, firmware files, the HAL `.so`, the `init` `.rc` files (they show the
   start order, arguments, users and device permissions), config such as `mixer_paths.xml`.
2. **Find the smallest native path.** Often the kernel driver already works and only needs its
   firmware at the right time, the right device permissions, or a client that does what the HAL
   did. Where the vendor userspace is unavoidable (GPU, NPU, GNSS engine), run it in a small,
   isolated Bionic runtime with only the device nodes it needs, never the Android framework.
3. **Write a bounded, reviewed test** with a deadline, a cleanup step and a rollback.
4. **Run it once**, capture the result, and record it, failures included, in `evidence/` and
   [EXPERIMENTS.md](../EXPERIMENTS.md).
5. **Only then** wire it into boot.

The [Subsystem playbooks](#subsystem-playbooks) below show what this looked like for each piece.

### 6. Let agents do the work, but make them check each other

See [Working with coding agents](porting/agent-workflow.md): one coordinator owns the phone,
workers write code in separate worktrees, independent reviewers try to break each change before
it runs, and nothing counts as working without device evidence.

## Subsystem playbooks

What we looked for in the stock image, and how we bridged it. Details and the files behind each
line are in the chapters.

| Subsystem | Stock pieces we looked at | How we bridged it | State here |
|---|---|---|---|
| Boot | Lineage recovery image, first-stage init, `modules.load` | Keep AOSP first stage; wrapper intercepts `selinux_setup`; our guardian is PID 1 | ✅ [chapter](porting/boot-and-rescue.md) |
| Wi-Fi | `vendor_dlkm/wlan.ko`, QCA6490 firmware, `wlan_vendor_rc` / `wlan_common_rc` | Lineage module (stock one is ABI-incompatible), read-only firmware mounts, reproduce the `.rc` ordering inside its deadline, answer one optional firmware request | ✅ [chapter](porting/wifi-bluetooth.md) |
| Bluetooth | QCA6490 BT firmware/NVM files | Our own userspace "holder": powers the chip, loads patch and NVM over UART, bridges H4 to the kernel through a PTY line discipline | 🟡 [chapter](porting/wifi-bluetooth.md) |
| Audio | `mixer_paths.xml`, ABOX firmware, `libaudioproxy2.so` | Stage missing firmware at boot, flatten `mixer_paths.xml` to `amixer` batches, play into the virtual frontends the stock library uses | ✅ [chapter](porting/audio.md) |
| GPU | `vulkan.samsung.so`, OpenCL libs | Isolated Bionic root, private `/dev` with only the GPU nodes, dropped privileges | ✅ compute [chapter](porting/gpu-npu.md) |
| NPU | `npu.ko`, `AIE.bin`, ENN libs, NNAPI support library | Stage firmware, BOOTUP on the stock module, ENN in the Bionic root; the vendor NNAPI library as a compiler | ✅ [chapter](porting/gpu-npu.md) |
| Modem | `cbd`, `/dev/umts_*`, ueventd perms, RIL | Run stock `cbd` with Android's device ownership; our own SIPC daemon instead of the RIL; NV to a private EFS copy | ✅ SMS/data, ❌ calls [chapter](porting/modem-gnss.md) |
| GNSS | `gpsd`, `gps.cfg`, `init.gps.rc`, HAL `.so` | Stock engine in a seccomp sandbox with no modem nodes; our own HAL client | 🟡 tracks, no fix yet [chapter](porting/modem-gnss.md) |
| Camera | Pablo `fimc_is` driver, sensor setfiles, OIS firmware | Our own V4L2 client for raw frames (no ISP, no TrustZone); our own software ISP | ✅ stills [chapter](porting/camera.md) |
| Display/touch/power | DPU driver source, touch evdev, battery sysfs | Stride workaround, our own KMS touch shell, deep suspend with no Android suspend daemon | ✅ [chapter](porting/display-input-power.md) |

## What this repo's scripts assume

Almost every script hard-codes something about *this* phone: partition names, USB addresses,
the userdata UUID, `/srv/s22/...` paths, ALSA PCM numbers, evdev names, the host user's home
directory. They refuse to run when an identity check fails, which is good, but it also means they
won't "just work" elsewhere. The list and what to change: [What the scripts assume](porting/device-assumptions.md).

## FAQ

**Can I just flash your image?** No. There is no public image, and our images contain this
phone's kernel, modules and identity checks. Flashing them onto another model, even an S22+ or
Ultra, is exactly what the [README](../README.md) and [OTHER_PHONES.md](OTHER_PHONES.md) tell you not to do.

**Is this mainline Linux?** No. It runs the Samsung/LineageOS vendor kernel (5.10) and its
modules. Linux userspace (Alpine, Arch Linux ARM) runs on top with no Android services
([NATIVE_LINUX.md](NATIVE_LINUX.md)). If your phone has a mainline port, use that.

**Is any Android code still running?** The AOSP first-stage init runs briefly at boot to load
modules, then hands over ([tools/native-handoff/README.md](../tools/native-handoff/README.md)).
Some vendor userspace runs in isolated Bionic roots when called: GPU/NPU libraries, the modem
boot daemon `cbd`, the GNSS engine. No Android framework, graphics services, binder or property
service run ([tools/gpu-compat/README.md](../tools/gpu-compat/README.md),
[GNSS.md](../tools/hardware/gnss/GNSS.md), [S22_LUNA_DRIVER_COMPLETION_MISSION.md](S22_LUNA_DRIVER_COMPLETION_MISSION.md#1-mission-and-operating-rules)).

**Does it boot when I power the phone on normally?** Not yet. It boots from RECOVERY; we reboot
into it in software with `s22-reboot recovery`. After a full power-off you need the recovery key
combination ([DEVICE.md](../DEVICE.md#special-boot-modes),
[PERSISTENCE.md](PERSISTENCE.md#cold-boot-is-a-separate-gate)).

**Can I go back to Android?** Re-flashing stock firmware from Download Mode is the way back. We
kept the stock images for that ([FLASH_LOG.md](../FLASH_LOG.md)). Your Android data will not come
back: userdata was reformatted.

**Do I need a second computer?** Yes, for flashing from Download Mode, building, and SSH over
USB. We used an Ubuntu x86-64 host with an ARM64 cross toolchain, QEMU for testing ARM binaries,
and the Android NDK for Bionic programs ([tools/linux-rootfs/README.md](../tools/linux-rootfs/README.md),
[GNSS.md](../tools/hardware/gnss/GNSS.md), [initramfs/nativev5_notes_for_sol.md](../initramfs/nativev5_notes_for_sol.md)).

**How long did this take?** Roughly five weeks of agent work, 2026-09-18 to 2026-10-22, with the
first working native boot on day two ([EXPERIMENTS.md](../EXPERIMENTS.md),
[NATIVE_LINUX.md](NATIVE_LINUX.md)). Most of the time went into drivers, not boot.

**What still doesn't work?** Calls and call audio, Bluetooth pairing and audio, a GPS fix,
normal power-on boot, speech recognition on the NPU ([README](../README.md#what-works-and-what-doesnt-yet),
[STATUS.md](../STATUS.md)).

**Can I help without risking a phone?** Yes. Most open problems can be worked on from the
evidence alone; see [CONTRIBUTING.md](../CONTRIBUTING.md#working-with-your-agent-on-this-repo).

**I ported this to another phone. What should I share?** Your method, scripts, patches, hashes and
redacted evidence, never the vendor files. Following the structure of an `evidence/<topic>-<date>/`
folder makes it easy for others to check your claims.
