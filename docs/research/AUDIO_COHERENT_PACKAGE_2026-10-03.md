# ABOX5 coherent recovery package builder — 2026-10-03

## State

The narrow host-only packager and its synthetic regression tests are
implemented. The packager has **not** been run against the retained modules to
create an image. No candidate output directory, candidate CPIO, candidate
ramdisk, or candidate recovery image was created by this work. An independent
code review and coordinator GO are required before the first real packaging
run.

This work adds only `tools/hardware/build-audio-coherent-recovery.py`, its
focused test, and this note. It does not authorize phone, SSH, ADB, firmware,
module loading, deployment, flashing, or boot activity. Authoring selection is
inherited as gpt-6-luna/max; this is not a backend-attestation claim.

## Exact inputs

The base is the retained camera-specific RECOVERY image
`builds/camera-module-recovery-20260927/recovery.img`: 100,663,296 bytes,
SHA-256
`b10412715756da3cc8ee221368b49f179cc0c64ab7bd2802976480905e6d8d2f`. A
read-only unpack and trusted public `avbtool` verification confirmed Android
header v2, the 100,663,296-byte partition, and the pinned non-ramdisk payloads:

| Payload | SHA-256 |
|---|---|
| kernel | `7738564db77e4a6183ffaa875fc12f8168a58e8135a3bdc86e27b127b47fc05c` |
| ramdisk CPIO | `f280aa4c1bee4545a281602d2f9f3551decfac47b7285c4fa5f9166ec85747ee` |
| DTB | `f5a00d80dd1800c934c6baed4ff4f5b7ac0bdc19acbc52a8e6092f03e5f7b2b8` |
| recovery DTBO | `dd2acb7f8e02a58bba6b9ee9da09790286ab19c887ef7feabc8a8be10e0e8f98` |

The kernel payload SHA is the byte-preservation pin. The existing baseline
record associates the recovery/runtime with GNU build ID
`b2dda820b18d410d9bf12f1bd2584567d545991d`; that ID is recorded as a
reference, not extracted from the raw Android `Image` payload.

The CPIO has 963 unique records and 324 module records. The exact replacement
set is:

| CPIO path | Bytes | SHA-256 | GNU build ID |
|---|---:|---|---|
| `lib/modules/snd-soc-samsung-abox.ko` | 9,596,112 | `55bae9f12135a2134337d7d520ddfadc85cdd049cedefd2a3c41f871fdd329bf` | `26347c3373e155fa6badf7883ff162f1d9f6723f` |
| `lib/modules/rainbow_prince.ko` | 522,472 | `6461073beee9e1fdc4f7c92b250bbb773a18cbd766e0c9331e77ec01e5e45170` | `8a7227b58cb7f4faf73ea92974781d34870bbfac` |
| `lib/modules/exynos-usb-audio-offloading.ko` | 401,656 | `92116d85c21c9c3969e00746fb0299a5cb7725edfe9c344d5645417686416ec2` | `8c9b0d4787ea32eae7de0086a4d662b0f351675d` |

Their preserved CPIO predecessors are independently pinned by full size,
SHA-256, and GNU build ID in the builder. The current `fimc-is.ko` remains
7,164,248 bytes, SHA-256
`256926d8a1499d9fd8fcef22fc9ad677998decb1317b2037045c8dcc44b35ecf`, build
ID `59e54c032c545fff3ba52156f226fb6d69aadf64`. The other 321 module records,
bootstrap files, and all remaining CPIO records are required to remain byte-
identical.

## Dependency and discovery metadata

Read-only comparison of the preserved modules and the three exact candidates
found equal direct dependency **sets** for all three modules and equal alias
sets. The ABOX5 and Rainbow Prince raw `modinfo -F depends` lists have
ordering-only differences; the offloader list is byte-for-byte the same.
ABOX5 still lists `snd-soc-samsung-abox-gic`, and its GIC imports and CRCs are
present in the candidate. No dependency or GIC provider is removed or stubbed.

The packager fails if any dependency membership or alias set changes. It also
pins and preserves the original CPIO metadata records without regeneration:

| Record | SHA-256 |
|---|---|
| `lib/modules/modules.dep` | `fe40d3926aa809acfcd17bbcf1cfe6c865f518af819bd27bf40d6562ca4dba22` |
| `lib/modules/modules.alias` | `c4437cdbbb7ed6e4af6b404a9175a5dcad7580b64013d65baf728c522b950ffb` |
| `lib/modules/modules.softdep` | `56c5b007ba4d737529861aca3a559d939d2f906d4752bc4307d08acb6ece9db0` |

It reconstructs the CPIO using the pinned camera packager's newc helper and
requires every non-target raw record, record order, and target metadata fields
to match. Only the three target payloads and their new file-size fields may
change.

## Static ABI preflight

The builder reconstructs the provider set from the pinned 17,283-row
native-eight `Module.symvers` (SHA-256
`15fc69e815cb4da6cb3372f4b5141005ab2e770b0414b67f230f1b185bf03df7`). It
removes the 28 old ABOX rows and five old offloader rows, then adds the exact
28-row ABOX5 and five-row rebuilt-offloader `modules-only.symvers` files. The
canonical final 17,283-row map has SHA-256
`0c8e481225fe3ab071ba9be1d14faae06ce8a556fa7e89b07f3dcefb761bd470` and no
symbols with distinct-CRC duplicate providers.

An independent host-only scan substituted the three exact candidate module
bytes into the preserved 324-path CPIO inventory for static inspection. It
recorded 16,569 imports across 324 modules, with zero missing, mismatched,
unknown-CRC, or ambiguous imports; all 324 had `__versions`, exact pinned
vermagic, and `module_layout` CRC `0x0e3c515c`. This scan inspected extracted
temporary module files; it did not construct or publish a candidate CPIO or
image.

The selected external Lineage WLAN file is separately pinned at 15,466,952
bytes, SHA-256
`cbf8932d079e97006a5b7aae0e1b5acfe65b3e8b5113ab8fa095daa36796738d`. Its 495
imports, `__versions`, vermagic, and `module_layout` record passed the same
static provider-map comparison. WLAN is external to the ramdisk and is not
copied into this three-module package.

These are host ELF/MODVERSIONS checks, not execution of the kernel loader.
They do not establish current-phone module membership, loaded file paths,
kernel memory identity, module loading, runtime PM, IPC, DMA, PCM progress,
physical audio, or deployment acceptance. The 324-module preserved CPIO set
is distinct from the separately observed 325-module phone state.

## Tool behavior and tests

The future packaging command requires `python3 -I -S`; `-B` is recommended but
not enforced. `--help` exits before the in-program startup gate or any input
preflight. If a caller ignores `-I -S`, Python startup hooks may run before the
program can refuse execution; the gate is not a sandbox. The implementation
uses the pinned camera CPIO/output helpers, trusted boot-image/AVB helpers,
and the pinned host ABI preflight; output paths must be absent, files use
exclusive creation, and the success manifest is written last after fsync and
hash rechecks.

The focused suite contains eight hardware-free/temp-fixture tests for the
exact-three transform, raw-record preservation, missing/duplicate/symlink/
corrupt targets, hash pins, provider-map replacement, dependency/alias
comparison, stale/incomplete ABI reports, output overwrite refusal, and CLI
startup behavior. All eight pass in each requested mode:

```text
python3 -I -S -B tools/hardware/test-build-audio-coherent-recovery.py
python3 -O -I -S -B tools/hardware/test-build-audio-coherent-recovery.py
PYTHONOPTIMIZE=1 python3 -S -B tools/hardware/test-build-audio-coherent-recovery.py
```

The effective environment-optimization run omits `-I` because isolated mode
ignores `PYTHONOPTIMIZE`; it is the test runner only, not a package build. The
startup test independently checks that a non-isolated builder invocation is
refused before input preflight.

No candidate `lz4` compression, `mkbootimg`, AVB footer addition, or output
publication was executed. No kernel/Image build, module execution/load,
phone/device operation, or deployment-profile change occurred. The next step
is independent review of the exact three-file diff; only after that and
coordinator GO may a real host package be produced.
