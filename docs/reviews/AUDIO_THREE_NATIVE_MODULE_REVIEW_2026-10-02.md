# Independent review: three-patch ABOX native module — 2026-10-02

## Disposition

| Evidence gate | Review result |
|---|---|
| Single-target module compilation | **PASS** — actual native AArch64 `snd-soc-samsung-abox.ko` verified |
| New module imports vs filtered native-eight dependencies | **PASS** — 359/359 match, including `module_layout` |
| Integration with the frozen native-eight module set | **BLOCKED** — 10 changed ABOX export CRCs leave 6 stale imports in 2 existing modules |
| Module loading, current-phone inventory, firmware/audio hardware | **NOT RUN** |

The integration blocker is reproducible from the actual private output and
actual baseline `.ko` metadata. Do not force-load, suppress MODVERSIONS checks,
package, or deploy this candidate. A separate ABI5 repair is outside this
review and does not change this artifact's blocked integration result.

The author report and receipt are
[`AUDIO_NATIVE_MODULE_BUILD_2026-10-02.md`](../research/AUDIO_NATIVE_MODULE_BUILD_2026-10-02.md)
and
[`s22-audio-native-module-build-20261002.json`](../../evidence/s22-audio-native-module-build-20261002.json).
This review was performed on `codex/s22-audio-three-artifact-review-20261002`
from candidate `79c539ea55a1663e946ed881606232519873fd93`; the author evidence
commit is `492fcf07c5803246ed5783dffc871ff694284f38`.

## Source, configuration, and build provenance

- The build source is clean commit
  `3c11bdda6ba6ba27fb4eb7e2cb096d98c514d6d3`, tree
  `8aac19a6621ed74db63960d0f4af84557a20404c`, directly based on native-eight
  `872bffb8ea2ea657f94d10b866dc655b5718d6db`. Its complete four-path delta is
  `include/trace/events/samsung_abox.h` and
  `sound/soc/samsung/abox/{abox.c,abox.h,abox_rdma.c}`. I verified the three
  patch-file hashes against the receipt: observation
  `afe1cbd271559b9aba5907ec732613a6efabb730e4763b8356c3c72906c93a4d`, error
  path `676735886d88441232d3e56aa07c77e38f63f823ffa09b90addc51918ef9fc9d`,
  and worker-PM
  `eeb5e15727fddbc3b153a08a1c77edee5e7be4a527d7ef1dbffb4ad3e4722c8a`.
- The native config export, output `.config`, pre-olddefconfig hash,
  post-olddefconfig hash, and post-build hash all equal
  `d762d5fc71e369013ee36657d007063ce1f0faca9707d5f9ccfba2597b7fcd16`.
  The retained olddefconfig phase reports exit 0 and unchanged bytes; its raw
  log contains `No change to .config`. The selected options include ABOX=m,
  tracepoints, CFI, Shadow Call Stack, MODVERSIONS, and LTO_NONE.
- All 13 resolved LLVM/AArch64 GNU tool binaries rehashed to the receipt's
  values (Clang/LLD 18.1.3). The monitored build helper hash is
  `56f39759e4a098562cbafd634a659b007be2a540a4ada6234962711be026e5d5`; the
  filtered-Symvers helper hash is
  `2404fab4a2ed469eab1930a7275c28fd3f5a0bbb1b23c88369338ca23cd26c67`; and
  the existing HCI preflight parser hash is
  `715383e85d4c5b426f8b6fa43961da2481b57a8fc52dcd54c335faf68d5c4155`.
- The persisted build phase is `build_finished`, `build_exit_code=0`,
  `resource_abort=false`, and `owned_process_group_absent=true`; the recorded
  builder and make PIDs were no longer live when checked. The actual command
  used `env -i`, `nice -n 10`, `-j1`, `V=1`, and only the target
  `sound/soc/samsung/abox/snd-soc-samsung-abox.ko`. Its fresh output contains
  one `.ko`, no `Image`, no `vmlinux`, and no other module.
- The actual `.cmd` files contain 28 ABOX C translation-unit compile commands;
  every one includes `--target=aarch64-linux-gnu`, `-O2`, `-Werror`,
  `-fsanitize=kcfi`, and `-fsanitize=shadow-call-stack`. Both changed C files,
  `abox.c` and `abox_rdma.c`, are compiled. No LTO flag appears in those
  commands or the link command.
- The build uses normal in-tree `genksyms` under MODVERSIONS; the log's
  `__GENKSYMS__` invocations are the standard Kbuild CRC-generation path. The
  changed source files contain no `__GENKSYMS__` branch or CRC override.
  Modpost consumed the filtered input and ran with `-E`; no
  `KBUILD_MODPOST_WARN`, `MODPOST_WARN`, `KBUILD_EXTRA_SYMBOLS`, force-load, or
  module insertion command was found. There were no warning/error diagnostics.
  The review's `modprobe --dump-modversions` calls only read `.ko` metadata;
  they did not load modules.

The private output directory is
`/home/corpunum/s22-linux/builds/audio-native-three-out-20261002`. The
reviewed bindings include `build.log` SHA-256
`8c493139786b6d8d310b6f8a119bbb16e71889c432bfe97e0c7bd6e810c52e54`,
`build-phase.json` `709fb586bc82b837e6970a533b2c9b693942029ba0b9cd9601ff42ac3810457f`,
`olddefconfig.log` `c685b5fa46d08567eb4d7e17ba3b3b41f78ab1533e82d617e18c13ed04429e8c`,
`olddefconfig-phase.json` `cf36c0bfcbc8bd96faf4c2e29ee3a8029bc681d4910d87c5fe823d9651bd166a`,
and `resource-monitor.jsonl`
`309592aff5925b5111116dadd067263447f675d9b90ac4888209f58ad76d0261`.
These raw logs remain private/outside Git; their local bytes matched the
receipt's hashes during review.

## Artifact and ABI findings

The actual `snd-soc-samsung-abox.ko` is a 9,583,728-byte ELF64 AArch64
relocatable module, SHA-256
`38ddcb2c1fd369b217d022f56b8a20a5488fedc01d6583fd3b2e4be5ec685df8`, GNU
Build ID `052414946f06902f9e5caca6a69d2732235eb720`. Its vermagic is
`5.10.260-g4e5c5ad7d950 SMP preempt mod_unload modversions aarch64`, equal to
the baseline ABOX module.

I used the existing HCI preflight parser against the actual module and
filtered dependency Symvers. All 359 imports resolve with no missing,
ambiguous, unknown-CRC, or mismatched-CRC records; `module_layout` is
`0x0e3c515c`. The filtered dependency table SHA-256 is
`ada1bf87ea2f99233622f4b75a7a88a93952cb24fc1f806d727bf6177352cafa`; I
independently confirmed byte-for-byte that it is the 17,283-row native-eight
`Module.symvers` (SHA-256
`15fc69e815cb4da6cb3372f4b5141005ab2e770b0414b67f230f1b185bf03df7`) with
exactly the 28 ABOX-owner rows removed. The generated `modules-only.symvers`
has exactly 28 rows (SHA-256
`e73d89c9637e3cea289c4bc61d999967d9cd20ae33a73bf71bfd13c7439f67ac`).

Comparing those actual 28 candidate exports with the native-eight owner's
28 baseline rows gives 18 unchanged CRCs and these 10 changes:

| Export | Baseline CRC | Candidate CRC |
|---|---:|---:|
| `abox_hw_params_fixup_helper` | `0x97d1fec7` | `0x2004e1bc` |
| `abox_iommu_map` | `0xc12c99a2` | `0x67a41b5b` |
| `abox_iommu_map_sg` | `0x73a131d9` | `0xfb1a2964` |
| `abox_iommu_unmap` | `0xdbbfe1df` | `0xe8251aec` |
| `abox_iova_to_phys` | `0x46589d01` | `0xac033b11` |
| `abox_iova_to_virt` | `0x6f844da0` | `0x9aa832d4` |
| `abox_register_ipc_handler` | `0x9318da9c` | `0x2242e539` |
| `abox_request_cpu_gear_ext` | `0xb57ee649` | `0x7d80e140` |
| `abox_request_dram_on` | `0xc59d7e2f` | `0x7fe9fa89` |
| `abox_request_ipc` | `0xded8a290` | `0xf2895e57` |

I scanned the actual `__versions` metadata of all 329 `.ko` files in the frozen
native-eight host output using `modprobe --dump-modversions`; all 329 parsed
without error. Exactly six stale import records remain in two unchanged
consumers (six records, not twelve):

- `sound/soc/samsung/rainbow_prince.ko`: old
  `abox_hw_params_fixup_helper` CRC `0x97d1fec7`.
- `sound/usb/exynos-usb-audio-offloading.ko`: old CRCs for
  `abox_iova_to_phys` (`0x46589d01`), `abox_request_ipc` (`0xded8a290`),
  `abox_iommu_map` (`0xc12c99a2`), `abox_iommu_unmap` (`0xdbbfe1df`), and
  `abox_register_ipc_handler` (`0x9318da9c`).

Those imports conflict with the new export CRCs above under MODVERSIONS. Thus
the ABOX module's own compile and imports pass, but the unchanged 329-module
baseline set is not an integrated compatible set. The existing baseline
artifacts still match their frozen hashes: `Image`
`c1e27df303465bc4917bb24699f38792023a979c40728b58298b9b62815f000e`,
`vmlinux`
`067fef0fe101f9df0f16652080841ff88bddbe34e3074789120ba13df269a503`,
`Module.symvers` above, baseline `npu.ko`
`5e587db7065bc94755ed48f74bd8a904a1d37b243a2b8715701cbeef6e1459bb`, and
baseline ABOX module
`316529b81d4745adb7e1b70a9a36f3039c08c48753c1c84dd9c84a58e15646eb`. This
review was read-only with respect to those outputs.

## Scope and limitations

The 329 modules are the frozen host native-eight baseline, not the distinct
325-module current-phone inventory. No live inventory was queried. No SSH,
ADB, phone, load, firmware, BOOTUP, DMA, PCM, packaging, deployment, or
physical-audio action occurred. Module compilation and static ABI analysis do
not establish workqueue/runtime-PM progress or hardware acceptance.

The author's nine extracted-C harness runs and three-mode Symvers-helper tests
were not repeated as part of this artifact review; they remain report-attested
host tests and do not execute Linux runtime PM, workqueues, or audio hardware.
The separate ABI5 repair is not included or evaluated here. The compile-pass /
integrated-ABI-blocked / device-NOT-RUN distinction remains in force until a
final ABOX export ABI and its dependent module set are reviewed together.

The receipt records the explicit `gpt-6-luna` / `max` task selection; this is
not backend or runtime attestation. No rebuild or mutation of the author
report, source tree, baseline output, or actual module output was performed
during review.
