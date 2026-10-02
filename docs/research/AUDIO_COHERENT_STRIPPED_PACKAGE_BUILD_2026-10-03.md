# Stripped audio recovery package build — 2026-10-03

## Result

The one coordinator-authorized host-only packaging invocation completed with
exit code 0. It produced a 100,663,296-byte Android v2 RECOVERY image whose
pinned AVB footer/hash verification passed. The image's only CPIO payload
changes are the three pinned audio modules, now using their separately
verified `llvm-objcopy-18 --strip-debug` derivatives. The original
unstripped module artifacts and the earlier oversized package failure remain
unchanged.

The output image and its module/ramdisk files remain private host build
artifacts. Only this sanitized receipt and its JSON evidence are committed;
no image, ramdisk, CPIO, module bytes, or private execution logs are included.
This is not Samsung authentication, bootability, deployment authorization,
module-loader evidence, or runtime/physical-audio acceptance.

## Source, inputs, and execution

- Execution worktree: clean before the invocation, commit
  `43f5996f81e4c78dfed36799e8cfdaa9f159e40b`, tree
  `d650cde5d54af4ae70233e4c05c336dc9ba40311`.
- Builder SHA-256:
  `3b7e031974fb60930629d14dff2df62c01aea27c89383f3240cfb4921fa4e462`.
- Debug-strip implementation commit: `7a8ffd3d78179e80006247520080c3f981e1e27e`.
- Independent debug-strip review commit: `e18871d82961d83210ddc57039de53cfbe4ef066`.
- Exact command:

  ```text
  /usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C TMPDIR=/tmp /usr/bin/python3 -I -S -B /home/corpunum/s22-workers/audio-stripped-package-build-20261003/tools/hardware/build-audio-coherent-recovery.py --out-dir /home/corpunum/s22-linux/builds/audio-coherent-recovery-stripped-host-20261003
  ```

- Launch ID: `201c9d90-71a0-48a9-a8d5-46edea60004f`; PID `2495256`.
- Start/end UTC: `2026-10-02T23:46:52.815156+00:00` /
  `2026-10-02T23:47:05.965293+00:00`; persisted monotonic duration: `13.281`
  seconds; exit `0`.
- The requested output path was absent at the immediate prelaunch check.
  MemAvailable was `31,449,528 KiB`; the build filesystem had
  `29,933,694,976` bytes free.
- Pinned input verification succeeded for the preserved base image, all three
  original unstripped module inputs, base and replacement `Module.symvers`,
  external WLAN, and the four project helpers. The final 17,283-row provider
  map hash is
  `0c8e481225fe3ab071ba9be1d14faae06ce8a556fa7e89b07f3dcefb761bd470`.
- Raw stdout/stderr and phase JSON remain on the host in a private directory;
  only byte counts and SHA-256 digests are recorded in the JSON receipt.
  Stderr was empty. The invocation was not retried.

The exact `llvm-objcopy-18` binary was
`/usr/lib/llvm-18/bin/llvm-objcopy`, SHA-256
`f52b9997b3c5019b4b3043e12b1ae2e821df67996ca344921c234c89c4d23e34`, invoked
through `/usr/bin/llvm-objcopy-18`. The manifest separately records original
source and packaged derivative identities; the original module files were
rechecked unchanged.

## Private output identities and verification

| Output | Bytes | SHA-256 |
|---|---:|---|
| Recovery image | 100,663,296 | `6b788b23f54b9b8e84212187544064949b72af20c167cbb02392cbe53ee5a6ab` |
| Package manifest | 26,524 | `f78bbaddf94ef6d1ae37b059b7455b24a15cbb7c2fab1ec9bbc4e1df5fd1edf8` |
| Candidate CPIO | 128,426,316 | `5dd1303047bd684a212c9838663e45260a527d2a766787a3b90c5b59c9751de4` |
| Compressed ramdisk | 63,105,814 | `6049d39ef900f036c6eb07a9dc1a434770cff3fcab9619f49ef5b2502f7fceb5` |
| Stripped ABOX5 | 1,735,880 | `61d846d2bb13d5ff48261f21efadcd8b378bdc586c28b3e288ddccf145488265` |
| Stripped Rainbow Prince | 70,792 | `b896200b333be6d518b9eb4b218abefe8c115a3162e5fb3b1a7016a6b7175c7a` |
| Stripped USB offloader | 42,704 | `3b732ada44c0a6812b6aaeb38d7de8757ad54e7f843c6761b97ff4e5d63e8392` |

The source-to-packaged module hashes and GNU build IDs are preserved in the
JSON receipt. Each packaged hash matches the independently checked strip
derivative; the build IDs remain unchanged metadata and are not used as a
substitute for full-file hashes.

The pinned AVB verifier passed both the base-image and candidate-image
checks. The candidate is exactly the 100,663,296-byte partition size; AVB
uses algorithm `NONE`, so this verifies the recorded hash footer, not
Samsung signing/authentication. Independent post-run verification rehashed
the image and manifest, reran the pinned AVB verifier, unpacked the candidate,
and decoded its ramdisk. The Android header remains version 2. Only the
derived `ramdisk_size`, `recovery_dtbo_offset`, and `image_id` header fields
differ; kernel, DTB, and recovery-DTBO payload hashes are byte-identical to
the preserved base.

The final 963-record CPIO differs only at:

- `lib/modules/snd-soc-samsung-abox.ko`
- `lib/modules/rainbow_prince.ko`
- `lib/modules/exynos-usb-audio-offloading.ko`

Their target metadata (except the expected new file-size fields), record
order, and all non-target raw CPIO records are preserved. The
`modules.dep`, `modules.alias`, and `modules.softdep` bytes remain unchanged.
The image's unpacked ramdisk and the separately saved candidate CPIO match by
full SHA-256.

## Static ABI result and limits

The package-time scan and an independent post-run scan checked all 324
ramdisk modules and 16,569 imported version records, plus the selected
external WLAN's 495 imports. Both scans found zero missing, mismatched,
unknown, or ambiguous imports; all module vermagic/version records and
`module_layout` CRC `0x0e3c515c` matched the pinned provider map. The WLAN is
not included in the ramdisk. The scan is static host ELF/MODVERSIONS evidence
only and does not run the kernel loader.

The first package attempt's AVB size failure remains preserved in commit
`1edf236f266433e42a3d2e56500dcbafa25c9d78`; it was not overwritten or
replayed. The successful result follows the separately reviewed, exactly
pinned debug-strip transform. No kernel build, phone/SSH/ADB, firmware,
module install/load, publication, deployment, flash, boot, reboot, or NPU
operation occurred. The 324-module CPIO set remains distinct from the
current-phone 325-module runtime inventory. Runtime PM/IPC/DMA/PCM progress,
physical audio, Samsung authentication, and bootability remain unestablished.
