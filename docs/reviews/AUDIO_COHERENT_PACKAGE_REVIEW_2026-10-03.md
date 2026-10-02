# Independent review: coherent audio recovery package

## Verdict

**BLOCKED for a real host packaging invocation.** The frozen builder has two
deterministic runtime failures: its selected-WLAN name check always rejects
the pinned report, and its isolated `mkbootimg.py` invocation cannot import
the script's local `gki` package. No package command was run, no candidate
output was created, and no phone or device operation was performed.

This is a scoped review of the package source and the three-path host-CI
allowlist addition. It is not package, kernel, loader, device, boot, or audio
acceptance.

## Reviewed pins and inputs

- Package source author commit: `291a892d79ad07c133cb2c83e9e5b1aaf342984e`;
  independent worktree cherry-pick: `cb4bd262c405687af60218322a2bc27cc13a411a`.
- CI allowlist commit: `705474888fb6e20fa1172f0030285e25c04eec27`;
  independent worktree HEAD: `df015e0137092a8399559922863a6f9dc169c693`.
- Builder SHA-256:
  `822495f01dd3354a8ec64d08223213f4de7188d69d132051f18f385fe54b6e77`.
- Focused test SHA-256:
  `619bacbf33ce2d2d83b9dae4dcbb0d6d5397357b45c15c4e0f5f68a60ec8ae54`.
- Authored research note SHA-256:
  `d2c0749a2d81486ca9813308c021cdafb5c00fd48f18dc7f48ee2c11d3dfa1ed`.

The implementation pins the B104 recovery image at 100,663,296 bytes,
SHA-256 `b10412715756da3cc8ee221368b49f179cc0c64ab7bd2802976480905e6d8d2f`,
and the three replacement modules:

| Module | SHA-256 |
|---|---|
| `snd-soc-samsung-abox.ko` | `55bae9f12135a2134337d7d520ddfadc85cdd049cedefd2a3c41f871fdd329bf` |
| `rainbow_prince.ko` | `6461073beee9e1fdc4f7c92b250bbb773a18cbd766e0c9331e77ec01e5e45170` |
| `exynos-usb-audio-offloading.ko` | `92116d85c21c9c3969e00746fb0299a5cb7725edfe9c344d5645417686416ec2` |

Independent read-only rehashes also matched native-eight `Module.symvers`
(`15fc69e815cb4da6cb3372f4b5141005ab2e770b0414b67f230f1b185bf03df7`),
candidate ABOX `modules-only.symvers`
(`e73d89c9637e3cea289c4bc61d999967d9cd20ae33a73bf71bfd13c7439f67ac`),
candidate offloader `modules-only.symvers`
(`715a469f62a95ab8813c866cb0656f756730475fb80322b1afb4dfcbcfd527ad`),
and the selected external WLAN
(`cbf8932d079e97006a5b7aae0e1b5acfe65b3e8b5113ab8fa095daa36796738d`). The
constructed 17,283-row merged provider-map hash is pinned as
`0c8e481225fe3ab071ba9be1d14faae06ce8a556fa7e89b07f3dcefb761bd470`.

The CPIO transform is carefully scoped: it validates exact source/candidate
hashes and ownership for the three target records, preserves every other raw
record and record order, and permits only the three payloads and their file
size fields to change. It preserves the pinned `modules.dep`, `modules.alias`,
and `modules.softdep` bytes, compares dependency and alias membership, and
checks full static ABI evidence for the 324 ramdisk modules plus the selected
external WLAN. Output creation is exclusive, hashes are checked before and
after copying, directories are fsynced, and the success manifest is published
last. Those paths have useful synthetic regression coverage; the blockers
below prevent reaching package publication.

## Reproduced blockers

1. **WLAN inspection rejects the known-compatible pinned module.** At builder
   lines 680–683, `inspect_module_file` is called with the descriptive string
   `"selected external Lineage WLAN"`. The pinned preflight helper returns
   `name = PurePosixPath(relative).name`, so this report's `name` is that
   descriptive label. The subsequent check at lines 687–690 requires
   `wlan["name"] == WLAN_MODULE_NAME`, whose value is `"wlan"`; the condition
   therefore fails every real run. An independent scan processed all 324
   ramdisk modules without error, then stopped at this unconditional WLAN
   guard. Direct inspection of the selected file showed its exact pinned hash,
   495 imports, expected vermagic and `module_layout` CRC, and zero missing,
   mismatched, unknown, or ambiguous import CRCs. The check should distinguish
   the report label from the module's actual `modinfo -F name` (or otherwise
   compare the right field), and a regression should assert the real report
   semantics.

2. **The isolated `mkbootimg` subprocess cannot import its pinned helper.**
   `_run_mkbootimg` at line 737 invokes `python -I -S -B <mkbootimg.py> ...`.
   I ran that exact interpreter/script isolation shape with `--help`; it exits
   1 before argument handling with `ModuleNotFoundError: No module named
   'gki'` at the script's `from gki.generate_gki_certificate import
   generate_gki_certificate`. The isolated launch does not put the script's
   `tools/mkbootimg` directory on `sys.path`. The child should retain isolated
   startup while explicitly exposing only the verified local helper package,
   then have a test execute the real pinned `mkbootimg.py --help` path.

These are implementation blockers, not missing input artifacts or ABI
incompatibilities. Separately, the package-specific eight-test suite does not
exercise AVB verification/corruption or `_isolated_avb_runner`. I did verify
the runner against the retained B104 image; the pinned verifier reported its
footer and `NONE` vbmeta/hash valid. This does not exercise candidate image
creation or the corruption-negative path. Existing camera-helper regression
coverage is relevant but does not test this package's isolated runner.

## Tests and CI scope

Focused package suite, independently run in the review worktree:

- `python3 -B tools/hardware/test-build-audio-coherent-recovery.py`: 8/8 pass.
- `python3 -O -B tools/hardware/test-build-audio-coherent-recovery.py`: 8/8 pass.
- `env PYTHONOPTIMIZE=1 python3 -B -c 'import sys; assert sys.flags.optimize == 1'`:
  passed; then the same environment with the focused test: 8/8 pass.

The effective environment-optimization run intentionally omits `-I`, because
isolated Python ignores `PYTHONOPTIMIZE`; it is test execution only. I also
ran the independent host-runner policy test under normal and `-O`: 7/7 pass in
each mode. The CI commit adds only the package test's literal path to the
runner allowlist, test inventory, and policy tuple; no discovery behavior or
runner logic changes.

## Limits

No package output, boot image, kernel, or module was built or published in
this review. The AVB check was read-only against the retained base image. A
successful host package run, once the two blockers are repaired and
independently reviewed, would still establish no current-phone membership,
module loading, PM/IPC/DMA/PCM progress, physical audio, Samsung
authentication, bootability, or deployment authorization.
