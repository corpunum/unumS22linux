# Independent review: stripped audio recovery artifact — 2026-10-03

## Verdict

**PASS, limited to the exact private host artifact’s byte integrity, package
scope, and static ELF/MODVERSIONS compatibility.** The image, manifest, AVB
footer/hash, unpacked header and payloads, decoded CPIO, three replacements,
and static ABI totals were rechecked against the pinned inputs. I found no
artifact-integrity or out-of-scope CPIO change.

The image is present as a local private build output. “Not published” in the
author receipt means the raw logs/artifact were not externally published or
deployed; it does not mean the local output was absent. No packager or build
was invoked for this review. This verdict grants no Samsung authentication,
bootability, module-loader, runtime audio, device, deployment, or physical
acceptance.

## Frozen review context and provenance

- Review worktree: `/home/corpunum/s22-workers/audio-coherent-artifact-review-20261003`,
  clean at commit `9db7e0c8ef0335a1245a6e1698237a526da514d2`, tree
  `d9c3449e10f0a0c09e315aa282a62fa92025c30f`. It contains the frozen package
  receipt imported from author receipt commit
  `e1dbecafbd376f836038faaa1c955df12d77b9c1` (review integration commit
  `9db7e0c`).
- Package author source: commit `43f5996f81e4c78dfed36799e8cfdaa9f159e40b`,
  tree `d650cde5d54af4ae70233e4c05c336dc9ba40311`; the builder hash in that
  receipt is also the hash of the review worktree's
  `tools/hardware/build-audio-coherent-recovery.py`:
  `3b7e031974fb60930629d14dff2df62c01aea27c89383f3240cfb4921fa4e462`.
- Explicit worker selection: `gpt-6-luna`, reasoning `max`, per parent
  assignment; this is configuration evidence, not backend attestation.
- The sanitized author receipt records launch ID
  `201c9d90-71a0-48a9-a8d5-46edea60004f`, PID `2495256`, one invocation,
  exit 0, and no retry. Its exact command was:

  ```text
  /usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C TMPDIR=/tmp /usr/bin/python3 -I -S -B /home/corpunum/s22-workers/audio-stripped-package-build-20261003/tools/hardware/build-audio-coherent-recovery.py --out-dir /home/corpunum/s22-linux/builds/audio-coherent-recovery-stripped-host-20261003
  ```

- The separate private phase JSON/raw logs were not in the supplied review
  worktree or artifact directory. I therefore corroborate the artifact against
  the committed sanitized receipt and its exact hashes, but do not claim an
  independent read of private process logs.
- The earlier oversized-image failure remains separately recorded in commit
  `1edf236f266433e42a3d2e56500dcbafa25c9d78`; this review did not replace or
  replay it.

## Artifact identity and image verification

Private output directory:
`/home/corpunum/s22-linux/builds/audio-coherent-recovery-stripped-host-20261003`
(directory mode `0700`; image and manifest mode `0600`).

| Item | Bytes | SHA-256 |
|---|---:|---|
| Recovery image | 100,663,296 | `6b788b23f54b9b8e84212187544064949b72af20c167cbb02392cbe53ee5a6ab` |
| Package manifest | 26,524 | `f78bbaddf94ef6d1ae37b059b7455b24a15cbb7c2fab1ec9bbc4e1df5fd1edf8` |
| Candidate CPIO | 128,426,316 | `5dd1303047bd684a212c9838663e45260a527d2a766787a3b90c5b59c9751de4` |
| Compressed ramdisk | 63,105,814 | `6049d39ef900f036c6eb07a9dc1a434770cff3fcab9619f49ef5b2502f7fceb5` |

The preserved base is
`builds/camera-module-recovery-20260927/recovery.img`, 100,663,296 bytes,
SHA-256 `b10412715756da3cc8ee221368b49f179cc0c64ab7bd2802976480905e6d8d2f`.
Both base and candidate pass the pinned `avbtool.py verify_image`; both use
algorithm `NONE`, partition name `recovery`, rollback index 0, and the pinned
recovery fingerprint/salt. The pinned `avbtool.py info_image` reports a
98,252,800-byte original-image payload for the base and 98,222,080 bytes for
the candidate. The pinned `add_hash_footer --calc_max_image_size` reports a
100,593,664-byte maximum payload for this 100,663,296-byte partition, so the
candidate is within the configured size limit. The footer/hash check is not a
signature or Samsung-authentication check.

The pinned Android boot-image unpacker reports header v2 for both images.
Every address below was checked in the actual unpacked images; the load
addresses and non-derived header values are unchanged:

| Header field | Base | Candidate |
|---|---:|---:|
| Kernel size | 32,532,992 | 32,532,992 |
| Kernel load | `0x10008000` | `0x10008000` |
| Ramdisk size | 63,136,931 | 63,105,814 |
| Ramdisk load | `0x11000000` | `0x11000000` |
| Second size/load | `0` / `0x00000000` | `0` / `0x00000000` |
| Tags load | `0x10000100` | `0x10000100` |
| Page size | 2,048 | 2,048 |
| Recovery-DTBO size | 2,207,744 | 2,207,744 |
| Recovery-DTBO offset | `0x05b3e000` | `0x05b36800` |
| DTB size/load | 370,688 / `0x11f00000` | 370,688 / `0x11f00000` |
| Image ID | `b984110510ccfbd1dfad21b1bbc0bb79fc7204b8000000000000000000000000` | `d6676d13bb2c07d303f8b288273a4d2673aeb300000000000000000000000000` |

The only changed header values are the expected derived `ramdisk_size`,
`recovery_dtbo_offset`, and `image_id`. The unpacked kernel, DTB, and
recovery-DTBO match the base byte-for-byte, with SHA-256s respectively
`7738564db77e4a6183ffaa875fc12f8168a58e8135a3bdc86e27b127b47fc05c`,
`f5a00d80dd1800c934c6baed4ff4f5b7ac0bdc19acbc52a8e6092f03e5f7b2b8`, and
`dd2acb7f8e02a58bba6b9ee9da09790286ab19c887ef7feabc8a8be10e0e8f98`.

## CPIO scope and module replacement

I decoded both base and candidate ramdisks with the pinned `lz4` and parsed
both CPIO streams with the pinned newc parser. The base CPIO is 128,603,884
bytes, SHA-256
`f280aa4c1bee4545a281602d2f9f3551decfac47b7285c4fa5f9166ec85747ee`; both
archives contain 963 unique records in the same order. Only the following
three raw records differ:

| CPIO record | Base bytes / SHA-256 | Candidate bytes / SHA-256 |
|---|---|---|
| `lib/modules/snd-soc-samsung-abox.ko` | 1,909,256 / `7d61a65a617e1e0c500b7aa437a21d0cdb9a583d2d1277b5c8a42e2392028975` | 1,735,880 / `61d846d2bb13d5ff48261f21efadcd8b378bdc586c28b3e288ddccf145488265` |
| `lib/modules/rainbow_prince.ko` | 70,400 / `7cca3e04a7a414b755a9f8ca01abfb5d3d78d490b2ef2f405be480e2d9f506eb` | 70,792 / `b896200b333be6d518b9eb4b218abefe8c115a3162e5fb3b1a7016a6b7175c7a` |
| `lib/modules/exynos-usb-audio-offloading.ko` | 47,288 / `ca62486424aa41961242812e93340b2a4b9d8e8265cfcc1f42b4ce85e2c48466` | 42,704 / `3b732ada44c0a6812b6aaeb38d7de8757ad54e7f843c6761b97ff4e5d63e8392` |

All target inode, mode, owner/group, link, and timestamp fields are preserved;
only newc payload-size/check fields vary as required by the replacement. The
other 960 records are byte-identical, including `modules.dep`, `modules.alias`,
`modules.softdep`, camera `fimc-is.ko` (SHA-256
`256926d8a1499d9fd8fcef22fc9ad677998decb1317b2037045c8dcc44b35ecf`), the
`init`/`system/bin/init` bootstrap records, and all 33 record names containing
`firmware`. The candidate's extracted CPIO equals the separately saved CPIO
artifact by full SHA-256. Each packaged `.ko` file in the private output
directory is byte-identical to its CPIO payload.

## Source-to-package ELF and static ABI checks

For each replacement, I compared the actual packed module with its pinned
unstripped source (source hashes: ABOX5
`55bae9f12135a2134337d7d520ddfadc85cdd049cedefd2a3c41f871fdd329bf`, Rainbow
Prince `6461073beee9e1fdc4f7c92b250bbb773a18cbd766e0c9331e77ec01e5e45170`,
USB offloader
`92116d85c21c9c3969e00746fb0299a5cb7725edfe9c344d5645417686416ec2`). The
expected packaged hashes and GNU build IDs match the extracted files. Using
the pinned ELF debug-strip validator on these actual bytes, plus fresh
`modinfo` and `modprobe --dump-modversions` comparisons, all three pass:

- only the pinned debug sections were removed; no sections were added;
- non-debug section order/metadata, all allocated-section bytes, runtime
  symbol inventories, and runtime relocation semantics match the source;
- `.modinfo`, `__versions`, export CRC sections, imported CRC records, and GNU
  build IDs are unchanged; modinfo fields and imported-version rows match.

The pinned `llvm-objcopy-18` SHA-256 is
`f52b9997b3c5019b4b3043e12b1ae2e821df67996ca344921c234c89c4d23e34`. This is
static ELF evidence; no module was loaded.

I rebuilt the provider map from the actual native-eight `Module.symvers` and
the two candidate `modules-only.symvers` files. The canonical map has 17,283
rows, SHA-256
`0c8e481225fe3ab071ba9be1d14faae06ce8a556fa7e89b07f3dcefb761bd470`, and
zero symbols with distinct CRC providers. A fresh run of the pinned static
preflight over the candidate CPIO and the selected external WLAN found:

- **324** ramdisk modules and **16,569** imported version records checked;
  zero missing symbols, CRC mismatches, unknown/ambiguous CRCs, missing
  versions sections, `module_layout` failures, or vermagic mismatches;
- the pinned vermagic is exact for every module and `module_layout` CRC is
  `0x0e3c515c`. The exact vermagic is
  `5.10.260-g4e5c5ad7d950 SMP preempt mod_unload modversions aarch64`;
- the external WLAN is not in the CPIO; its pinned ELF identity is `wlan`, and
  all **495** of its imports pass the same static checks.

These were fresh host-static inspections with pinned `modinfo`, `modprobe`,
`readelf`, and preflight source. They do not invoke the kernel module loader.
The CPIO's 324 modules remain distinct from the separately recorded
325-module live inventory; this review did not access or recheck a device.

## Exact checks and remaining limits

Read-only review commands included:

```text
sha256sum <private output recovery.img, manifest.json, ramdisk.cpio, ramdisk.lz4, and three module files>
/usr/bin/python3 -I -S -B /home/corpunum/s22-linux/tools/avb/avbtool.py info_image --image <base-or-candidate image>
/usr/bin/python3 -I -S -B /home/corpunum/s22-linux/tools/avb/avbtool.py verify_image --image <base-or-candidate image>
/usr/bin/python3 -I -S -B /home/corpunum/s22-linux/tools/avb/avbtool.py add_hash_footer --partition_size 100663296 --calc_max_image_size
/usr/bin/python3 -I -S -B /home/corpunum/s22-linux/tools/mkbootimg/unpack_bootimg.py --boot_img <base-or-candidate image> --out <temporary review directory>
/usr/bin/lz4 -d <temporary unpacked ramdisk> <temporary decoded CPIO>
```

The CPIO and ELF/ABI checks were also rerun with `/usr/bin/python3 -I -S -B`
against the actual extracted artifacts and pinned source helpers; no
`build_candidate`, `mkbootimg`, footer-writing, output-publication, or source
compile path was called. The helper and binary hashes are recorded in the
machine-readable evidence. The CPIO check called the pinned helper's
`parse_cpio` for base and candidate and compared record names/order/raw bytes
and metadata. The ELF check called the pinned builder validator's
`validate_debug_strip_transform` on each source/candidate pair and compared
fresh `modinfo` and `modprobe --dump-modversions` results. The ABI check
rebuilt the provider map with `build_updated_provider_map` and ran
`inspect_static_abi` on the actual candidate CPIO plus the pinned external
WLAN file.

No phone, SSH, ADB, firmware capture, module install/load, boot, reboot, or
deployment occurred. The private local package output exists, but there was
no external publication. The following remain unproven: AVB signature or
Samsung authentication; bootability; compatibility with current live module
membership; runtime PM/IPC/DMA/PCM behavior; firmware delivery; physical audio;
and all device acceptance. The audio source findings about stalled DMA remain
separate from this package-artifact review.
