# Persistent, standalone S22 Linux: decision and implementation plan

Status: **persistent desktop/model recovery autostart verified; normal BOOT
attempt unconfirmed**, 2026-09-20.
The owner explicitly accepted possible loss of Android data after the raw
backup completed. The exact userdata partition has now been formatted as
ext4 and successfully mounted; the partition table is unchanged. Private raw
capture is checksum-verified for 15 selected partitions, but readable personal
file recovery remains unverified. See the
[preparation record](PERSISTENCE_PREPARATION_2026-09-20.md) and
[migration record](PERSISTENCE_MIGRATION_2026-09-20.md).
BOOT was subsequently written/read back at approximately 07:48 UTC, but its
normal reboot did not return SSH, ADB, or Download USB by 07:56 UTC. RECOVERY
and vendor_boot remain unchanged. This does not establish a working normal
boot.

## Three separate requirements

1. **Keep files:** store Arch, configurations and model weights on nonvolatile
   storage instead of tmpfs.
2. **Start automatically:** the native bootstrap mounts that storage, starts
   the local model server and desktop, and offers a rescue path if either fails.
3. **Cold boot without a host/button combination:** the bootloader must select
   the correct Linux boot path after full power-off. Persistent files alone do
   not change Samsung's boot-target selection.

Alpine rescue remains CACHE-backed. Arch/Omarchy and the model now live on
userdata ext4 and started automatically in BORE519, without host restaging.
Requirements 1 and 2 passed in BORE519 recovery (15.81-second readiness and
77-second stable sample). Normal cold-power-on/Linux selection is unconfirmed
after the BOOT attempt.

## What the live phone provides

| Item | Observed state |
| --- | --- |
| CACHE-backed overlay | 582.6 MiB filesystem, rescue packages/configuration |
| Arch UI | Persistent `/srv/s22/arch`, runtime bind `/mnt/omarchy-trial` |
| Model/runtime | Persistent `/srv/s22/model-bench`, runtime bind `/mnt/model-bench` |
| userdata | 221,257,728 sectors × 512 bytes ≈ 105.5 GiB |
| userdata access | Converted ext4; UUID 1dd55c26-bd57-489a-9d9b-4c60e6f430eb; about100GiB free |
| Boot/recovery sizes | BOOT 64 MiB; RECOVERY 96 MiB; not interchangeable image targets |

Before conversion, the matching device-tree fstab configured F2FS, file-based encryption,
metadata encryption and wrapped keys for userdata. Combined with the live
probe, this is evidence that the existing Android data volume is **not a
ready-to-mount spare Linux disk**. It is not proof that the volume is empty.
Do not infer safe formatting from `blkid` returning no signature.

Android metadata encryption also protects filesystem metadata; decrypting it
depends on the Android key-management path. See the
[AOSP metadata-encryption documentation](https://source.android.com/docs/security/features/encryption/metadata).
A file-backed Linux image would only help if its underlying encrypted storage
could first be accessed reliably at native boot. That has not been established.

## Original migration plan and remaining tests

The owner accepted the loss risk and the existing userdata partition was
converted to ext4 without changing the partition table. **Do not format it
again.** The original sequence is preserved below for context, not as an
instruction to repeat completed destructive work.

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

The owner explicitly accepted raw-only recovery risk; the conversion and
recovery autostart are complete. Raw snapshots still do not establish readable file
recovery. The backup helpers themselves deliberately do not format, flash or
authorize erasure; authority came separately from the owner.

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

A conventional Linux BOOT image was authorized, written, and read back with
SHA256 `4aeb801486e35e2c71dab0e988c6ac14802b05a29d062ddd93834e74802b5b2e`.
The normal reboot produced no host connection before the bounded observation
ended, so boot mode/runtime remain unknown. Compression-boundary behavior is a
current suspicion without phone evidence. RECOVERY remains the last accepted
rescue path; do not claim normal-BOOT success or repeat a write yet.
Do not flash the 96 MiB recovery image into the 64 MiB BOOT partition. No
bootloader replacement or PIT change is proposed. Cold-power-on acceptance
requires an actual power-cycle test, not an `adb reboot` observation.

Persistence will not fix GPU/NPU drivers, Wi-Fi, calls or suspend. Those remain
independent acceptance items in [the driver/model report](DRIVER_MODELS_2026-09-20.md).
