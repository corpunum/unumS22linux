# Independent NPU twelve-module artifact review — 2026-10-03

## Verdict and scope

The retained output passes this bounded host-artifact review: the single
`drivers/vision/npu.ko` is bound to the frozen source/config/build record, and
its static import/export version records match the pinned native-eight
`Module.symvers` and the prior native-ten NPU module. No artifact identity,
build-scope, or static symbol-version blocker was found in that scope.

This is not deployment or device acceptance. It authorizes no packaging,
module load, phone access, BOOTUP, or runtime test. It does not prove firmware
response, IRQ/workqueue/report lifetime, model availability, or NPU function.
The build receipt separately records an unresolved `npu_interface_open()`
IRQ/workqueue error-path finding; this artifact review did not re-review that
source finding, and it remains a deployment/runtime blocker requiring its own
repair, review, and fresh build.

## Source, inputs, and execution record

The actual source worktree
`/home/corpunum/s22-linux/builds/npu-native-twelve-kernel-20261003` is clean at
`e9c3016233a72ceccb13e537f0b7ef72426582b9`, tree
`f917408e1c3388c87a8ed8f0220e4f9c3c9c6a4e`, directly parented by
`709ac38b573d092e300f3787d0d5ba97dd333c0a`. The only committed source paths
changed from that parent are `drivers/vision/npu/core/npu-log.c` and
`drivers/vision/npu/core/npu-util-msgidgen.c`. Their reviewed patch inputs
match SHA-256 `71c2fa44f0fe42bd94ee416fb09453185dd418b408ba15937ec3c787a5e9bf5d`
and `c8366edfab42090ac09a6c366ad3c535a62e13384dd494ed685bbfe524a64d14`,
respectively.

The preserved config export and actual output `.config` both hash to
`d762d5fc71e369013ee36657d007063ce1f0faca9707d5f9ccfba2597b7fcd16`.
`olddefconfig.log` is retained (869 bytes, SHA-256
`88a885d080fd145f312d6dd20b008e8341fe832670b5aa1e908f1e58ceccddc7`) and
reports no configuration change. The post-target
`include/config/kernel.release` is
`5.10.260-g4e5c5ad7d950` (SHA-256
`42baea336b74cd6cdbd1d3dbd3c9709c3fff772b21ba109458b388b72d00d961`).
Relevant settings include `CONFIG_EXYNOS_NPU=m`, `CONFIG_MODVERSIONS=y`,
`CONFIG_CFI_CLANG=y`, `CONFIG_SHADOW_CALL_STACK=y`, and `CONFIG_LTO_NONE=y`.

I inspected the actual output and frozen receipt, not a rerun. The append-only
phase journal records one `make -j1 V=1 drivers/vision/npu.ko` target under
`nice` and `env -i`; no `Image` target was requested. The build phase ended
`2026-10-02T22:54:57Z` with child exit 0 and `resource_abort=false`. The
recorded operation-start gate was 12 GiB available memory / 24 GiB free on the
host builds filesystem; the unchanged monitor abort limits were 8 GiB / 16
GiB. Six retained monitor samples stayed above the monitor limits: minima
24,155,299,840 bytes available memory and 30,556,766,208 bytes free disk.
These are point-in-time readings, not reservations. The separate 32 GiB
full-profile initial-disk policy was not changed or superseded.

The phase record pins the wrapper, filtered-Symvers helper, monitor helper,
and all 17 executable invocation identities (13 LLVM/GNU compiler tools,
Python, `make`, `nice`, and `env`). I independently recomputed all 17 recorded
tool hashes; all matched. Wrapper SHA-256 is
`8dab1a2c0ea2b01555645717b46c0f1506ff951a60a9256669d90bae6859d61d`, filter
helper SHA-256 is
`2404fab4a2ed469eab1930a7275c28fd3f5a0bbb1b23c88369338ca23cd26c67`, and
the monitored-build helper SHA-256 is
`56f39759e4a098562cbafd634a659b007be2a540a4ada6234962711be026e5d5`. The
raw build log hashes to
`e47b0b7f6a9562ff9943e6aecb3d1e26f7d0c98baa5238103dee3b7e4cedf389`; the
phase journal and resource log hash to
`6077d2a714ddf98ea5f511058bd1b47dc30ad56e9be4841e542a27532fa1f16b` and
`449b3727f3f2e649297cf7ff2de8fc45c07bd0dd474d932dc21991b8aa3ff064`.
The recorded builder and make process IDs/process group have no surviving
processes.

## Artifact and static ABI checks

The actual private output contains exactly one `.ko`,
`drivers/vision/npu.ko`, and no `Image`, DTB, or image file. Its SHA-256 is
`03e1ad6403265d39e1d6c48d31c1defad3aad984cd972f8fe80f8ee0c4eaa621`; it is an
ELF64 little-endian AArch64 relocatable module with GNU Build ID
`59da71800d33e0c7dac1936908ffab47ec99584c`, internal name `npu`, and vermagic
`5.10.260-g4e5c5ad7d950 SMP preempt mod_unload modversions aarch64`. The
output directory is owner-only. The module was not loaded or executed.

I independently read the ELF sections and used `modprobe --dump-modversions`
and `--show-exports` as static file queries only. The `__versions` section is
0x3a80 bytes and contains exactly 234 ordered import/CRC records. Their
canonical `CRC<TAB>symbol<NL>` SHA-256 is
`99aef867af8439d5c5cf1954ff01cd3f70eff870c5e3dbc19c733b386f0aaf55`; the
prior native-ten NPU artifact has the same 234 records in the same order
(its module SHA-256 is
`a221c110ca2833b499b1fc7fbd8d34fbef38a60673aa4a70f0e919d818021200`). I
compared every import against both the pinned full native-eight
`Module.symvers` (17,283 rows, SHA-256
`15fc69e815cb4da6cb3372f4b5141005ab2e770b0414b67f230f1b185bf03df7`) and
the build's filtered input (17,282 rows, SHA-256
`add620bc3a3732654f23161e4c966c7076b020568683f527402c6253cb59a334`): zero
missing, ambiguous, or CRC-mismatched imports in either map. `module_layout`
is `0x0e3c515c` in the module and both maps. The single export is
`vision_register_device`, CRC `0x982f6fee`, matching the baseline owner row
`drivers/vision/npu`.

The two changed C translation units were compiled in this actual build. Their
retained `.cmd` records and verbose build-log compile commands show
`-fsanitize=kcfi`, `-fsanitize=shadow-call-stack`, `-ffixed-x18`,
`-mbranch-protection=pac-ret+leaf+bti`, and `-O2`; the corresponding object
files are present. This verifies the recorded compile flags for those
objects, not runtime enforcement or driver correctness.

## Evidence boundary

The machine-readable independent observations are in
[`s22-npu-twelve-artifact-review-20261003.json`](../../evidence/s22-npu-twelve-artifact-review-20261003.json).
The imported build receipt is author commit
`e7f0035735f50349b1ab1732fe59edd78a1aa971`; the independent review worktree
added only this note and that JSON receipt. The private raw logs and module
remain at the paths above; they were not copied or modified.

This clears only the retained host compile, artifact identity, and static
symbol-version checks against the pinned native-eight baseline. It is not a
current-kernel or phone ABI attestation, loader test, firmware test, complete
kernel build, NPU publisher/IRQ lifetime proof, BOOTUP clearance, or device
acceptance. No kernel rebuild, package, module insertion, phone, SSH, ADB, or
deployment action was performed for this review.
