# Persistent, standalone S22 Linux: decision and implementation plan

Status: **proposal only**, checked against the live phone on 2026-09-20.
No storage migration, partition format, new image flash or boot-target change
was performed for this assessment.

## Three separate requirements

1. **Keep files:** store Arch, configurations and model weights on nonvolatile
   storage instead of tmpfs.
2. **Start automatically:** the native bootstrap mounts that storage, starts
   the local model server and desktop, and offers a rescue path if either fails.
3. **Cold boot without a host/button combination:** the bootloader must select
   the correct Linux boot path after full power-off. Persistent files alone do
   not change Samsung's boot-target selection.

The small Alpine base already satisfies file persistence and automatic rescue
desktop/SSH startup through its CACHE overlay. Arch/Omarchy and the model do
not. Targeted `s22-reboot recovery` works, but ordinary cold-power-on Linux
selection is not proven or installed.

## What the live phone provides

| Item | Observed state |
| --- | --- |
| CACHE-backed overlay | 582.6 MiB filesystem; 495.9 MiB used; 74.6 MiB free |
| Arch UI staging | Roughly 1.8 GiB in tmpfs |
| Model staging | Roughly 1.2 GiB in tmpfs, plus inference working memory |
| userdata | 221,257,728 sectors × 512 bytes ≈ 105.5 GiB |
| userdata access | No recognized filesystem signature from the read-only probe; no decrypted device-mapper volume present |
| Boot/recovery sizes | BOOT 64 MiB; RECOVERY 96 MiB; not interchangeable image targets |

The matching device-tree fstab configures F2FS, file-based encryption,
metadata encryption and wrapped keys for userdata. Combined with the live
probe, this is evidence that the existing Android data volume is **not a
ready-to-mount spare Linux disk**. It is not proof that the volume is empty.
Do not infer safe formatting from `blkid` returning no signature.

Android metadata encryption also protects filesystem metadata; decrypting it
depends on the Android key-management path. See the
[AOSP metadata-encryption documentation](https://source.android.com/docs/security/features/encryption/metadata).
A file-backed Linux image would only help if its underlying encrypted storage
could first be accessed reliably at native boot. That has not been established.

## Recommended route for a dedicated Linux handheld

If the owner accepts losing Android's existing userdata, **reuse the existing
userdata partition as a Linux filesystem without changing the partition
table**. This is a proposed destructive conversion, not an authorized command.
Prefer ext4 for the first validated port; the running kernel supports it.

Before implementation:

1. Explicitly revise the current RECOVERY-only write boundary to allow the
   identified userdata partition. Confirm that Android app data, photos,
   accounts and files may be erased after preservation. Do not assume earlier
   bootloader unlocking means there is no remaining data.
2. Inventory and preserve data. Verify a restorable backup, not just a copied
   filename; an encrypted raw image is not a substitute for accessible file
   backups and does not guarantee restore across key/metadata changes.
3. Preserve the exact partition map and current boot/recovery artifacts
   read-only. Keep the verified Lineage rollback and native V3 rescue path.
   Never change PIT, EFS, IMEI, bootloader or TrustZone.
4. Format only the explicitly resolved, size-checked userdata target, then
   build a clean persistent Arch ARM root and model directory. This erases its
   previous contents and makes the existing Android installation unusable as-is.
   No formatting command is included here to prevent premature execution.
   Establish a reliable recovery/rescue entry first, and do not let normal
   Android boot or run recovery repair/reset operations after conversion;
   those paths are not validated to preserve a repurposed Linux filesystem.
5. Reconcile package ownership and dependency integrity. The RAM UI currently
   includes verified file-only payloads that are not all registered in its
   package database; blindly copying it is not sufficient installation proof.
6. First keep the working Alpine bootstrap and mount the persistent Arch root
   from it. Add supervised model/desktop startup with a native SSH/Weston
   fallback. Avoid a simultaneous PID1/kernel/storage migration.
7. Test file and model hashes, package operations, normal recovery reboots,
   missing-storage recovery, and startup with the host disconnected. Back up
   writable configuration; keep a separate rescue copy.

Existing data loss requires explicit authorization. This plan does not grant
it and the current scripts do not implement it.

## Nondestructive alternative: USB-C storage

A USB drive/SSD can hold the Linux root and models without repurposing internal
userdata. Use an already approved/identified filesystem or explicitly authorize
formatting that external device only. A powered OTG hub may be needed for
charging and input. The single USB-C port currently supplies USB-device-mode
Ethernet to the host; storage host mode cannot be assumed to preserve that
same connection. OTG storage, simultaneous charging and a replacement rescue
link must be tested before relying on this route. It is not yet verified here.

Host-backed storage or automatically restaging RAM can reduce manual work but
does not make the phone a standalone installation.

## Cold boot is a separate gate

For now, retain the verified recovery reboot target. Do not write a handcrafted
MISC/BCB record: the previous attempt failed Samsung integrity checking.

A future conventional Linux BOOT image could be investigated while preserving
RECOVERY as rescue, but that requires **separate explicit BOOT-write authority**,
correct boot/vendor_boot/DTB/header/size handling and a verified rollback.
Do not flash the 96 MiB recovery image into the 64 MiB BOOT partition. No
bootloader replacement or PIT change is proposed. Cold-power-on acceptance
requires an actual power-cycle test, not an `adb reboot` observation.

Persistence will not fix GPU/NPU drivers, Wi-Fi, calls or suspend. Those remain
independent acceptance items in [the driver/model report](DRIVER_MODELS_2026-09-20.md).
