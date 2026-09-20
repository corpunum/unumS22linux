# Native BOOT experiment — not accepted

The owner separately authorized BOOT writes while preserving RECOVERY rescue.
This directory is an experimental, phone-specific toolset, not an installer
for other S22s. The working persistent Linux session was tested from RECOVERY
at BORE519. Normal BOOT has not passed acceptance.

## Installed experimental image

`builds/native-boot-v2/boot.img` (local only), SHA256:
`4aeb801486e35e2c71dab0e988c6ac14802b05a29d062ddd93834e74802b5b2e`.

The complete 64 MiB BOOT write/read-back passed; RECOVERY and vendor_boot hashes
were unchanged. The normal reboot at2026-09-20 07:48:18UTC did not restore
USB SSH, ADB or Download detection during the subsequent observation interval.
No post-test BORE or kernel log has been obtained. Do not call this a successful
boot or reflash this candidate.

`build_boot.py` preserves the V3 kernel, all324 modules and their metadata,
the first-stage `/init` and recovery marker ELF. It changes wrapper/guardian
to restart into RECOVERY on native startup failure, and moves only the Alpine
lower archive out of the CPIO to ordinary CACHE storage. The gzip generic
ramdisk fits64MiB, but its concatenation with the existing legacy-LZ4 vendor
ramdisk was a missing validation gate: gzip support alone is not sufficient.
This defect exists before the guardian can provide its intended fallback.
Host parser evidence is not a substitute for the missing phone boot log.

The AOSP [vendor-boot contract](https://source.android.com/docs/core/architecture/partitions/vendor-boot-partitions)
requires generic ramdisk last, with matching compression. The exact kernel's
`lib/decompress_unlz4.c` handles another LZ4 magic or zero terminator but does
not detect a directly appended gzip stream. Subsequent candidates must validate
the whole concatenated stream, not only individual images. Prefer consistent
LZ4 compression and a measured minimal ELF dependency closure.

## Sources and boundaries

- `build-native-start-boot-variant.py`: derives startup from the pinned V3
  script; hashes `/cache/s22-linux/lower-rootfs.tar.xz` before extraction.
- `build_boot.py`: historical host-only gzip-v2 reproducer, not accepted.
- `install-boot-on-phone.py`: default read-only validation; `--write` was used
  once to write exact BOOT8:14 after checking original hash, geometry, inactive
  state, native RECOVERY PID1, staged image and rescue hashes. It does not
  reboot, format, change the partition table or write vendor_boot/RECOVERY.
  It refuses an already changed BOOT, including the currently installed v2.
- `analysis/lineage-first-stage-pinned.txt`: pinned first-stage source facts.

The original BOOT and native RECOVERY raw images are preserved in the private
backup outside this repository. Public records contain hashes and test facts,
not firmware images, model weights, private keys or personal-data backups.
Physical entry to unchanged RECOVERY is needed if no USB endpoint returns.
Once connected, immediately read bounded BORE/pstore evidence before another
test; do not format userdata or run recovery factory-reset/repair operations.

## Next candidate: built on host only, not flashed

`build_lz4_boot.py` produced `builds/native-boot-v3-lz4/boot.img`:

- SHA256 `b0481f8888c6d54d8e8e4d885a8f991462d69962d837a9b55b595e9bbf270138`.
- 67,108,864 bytes with BOOT AVB footer; raw image58,744,832 bytes.
- Legacy LZ4 ramdisk25,222,162 bytes, same format as the actual vendor fragments.
- All324 V3 modules and four metadata files remain unchanged. The34-object
  recursive ELF dependency closure of init/linker/toybox/sh is retained.
- Removed62 unused system ELF files and15 now-dangling optional-tool links;
  `/system/bin/recovery` remains as a zero-byte regular first-stage marker.
- Exact phone vendor fragments plus new generic ramdisk decode to all three
  expected CPIO streams. The merged view verifies all328 module files, original
  first-stage init and the recovery marker. AVB internal hash check passes.

These are host checks only. Module loading, native startup, bootloader
acceptance, touchscreen and ordinary power-on still need a real device test.
The first installer deliberately refuses to overwrite the now-changed BOOT;
do not weaken that check without fresh identity/hash and rescue evidence.
