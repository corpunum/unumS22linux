# ABOX5 coherent consumer build — 2026-10-02

## Result

The one authorized host-only build produced `rainbow_prince.ko` and
`exynos-usb-audio-offloading.ko` with exit code 0. Replacing those two modules
along with the already-built ABOX5 provider in the pinned native-eight
inventory yields a static ABI pass: all 329 modules and 17,255 recorded
`__versions` imports match the updated provider map. Every module has a
matching `module_layout` CRC (`0x0e3c515c`) and the exact expected vermagic.

This is a host-side artifact/inventory result only. It does not establish a
current-phone ABI, module loading, deployment, runtime PM, IPC, DMA, PCM
progress, or physical audio. The generic trace test's physical-slot reuse
coverage was closed separately by actual extracted-C tests in `3da3d06`,
independently reviewed in `6b9c1f6`; compilation itself does not cover it.
The cause of the earlier ten ABOX export CRC changes is not proven. No CRC
masking or force-load behavior was used.

## Inputs and execution

The clean source was commit `7363ab97d917a20f2c96632c94efd9ab7df7729e`
(tree `84325be79eab0244dd9a130e51d0974f6eadd4f5`), parent
`3c11bdda6ba6ba27fb4eb7e2cb096d98c514d6d3`. Its native-eight base was commit
`872bffb8ea2ea657f94d10b866dc655b5718d6db` (tree
`417e4a55e222b99ecca0f198e081d2d808ef5628`). The only four changed paths
relative to that base were the ABOX observation trace header and
`sound/soc/samsung/abox/{abox.c,abox.h,abox_rdma.c}`. NPU9/NPU10 changes were
not present.

The native config SHA-256 was
`d762d5fc71e369013ee36657d007063ce1f0faca9707d5f9ccfba2597b7fcd16`.
It retained ABOX, Rainbow Prince, and USB audio offloading as modules,
tracepoints, CFI, shadow call stack, MODVERSIONS, and `CONFIG_LTO_NONE=y`.
A separate bounded `olddefconfig` exited 0; its before and after config bytes
both hash to the same pinned SHA.

The dependency map for compilation started from the native-eight
`Module.symvers` (17,283 rows; SHA-256
`15fc69e815cb4da6cb3372f4b5141005ab2e770b0414b67f230f1b185bf03df7`). It
removed exactly the 28 old ABOX rows and five old offloader rows, retained
17,250 unrelated rows, and appended the 28 rows from the actual ABOX5
`modules-only.symvers`. This produced a 17,278-row input map (SHA-256
`89aadfec054550479f5b2f05fcd70c51a51bddbc7306209fa2e39f7fc2e46513`).
The old Rainbow owner had no export rows. The new offloader's five exports
were restored to the *post-build* integration map only after checking its
actual module and generated modpost rows.

The isolated wrapper used `env -i` with the pinned LLVM 18/system `PATH`,
`LANG=C`, and `LC_ALL=C`; it ran the exact two module targets with `-j1`,
`V=1`, nice level 10, and no `LD` or `LLVM_IAS` override. Tool binaries were
hash-checked against the native-eight receipt. The executed wrapper SHA-256
was `2624029f6fc3406a2256acb0df72c904fb92a30cb5e96ee1f3b6d65916f9157f`;
the monitored-build helper SHA-256 was
`56f39759e4a098562cbafd634a659b007be2a540a4ada6234962711be026e5d5`.
The isolated CPython 3.12 interpreter had optimize level 0 and SHA-256
`e50d468e8b0adfb05733f5b87b3cff34829c4a8c1aea50c865aa8bdfe4bb150f`.

The target exited 0; the monitor reported no resource abort and the owned
process group was absent afterward. Available-memory/free-disk low-water
marks were 23,055,863,808 and 44,629,704,704 bytes, above the configured 8
GiB/16 GiB abort thresholds. The raw private phase and log files remain
outside this worktree; their hashes and redacted command record are in the
evidence receipt. The `olddefconfig` target was separately bounded to 180
seconds; the following build phase ran only the two named module targets.

## Artifacts and ABI inventory

Both new outputs are ELF64 AArch64 relocatable modules. Their exact hashes,
build IDs, internal names, sizes, import digests, and phase/log bindings are
recorded in the JSON receipt. Their vermagic is
`5.10.260-g4e5c5ad7d950 SMP preempt mod_unload modversions aarch64`.
The build generated only the two requested `.ko` files; it did not generate
`Image`, `vmlinux`, a full module set, or a package.

The rebuilt offloader exports the same five names and CRCs as native-eight.
All five `__crc_*` values in its actual ELF match the new modpost rows, and
each export has a corresponding `__ksymtab_*` entry. Rainbow Prince exports
no symbols. The updated provider map is therefore the native-eight map with
the old ABOX/offloader rows replaced by the ABOX5 and rebuilt-offloader rows:
17,283 rows total, 17,250 unchanged providers, 28 ABOX providers, and five
offloader providers. There are no distinct-CRC duplicate providers.

The static scan used all 329 exact native-eight `modules.order` paths and
substituted only the ABOX5 artifact plus these two rebuilt consumers; the
remaining 326 modules were the original native-eight artifacts. The ordered
path inventory digest is
`d79baeab5ac13f13227b9f9ab71894f22ccab6f8157ceddb81f78624f83ec6ad`; the
path-plus-artifact-hash inventory digest is
`06e59a60df846aff3a2877367793b4558d1da610714df73f246ca72f2d2d1e77`.
The latter hashes `<relative path><TAB><artifact SHA-256><LF>` rows in
`modules.order` order. Import-record digests use normalized lowercase CRC and
symbol fields from `modprobe --dump-modversions` in its output order. The
final provider-map digest sorts complete `Module.symvers` rows bytewise and
hashes each row followed by LF.
Across all 17,255 import records there were zero missing, mismatched,
unknown-CRC, or ambiguous providers. All 329 modules contain a versions
section and a matching `module_layout` record; all vermagic strings match.
The canonical sorted-row digest of this final provider map is
`0c8e481225fe3ab071ba9be1d14faae06ce8a556fa7e89b07f3dcefb761bd470`.

The prior ABOX5-only result had six stale imports in these two consumers.
This build recompiled those consumers against the actual ABOX5 exports, then
checked the complete substituted 329-module inventory. That closes the
reported static dependency mismatch for this specific host artifact set; it
does not prove the CRC-generation cause or compatibility with a separate
phone module inventory. The earlier source review's generic physical-slot
coverage finding was subsequently corrected in `3da3d06` and independently
reviewed in `6b9c1f6`. That closes the host test coverage finding, not kernel
RCU/concurrency ordering, firmware behavior or physical audio. The original
author commit `7d0c3114` retains the stale open-gap wording for historical
inspection; this correction changes no source patch or build artifact.

## Scope limits

This does not authorize phone/SSH/ADB access, module installation or loading,
firmware operations, packaging, publication, deployment, or boot. The
native-eight inventory here is not the current phone's 325-module runtime
inventory. NPU9/NPU10, full kernel/image builds, and powered audio tests are
outside this result.
