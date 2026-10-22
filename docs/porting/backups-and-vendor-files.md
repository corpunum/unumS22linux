# Backups and vendor files

Part of the [porting guide](../PORTING.md). How we backed up the phone, and where in the stock
image the "drivers" for each piece of hardware actually live.

## Raw partition backups

We backed up partitions from our own native Linux (Android services stopped), not from Android.
The helper [`tools/persistence/backup-phone.py`](../../tools/persistence/backup-phone.py) runs on
the host and streams each partition over SSH. What it does, and what yours should do too
([tools/persistence/README.md](../../tools/persistence/README.md)):

- checks native PID 1, the exact partition names, sizes, device numbers, and that nothing holds
  or mounts the partition;
- opens block devices **read-only**;
- writes into a fresh mode-0700 directory on the host, never into the repo;
- verifies every image three ways (SHA256 on the phone while reading, on the incoming stream, and
  again from the saved file), then makes it read-only and writes a `.verified.json`;
- leaves interrupted copies as `.partial`, never counts them as done.

Fifteen partitions were captured and checksum-verified ([PERSISTENCE.md](../PERSISTENCE.md)):
`metadata`, `keystorage`, `keyrefuge`, `efs`, `sec_efs`, `boot`, `vendor_boot`, `recovery`,
`dtbo`, `vbmeta`, `vbmeta_system`, `userdata`, `super` (holds the vendor partition), `prism` and
`optics` (the `ORDER` list in [`backup-phone.py`](../../tools/persistence/backup-phone.py)). EFS and
the key partitions are **read** once into the private backup so that a copy exists; they are never
written. CACHE was excluded because the running Linux overlay writes there.

Two honest limits:

- **Raw userdata is ciphertext.** A matching hash proves the copy is intact, not that the files
  can ever be decrypted again. Our receipts say so explicitly
  (`"file_restore_verified": false, "permits_erasure": false`). Erasing userdata was a separate,
  explicit owner decision ([tools/persistence/README.md](../../tools/persistence/README.md#critical-limitation)).
- **These backups are private.** They contain device identity and keys. Never publish them
  ([PUBLICATION.md](../PUBLICATION.md)).

Also keep the **stock firmware for your current bootloader version** from the official update
server, with hashes ([FLASH_LOG.md](../../FLASH_LOG.md)). It is your clean source for vendor
files and your way back.

## Getting files out of the vendor partition

On this phone vendor is a logical partition inside `super`, formatted as **compressed F2FS**. We
read the super metadata read-only, checked its header and table hashes, and copied only the
vendor extents to a host image ([EXPERIMENTS.md](../../EXPERIMENTS.md), "Read super metadata
O_RDONLY…"; [`tools/npu-probe/read-lp-metadata.py`](../../tools/npu-probe/read-lp-metadata.py)).

**Trap:** the first extraction tools silently produced **zero-filled files** for compressed
assets. That looks like "firmware present" but is garbage. We wrote a cluster-aware LZ4 recovery
for the layout we saw, and later re-extracted with f2fs-tools 1.16 and confirmed the ABOX
firmware was byte-identical
([tools/hardware/README.md](../../tools/hardware/README.md),
[evidence/audio-20261021](../../evidence/audio-20261021/README.md)). Check extracted files by hash
or by content, never by "the size is not zero".

Mount stock images read-only on the host (`losetup -r`, `mount -o ro,norecovery`), copy what you
need, and unmount ([evidence/gnss-20261022](../../evidence/gnss-20261022/README.md)). Keep a
pristine, read-only copy of the vendor image; we once found our working copy differed in one
block and had to rebuild it from the backup ([EXPERIMENTS.md](../../EXPERIMENTS.md), "Provenance
correction").

## What to look for, and where

| You want | Look in | What it told us |
|---|---|---|
| Kernel modules and their order | Recovery/vendor_boot ramdisk `lib/modules`, `modules.load`, `modules.dep`; Lineage `configs/kernel/modules/*.load` | ~324 modules in strict order; dependencies such as the watchdog's ([RECOVERY_COMPARISON.md](../RECOVERY_COMPARISON.md), [EXPERIMENTS.md](../../EXPERIMENTS.md)) |
| Extra modules not in the ramdisk | `vendor_dlkm` in the LineageOS build | The Wi-Fi `wlan.ko` matching the running kernel's vermagic; the stock one is ABI-incompatible ([WIFI_NATIVE.md](../WIFI_NATIVE.md)) |
| Firmware | `/vendor/firmware`, firmware names in the DT and driver source | Which audio, amp, camera-OIS, NPU and Wi-Fi/BT files the drivers ask for and when ([evidence/audio-buttons-20261006](../../evidence/audio-buttons-20261006/README.md), [BT_AUDIO_NATIVE.md](../BT_AUDIO_NATIVE.md)) |
| How Android starts a service | `/vendor/etc/init/*.rc`, `ueventd*.rc` | Start order and deadlines (Wi-Fi `fs_ready`), arguments (`cbd`), users and **device permissions** (`/dev/umts*` for the radio user) ([WIFI_NATIVE.md](../WIFI_NATIVE.md#official-init-ordering), [evidence/modem-20261006](../../evidence/modem-20261006/README.md), [GNSS.md](../../tools/hardware/gnss/GNSS.md#22-stock-userspace-verified-from-the-fyi3-vendor-image)) |
| Hardware "glue" logic | HAL `.so` files in `/vendor/lib64/hw`, `/vendor/lib64` | Which PCM devices audio really uses ([audio chapter](audio.md)); how GNSS talks to its engine ([GNSS.md](../../tools/hardware/gnss/GNSS.md)) |
| Mixer and route defaults | `mixer_paths.xml`, `mixer_gains.xml` | The controls Android sets before the first sound ([evidence/audio-buttons-20261006](../../evidence/audio-buttons-20261006/README.md)) |
| Tuning data | Camera setfiles, `gps.cfg` | The camera colour matrix ([CAMERA.md](../../tools/hardware/camera/CAMERA.md#software-isp-s22-develop-2026-10-22)); GNSS options to turn off ([GNSS.md](../../tools/hardware/gnss/GNSS.md)) |
| Kernel behaviour | The matching kernel source (Lineage's kernel repo at the exact commit) and the *embedded* config | ioctl tables, device-tree nodes, what a sysfs knob does ([evidence/gnss-20261022](../../evidence/gnss-20261022/README.md), [KEXEC_FEASIBILITY.md](../KEXEC_FEASIBILITY.md)) |

## Reading binaries without running them

Most answers came from reading, not running. Tools used on the host: `readelf` (NEEDED libraries,
interpreter), `strings`, `llvm-objdump` disassembly, and comparison with the matching kernel
source ([evidence/gnss-20261022](../../evidence/gnss-20261022/README.md)). Examples of what a few
instructions told us:

- `cbd` drops to the radio user by itself, and its "NV validation TIMEOUT" is a harmless 5-second
  wait for an Android property ([evidence/modem-20261006](../../evidence/modem-20261006/README.md));
- gpsd has a path that resets the whole phone, which we then blocked
  ([GNSS.md](../../tools/hardware/gnss/GNSS.md#23-gpsd-paths-that-reach-outside-gnss-verified-by-disassembly));
- the stock audio library's PCM device numbers revealed the real playback path
  ([evidence/audio-20261021](../../evidence/audio-20261021/README.md)).

When someone else (or their agent) needs something from a vendor file, don't send the file. Write a
small script that takes the path as an argument and prints only the needed facts: symbols,
offsets, hashes ([CONTRIBUTING.md](../../CONTRIBUTING.md#1-read-the-evidence-first)).

## The isolated Bionic runtime

Vendor userspace is built for Android's C library (Bionic) and linker. Instead of running Android,
we built small private roots on userdata that contain only the Bionic linker and libraries plus
the exact closure of vendor libraries one program needs:

- `/srv/s22/android-rt` for the modem boot daemon `cbd` and as the base for GNSS
  ([evidence/modem-20261006](../../evidence/modem-20261006/README.md), [GNSS.md](../../tools/hardware/gnss/GNSS.md));
- the GPU roots built by [`tools/gpu-compat/`](../../tools/gpu-compat/README.md), seeded from the
  recovery's Bionic libraries plus the recorded vendor closure;
- `/srv/s22/enn-rt` for the NPU ([evidence/npu-20261007](../../evidence/npu-20261007/README.md)).

Libraries a program links against but never really uses got fail-fast stubs: a stub that is
called aborts loudly; none pretends to succeed
([tools/gpu-compat/README.md](../../tools/gpu-compat/README.md#reconstruction-and-boundaries),
[GNSS.md](../../tools/hardware/gnss/GNSS.md)). Our own Bionic programs were built with Android NDK
r27c ([evidence/npu-20261007](../../evidence/npu-20261007/README.md)).
