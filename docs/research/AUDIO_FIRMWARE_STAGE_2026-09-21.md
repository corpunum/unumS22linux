# Audio firmware staging seam — 2026-09-21

This is a host-only preparation note for a future controlled boot. It does not
change production bootstrap, native autostart, kernel modules, or the phone.
The companion tools/hardware/audio-firmware-stage.py has an audit mode and an
explicit staging mode. Staging writes only new files under one userdata
directory and absent files under the guardian root's /vendor/firmware; it
does not write power/control, bind/unbind, open ALSA/CP devices, reboot, or
read a partition.

## Exact ABOX payload

The four files are the only payload accepted by the stager:

| name | host source | bytes | SHA-256 |
| --- | --- | ---: | --- |
| calliope_sram.bin | rootfs/bt-audio-vendor-assets/vendor/firmware/ | 165120 | 786ae058d4c439c01edf856abb52184a92e8d25e90b1b748e380186bed84796d |
| calliope_dram.bin | same | 2204800 | d2460b85f1e8ceabe6a23e103688932c03ca5aca6da0de5ed95ff78dfd8feccd |
| abox_tplg.bin | same | 1066664 | 3690d21b6a7e91242ee423ea655604842969607c318769fe73fbc6db826eb9da |
| abox_tplg.conf | same | 109315 | ddc3874ec797a085c8497a240d47b995fedbcc5c876483c94e2f236ecb79f782 |

The fixed new userdata target is
/srv/s22/audio-reuse-20260921/audio-firmware-stage-20260921. The guardian
target is /proc/1/root/vendor/firmware. Both target classes are checked
nonexistent before any stage write; existing files, symlinks, wrong hashes,
wrong sizes, and wrong permissions fail closed. Files are written 0644
root:root with fsync, and every resulting hash is checked remotely.

sectiongraph_tplg.bin and all other recovered ABOX extras are intentionally
not staged. The pinned topology source marks abox_tplg.bin required and
sectiongraph_tplg.bin optional; the four-file request is the bounded
source-matched payload already accepted by the Calliope 6XH0 trial.

## Correct startup seam

The pinned kernel uses firmware_class.path=/vendor/firmware. ABOX core
requests its DT-declared SRAM/DRAM names through the normal firmware loader;
the topology component requests abox_tplg.bin. The clean ordering for a
future controlled boot is therefore:

1. Verify the local four-file manifest and the target kernel/guardian identity.
2. Create the new userdata staging directory and copy the four files into the
   guardian-visible /vendor/firmware before ABOX/audio module or platform
   probing.
3. Let the existing kernel probe order run unchanged.
4. Observe Calliope version, runtime state, ASoC cards, and /proc/asound/pcm.
5. Keep the current boot's power/control experiment separate; the stager
   never triggers it.

The prior bounded trial staged the files after the drivers were already bound
and then wrote power/control=on; it proved Calliope firmware boot (6XH0)
and 34 dump/debug PCMs, but did not produce a Rainbow machine card. Because
the pinned remove paths leave uncancelled work and stale topology IPC/dump
registrations, dynamic unbind/rebind is not the next seam. A clean controlled
boot/session with firmware visible before probe is required.

## Recovered CP userspace metadata

The existing read-only F2FS vendor image is
rootfs/vendor-pristine-20260920.img, SHA-256
6dfe677119792e95a37b016092c327ad62bc0dc7759851424ad05e6bcb31b206.
The retained metadata index is /tmp/vendor-tree.txt; no CP file bytes were
recovered in this preparation. Its vendor-tree metadata does contain the
following exact names for a future read-only, individually guarded recovery:

Core boot/RIL executables and init fragments:

- /vendor/bin/cbd (tree inode 0x2e)
- /vendor/bin/hw/rild (tree inode 0x11f)
- /vendor/bin/secril_config_svc (tree inode 0xb5)
- /vendor/etc/init/vendor.samsung.rild.rc (tree inode 0x260)
- /vendor/etc/init/vendor.samsung.rilchip.slsi.rc (tree inode 0x25f)

Small native-library closure candidates:

- /vendor/lib64/libsec-ril.so (tree inode 0x84b)
- /vendor/lib64/libsecril-client.so (tree inode 0x854)
- /vendor/lib64/libsec_semRil.so (tree inode 0x84d)
- /vendor/lib64/libshmemcompat.so (tree inode 0x85f)
- /vendor/lib64/libshmemutil.so (tree inode 0x860)

Radio interface names present in the same metadata include
android.hardware.radio@1.0.so through @1.6.so,
android.hardware.radio.config@1.0.so through @1.3.so,
vendor.samsung.hardware.radio@2.0.so through @2.2.so,
vendor.samsung.hardware.radio.bridge@2.0.so/@2.1.so, and
vendor.samsung.hardware.radio.channel@2.0.so.

These are metadata candidates, not a validated closure. Recovery must remain
file-by-file, read-only, hash-recorded, and separate from CP activation. The
native guardian currently has no cbd, rild, RIL libraries, or matching init
fragments. No CP boot or userspace service start is part of this task.

## Host validation

Run without phone access:

    python3 tools/hardware/test_audio_firmware_stage.py
    python3 tools/hardware/audio-firmware-stage.py --help

The default stager audit is read-only against the phone. --stage is the only
mode that performs the narrowly scoped file staging and does not start audio.
