# Independent ABOX5 retained-artifact and fixture review — 2026-10-02

## Scope and disposition

Reviewed the ABOX5 build record authored in `29511c2dea38a143fd43619eff739b085a5e3a34`
and integrated in `2dbe36a9428015cbd62c6df090e4670052f69b85`, plus the
test-only correction at `c2bc9f4a19f4bfdd5264d11fb1b35ec7f86258ed`. For the
route fixture, I also cherry-picked and reviewed the single-test follow-up
`c9cd6ac3426880e5f202b7064e1f685011afea72` in this isolated review worktree.

The retained artifact is identified and its build/static import evidence is
consistent with the raw output. Its imports match the filtered native-eight
dependency set, but the candidate is not integrable into the unchanged
native-eight module set: ten of its 28 ABOX export CRCs differ, leaving six old
imports stale in two consumers. The cause of the CRC changes remains unproven.
This is host build and static ABI evidence only; it provides no device or
deployment clearance.

## Retained build and artifact

The output contains exactly one `.ko`, at
`builds/audio-native-five-out-20261002/sound/soc/samsung/abox/snd-soc-samsung-abox.ko`.
Read-only hashing and ELF inspection confirmed:

| Property | Observed value |
| --- | --- |
| SHA-256 | `55bae9f12135a2134337d7d520ddfadc85cdd049cedefd2a3c41f871fdd329bf` |
| GNU build ID | `26347c3373e155fa6badf7883ff162f1d9f6723f` |
| Format | ELF64 AArch64 relocatable module, with `__versions` |
| Size | 9,596,112 bytes |
| Vermagic | `5.10.260-g4e5c5ad7d950 SMP preempt mod_unload modversions aarch64` |

The source symlink resolves to the clean worktree at commit
`7363ab97d917a20f2c96632c94efd9ab7df7729e`, tree
`84325be79eab0244dd9a130e51d0974f6eadd4f5`, parent
`3c11bdda6ba6ba27fb4eb7e2cb096d98c514d6d3`. Its only changes from native-eight
commit `872bffb8ea2ea657f94d10b866dc655b5718d6db` are the four recorded ABOX
trace/IPC source paths. The four source-file hashes match the build-phase
record; the source worktree is clean now and was recorded clean after the
build. The five patch hashes also match the pinned build record, including
private-ABI patch SHA-256
`2c85c2603a31f8a5535a21a2efdd60f6e3750174b6c706828f777a7bb109e1d6`.

The native config was retained byte-for-byte: `.config`, the olddefconfig
before/after guard, and the exact config digest are all
`d762d5fc71e369013ee36657d007063ce1f0faca9707d5f9ccfba2597b7fcd16`.
The raw olddefconfig log hash is
`bdf0e144b1aee67971fde5615465bf0a0bdfe183771bf1cec236910dc2769f58`; its phase
records exit 0 and unchanged config hashes. The recorded settings retain ABOX
as a module, tracepoints, CFI, shadow call stack, MODVERSIONS, and LTO disabled.

The raw build phase records the one `-j1 V=1` module target under the pinned
`env -i` allowlist at nice level 10, `build_exit_code=0`, and
`resource_abort=false`. Its five monitor samples have low-water marks of
23,180,677,120 bytes available RAM and 46,303,342,592 bytes free disk, above the
8 GiB/16 GiB thresholds. I verified the recorded wrapper
(`b4720b418e8a735894cf7f796fd732a117d5622b743829327f1a10a2622a1bc1`), monitor
helper (`56f39759e4a098562cbafd634a659b007be2a540a4ada6234962711be026e5d5`),
symvers helper (`2404fab4a2ed469eab1930a7275c28fd3f5a0bbb1b23c88369338ca23cd26c67`),
interpreter (`e50d468e8b0adfb05733f5b87b3cff34829c4a8c1aea50c865aa8bdfe4bb150f`),
and all 13 resolved tool hashes against the current retained files; all
matched. The output build-phase, raw log, and resource-monitor hashes also
match the evidence record.

The `ABOX5_BUILD_ABORT SystemExit: 0` status line is a wrapper reporting bug,
not a target/resource abort: the wrapper surrounds `sys.exit(main())` with a
`BaseException` handler, so it prints that label for the normal successful
`SystemExit(0)` before re-raising it. The raw build phase independently records
target exit 0 and no resource abort. I preserved that historical line and did
not retry or rebuild the target.

## Static ABI comparison

The retained candidate has 363 versioned imports. `modprobe --show-modversions`
read-only inspection found all 363 `(CRC, symbol)` pairs in the filtered
dependency input, with no missing or wrong-CRC pair and no duplicate dependency
row among those imported names. `module_layout` is `0x0e3c515c`. The filtered
input is exactly the 17,283-row native-eight `Module.symvers` minus the 28 rows
owned by the old ABOX target: the baseline digest is
`15fc69e815cb4da6cb3372f4b5141005ab2e770b0414b67f230f1b185bf03df7`, the
excluded-row digest is
`7b7e8246796a1ea9965a31c4eaebd9b1af3cf19dfdb564984732c4f3ba79be37`, and the
17,255-row filtered digest is
`ada1bf87ea2f99233622f4b75a7a88a93952cb24fc1f806d727bf6177352cafa`.

Direct comparison against the native-eight ABOX export rows confirms 28 names,
18 unchanged CRCs, and 10 changed CRCs. Against the exact 329-entry
native-eight `modules.order`, all 329 listed `.ko` files exist and the actual
`.ko` count is 329; the sorted inventory digest matches the recorded
`d79baeab5ac13f13227b9f9ab71894f22ccab6f8157ceddb81f78624f83ec6ad`.
Comparing the candidate exports with the two old consumers confirms the six
stale imports: five in
`sound/usb/exynos-usb-audio-offloading.ko` and one in
`sound/soc/samsung/rainbow_prince.ko`. The exact ten CRC changes are tabulated
in the [build research note](../research/AUDIO_FIVE_NATIVE_MODULE_BUILD_2026-10-02.md).
No CRC was suppressed or force-loaded, and no reason for the changed CRCs is
claimed.

## Test corrections and bounded verification

The `c2bc9f4` fixture now renames the original trace path while retaining that
inode, creates a replacement at the old path, and explicitly checks the two
inode numbers differ. The follow-up `c9cd6ac` additionally sets the replacement
mode to `0600`, requires the readback to fail with
`trace_reservation_identity_changed`, then mutates the actual
`REMOTE_TRACE_READ` template by removing only its single inode predicate. The
mutant must accept the same replacement and return its bytes using the same
device/inode receipt arguments. This isolates the inode check from mode,
ownership, link-count, device, and size checks. In the `c2bc9f4`-only run, this
host's `0002` umask made a newly created replacement mode `0664`; readback would
reject that on mode even with the inode predicate removed, so that negative
assertion did not isolate inode enforcement. The `c9cd6ac` control closes that
gap. The earlier focused-pass vs full-run-fail discrepancy is consistent with
fixture inode allocation, umask, or test-order sensitivity, not evidence of a
live inode-ABA vulnerability.

| Host test | Python mode | Effective optimize | Result |
| --- | --- | ---: | --- |
| Audio route assessment (47 cases) | normal | 0 | 47 passed |
| Audio route assessment (47 cases) | `python3 -O` | 1 | 47 passed |
| Audio route assessment (47 cases) | `PYTHONOPTIMIZE=1` | 1 | 47 passed |
| Private ABOX trace ABI, verified local source fixture | normal | 0 | passed |
| Private ABOX trace ABI, verified local source fixture | `python3 -O` | 1 | passed |
| Private ABOX trace ABI, verified local source fixture | `PYTHONOPTIMIZE=1` | 1 | passed |

Each private-ABI run also passed its extracted-C `-O0`/`-O2` baseline, corrected
zero-sequence physical-slot case, and both dedicated overwrite/clear mutations.
The root diagnostic label now accurately says the zero-sequence **put** occurs
before traced dequeue; the immutable source patch hash above is unchanged.
That local private-ABI matrix ran after `c2bc9f4` and before the route-only
`c9cd6ac` follow-up, which did not change its source, harness, test, or patch.
These tests compile/run only host C shims or short Python snippets. No kernel
target was rebuilt or the retained module executed.

## Evidence boundary

The inode mutation is a controlled local temporary-filesystem test of the
embedded readback predicate, not cryptographic attestation or proof about every
filesystem race or the device's persistent storage. The retained phase and
matching current-file hashes corroborate provenance, but they are not an
external attestation of the historical process image. Likewise the ABOX
artifact and import checks do not establish Linux workqueue/RCU behavior,
runtime PM, firmware acceptance, IPC, DMA, PCM progress, or physical audio. No
phone, SSH, ADB, module load, firmware, packaging, publication, deployment, or
boot action was performed.
