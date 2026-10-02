# Independent review: NPU ten native module artifact — 2026-10-02

## Disposition

**Clear for the narrow host-only NPU module compilation and static import-ABI
comparison.** I independently checked the source/patch composition, preserved
build inputs, completed build record, resulting artifact, and import-version
metadata. This is not evidence of module loading, current-kernel inventory,
NPU runtime, firmware acceptance, or hardware behavior.

The author report and receipt are
[`NPU_TEN_NATIVE_MODULE_BUILD_2026-10-02.md`](../research/NPU_TEN_NATIVE_MODULE_BUILD_2026-10-02.md)
and
[`s22-npu-ten-native-module-build-20261002.json`](../../evidence/s22-npu-ten-native-module-build-20261002.json).
The review ran on branch `codex/s22-npu-ten-module-review-20261002`, starting
from candidate `9bdd0bc52d22129e2a7aad215ebcaccfac62bb32`.

## Independent checks

- The build source is `709ac38b573d092e300f3787d0d5ba97dd333c0a`, tree
  `a1606229eeb84e55175fcd43fd9a6f2faff200ef`, directly based on
  `872bffb8ea2ea657f94d10b866dc655b5718d6db`; its worktree was clean. Applying
  the two pinned patches in order to an extracted parent copy produced byte-
  identical versions of the source worktree files. The complete delta is only:
  `npu-if-protodrv-mbox2.c` (patch 9,
  `2e2e2de8a28c5b408bfa0661535c358070130340318f1d0eaa0efe38edc11078`) and
  `npu-session.c` (patch 10,
  `7137b797c9b4658e1ec7e7829054b11ed684e51552933203d5af45b7a9743709`). No
  third source file is in the composed delta.
- The preserved configuration export and output `.config` are byte-identical,
  both SHA-256
  `d762d5fc71e369013ee36657d007063ce1f0faca9707d5f9ccfba2597b7fcd16`. The
  actual config enables `EXYNOS_NPU=m`, boot-ioctl, MODVERSIONS, Clang CFI,
  LTO_NONE, and Shadow Call Stack. The pinned builder source
  (`build-npu-six-profile.py`, SHA-256
  `56f39759e4a098562cbafd634a659b007be2a540a4ada6234962711be026e5d5`)
  contains the config-copy → `olddefconfig` → unchanged-hash guard. However,
  this module-only output does not retain `olddefconfig.log`; therefore I
  independently verified the final config identity and guard implementation,
  not the raw olddefconfig invocation or its reported “No change” output.
- I rehashed all 13 recorded LLVM/AArch64 GNU tools; their bytes and SHA-256
  values match the receipt. Clang and LLD report Ubuntu LLVM 18.1.3. The
  symvers-filter helper also matches its recorded SHA-256
  (`2404fab4a2ed469eab1930a7275c28fd3f5a0bbb1b23c88369338ca23cd26c67`).
- The persisted phase record says `build_complete`, make exit 0,
  `resource_abort=false`, builder absent, and owned make process group absent;
  I also confirmed those process IDs were no longer live. The build was the
  recorded clean-environment, `-j1`, `V=1` target
  `drivers/vision/npu.ko`, not a full image target. Actual `.cmd` records for
  both changed translation units and their build-log compile commands use
  `--target=aarch64-linux-gnu`, `-O2`, `-fsanitize=kcfi`,
  `-fsanitize=shadow-call-stack`, and `-Werror`. Modpost used `-m -E`; no
  warning/error diagnostics or warning-suppression overrides were found.
- The actual private artifact at
  `/home/corpunum/s22-linux/builds/npu-native-ten-module-only-out-20261002/drivers/vision/npu.ko`
  hashes to
  `a221c110ca2833b499b1fc7fbd8d34fbef38a60673aa4a70f0e919d818021200`, is
  14,769,536 bytes, AArch64 ELF64, Build ID
  `aee59cd0b95ed49038d0712106ac15bb4c1e9293`, with vermagic
  `5.10.260-g4e5c5ad7d950 SMP preempt mod_unload modversions aarch64`. Both
  patched C units are represented by actual compiled object commands, not just
  filenames in a receipt. The fresh output contains one `.ko` and no `Image`.
- The native-eight baseline `Module.symvers` is 17,283 rows and hashes to
  `15fc69e815cb4da6cb3372f4b5141005ab2e770b0414b67f230f1b185bf03df7`. The
  filtered 17,282-row dependency input hashes to
  `add620bc3a3732654f23161e4c966c7076b020568683f527402c6253cb59a334`; the
  excluded old NPU self-export and newly generated `modules-only.symvers` are
  the same single `vision_register_device` row/CRC (`0x982f6fee`), and the
  new module defines that symbol. `module_layout` is `0x0e3c515c` in both
  baseline and input.
- I used the existing HCI preflight parser
  (`s22-hci-candidate-preflight-20260924.py`, SHA-256
  `715383e85d4c5b426f8b6fa43961da2481b57a8fc52dcd54c335faf68d5c4155`) to
  inspect the actual new and baseline NPU modules against the full baseline
  exports. Each has 234 ordered import records with the same digest
  `99aef867af8439d5c5cf1954ff01cd3f70eff870c5e3dbc19c733b386f0aaf55`; the
  parser reports no missing symbols, CRC mismatches, unknown CRCs, or
  ambiguous CRCs. The matching `module_layout` CRC was confirmed. This is
  static compatibility against that baseline export map, not a live loader
  test.

## Scope boundary

No image or other module set was rebuilt, and no output was packaged, installed,
copied into firmware, deployed, or loaded. No device, SSH, or ADB action
occurred. The retained report itself keeps runtime/device acceptance `NOT_RUN`
and BOOTUP unauthorized. In particular, this artifact does not clear the
whole-operation NPU drain/close/firmware gate: committed `publish_done` waiting
remains unbounded, and close/reopen plus firmware/IRQ quiescence and hardware
liveness are not established. The existing 325-module/current-kernel
inventory and any NPU hardware acceptance remain unverified.

The build receipt records the requested `gpt-6-luna` / `max` selection from
explicit task configuration and correctly sets `independent_runtime_attestation`
to false. No build, packaging, device operation, or mutation of author/source/
output artifacts was performed during this review.
