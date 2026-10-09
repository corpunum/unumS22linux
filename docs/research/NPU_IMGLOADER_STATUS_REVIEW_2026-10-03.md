# Independent NPU image-loader status review — 2026-10-03

## Verdict

**PASS_LIMITED for the exact frozen provider-status patch's extracted-C behavior.** The reviewed patch SHA-256 is `e7355e8906b22f1decf00de59cd644aac7bfcf6d861d97cdb3ff9bf084428385`; all three requested Python modes fetched the public pinned inputs and passed 32 actual extracted-C compile/run jobs each. A supplemental actual-C control also confirmed first-error and callback/notify ordering when both fail.

The frozen runner has a separate patch-identity gap: it applies the local provider-status patch after checking its selected paths, but does not compare that file with the SHA recorded in the research note. This review independently verified the frozen patch SHA before judging its results. The test-integrity gap does not change the result for this identified frozen patch; it does mean the frozen runner alone is not a self-contained pin for that input. A coordinator follow-up correction was independently checked in a separate, still-uncommitted integration worktree; its scope and results are recorded below and are not part of the reviewed HEAD.

This is source and host-shim evidence only. It grants no kernel/module, firmware, deployment, BOOTUP, runtime, or hardware acceptance.

## Frozen identity and method

- Review worktree: `/home/corpunum/s22-workers/npu-provider-review-20261003`, branch `codex/s22-npu-provider-review-20261003`.
- Reviewed integrated HEAD: `39735fcd68c6b175e1aa3bc3bb0c5d129906671f`, tree `bbb85595ec90b7359f837647c2f0a46436bf5614`; worktree was clean before review artifacts were added.
- Public kernel base: LineageOS `android_kernel_samsung_s5e9925` commit `4e5c5ad7d950e4de0688b5663965f2075654b2ad`.
- Status patch SHA-256: `e7355e8906b22f1decf00de59cd644aac7bfcf6d861d97cdb3ff9bf084428385`.
- Harness SHA-256: `748eae416333f6aafa3926155e1e756c9152e6630360c6db89c3f840c548882e`.
- Runner SHA-256: `fe970bf5c8b224d594e28e09850ed9f877ade1f523044d51c940f4a3f0c0cbee`.
- Research note SHA-256: `1564bc73acb658b2be852623f67fedd3a377fd8d3a500461940f5579f8ed5fd3`.

The runner fetched five raw source files at the exact public pin, verified their recorded hashes, composed the explicit native-eight/NPU13/corrected-NPU14 selected patch stack, then applied the provider-status patch to only `imgloader.c`, `imgloader.h`, and `npu-system.c`. The public route does not query local Git history. All matrix invocations unset `S22_NPU_STATUS_COMPOSED_SOURCE_TREE` and explicitly used `--skip-optional-local-fixtures`. The default NPU12 fixture path exists on this host but was not inspected or used in these public-route runs. An explicit missing fixture override was separately rejected with exit 1 after public source fetch and before any C compilation.

## Independent execution

Compiler: `/usr/bin/cc`, Ubuntu GCC 13.3.0. Each invocation completed 32 compile/run jobs: baseline and patched C at C `-O0` and `-O2`, covering BOOT_IOCTL and runtime-PM/STM, S2MPU-supported and non-S2MPU routes, the image-loader-disabled stub, and secure warm-boot skip combinations.

| Python invocation | Observed `sys.flags.optimize` | Result |
| --- | ---: | --- |
| `PYTHONDONTWRITEBYTECODE=1 python3 -B tools/hardware/test-npu-imgloader-shutdown-status.py --skip-optional-local-fixtures` | 0 | 32 jobs passed |
| `PYTHONDONTWRITEBYTECODE=1 python3 -B -O tools/hardware/test-npu-imgloader-shutdown-status.py --skip-optional-local-fixtures` | 1 | 32 jobs passed |
| `PYTHONOPTIMIZE=1 PYTHONDONTWRITEBYTECODE=1 python3 -B tools/hardware/test-npu-imgloader-shutdown-status.py --skip-optional-local-fixtures` | 1 | 32 jobs passed |

The baseline extracted C reproduced all three false-success paths: permission-release failure returned through the old void interface, callback failure was only reflected in `shutdown_fail`, and notify failure was only logged; each continued through CPU_OFF and cleared ownership. Patched C returned each negative provider error, mapped unexpected positive errors to `-EIO`, retained `FW_LOAD` and all lower-stage owner bits on error, stopped before CPU/STM/clock/buffer/wake teardown, and rejected later suspend/open/close without retrying permission release.

The harness's reentrant provider shim invoked suspend while the permission-release call was outstanding. The nested call returned `-EUCLEAN`; only one permission-release and notify call occurred. This validates the atomic claim in a deterministic reentry case, not threaded races or Linux memory ordering.

I also compiled and ran one supplemental status-path harness from the same hash-verified public source and exact patch. With callback `-EIO` and notify `-ECOMM`, it returned the earlier `-EIO`, recorded `I → P → C → N` (interface close, permission release, callback, notify), requested no CPU_OFF, and retained quarantine. The test runner's stock matrix covers each callback and notify error separately; this combined first-error case was added only in a temporary review harness.

The source and controls confirm that the existing CPU_ON uncertainty guard (and the non-BOOT_IOCTL STM guard) remains before interface/provider calls, and that the runtime resume owner guard still returns `-EBUSY` before firmware allocation. Config-disabled and secure warm-boot paths preserve their existing skip behavior. The provider call remains before the later CPU_OFF request on BOOT_IOCTL, and before STM-disable/CPU_OFF on runtime-PM. This review does not endorse that physical ordering.

## Source findings and remaining limits

The checked provider function returns the permission-release errno immediately, skips later steps on that failure, normalizes positive results to `-EIO`, and otherwise keeps the first callback/notify error (`tools/hardware/npu-imgloader-shutdown-status.patch:20-65`). The supplemental combined-error case confirms that ordering. The public provider descriptor and ops definitions compare byte-for-byte before and after; the legacy `void imgloader_shutdown()` declaration/export remains, the two direct MFC callers remain on it, and the NPU provider's `.shutdown` callback is still `NULL`. The enabled API export and config-disabled inline stub are both present.

NPU suspend claims `FW_SHUTDOWN_UNCERTAIN` before the provider call and leaves it set on every error. On reported success it clears `FW_LOAD` before releasing the claim (`tools/hardware/npu-imgloader-shutdown-status.patch:128-169`). The caller retains all owner state after any provider error. These are conservative source-state decisions: the actual S2MPU function is one external call whose partial effects cannot be reconstructed from an error return. The harness injects that call's status and cannot establish whether permission was partly released.

The frozen runner identity gap is concrete. It verifies NPU13/NPU14 hashes, but at `test-npu-imgloader-shutdown-status.py:301-306` only checks the provider patch's selected path set before applying the current file; `:546-549` prints its digest after execution and does not compare it to an expected digest. The research note says every patch is digest-pinned (`docs/research/NPU_IMGLOADER_SHUTDOWN_STATUS_2026-10-03.md:47-56`). The exact frozen patch did match the externally supplied digest for this review, so all 96 jobs and the supplemental case exercised the requested artifact.

### Separate coordinator correction review

The coordinator's follow-up changes only `tools/hardware/test-npu-imgloader-shutdown-status.py` in `/home/corpunum/s22-workers/integration-20261002`, at base commit `10846e9ad0a2b399a90148aa2e4d3d56231a1e52`. The reviewed uncommitted file SHA-256 is `b5319344c0ac811d4972c6ba221b3b4cb71eecf1cb560b3e54ab86f60fb56426` (25 insertions, no deletions). It adds the expected provider-patch digest and size checks, runs exact/newline/empty/oversize rejection controls before public source fetch, and rechecks the same patch buffer immediately before application. The original 96-job matrix was not rerun because the provider patch and extracted source are unchanged.

I imported that corrected runner under normal Python, `python3 -O`, and `PYTHONOPTIMIZE=1`. In all three modes the exact input was accepted and the trailing-newline, empty, and `MAX_PATCH_BYTES + 1` inputs were rejected. The trailing-newline variant was independently shown to remain applicable to the pinned public composition and to produce the same three corrected source hashes. Inspection confirms `main()` invokes the integrity controls before `load_public_sources()`, the pre-apply composition path revalidates the bytes, and the gate uses explicit checks rather than Python `assert`. This clears the identified test-integrity gap for the inspected follow-up diff, pending its integration/commit; it does not alter this review's frozen-input identity or source verdict.

The patch does not cover `npu_system_release()` or device remove/unbind; those lifetime paths remain a separate unresolved limit. The provider status may also be correct while remote firmware or CPU state remains live: the pinned NPU callback is absent, the pinned notify helper is a no-op, and CPU_OFF is a later independent request. A hung provider has no new timeout or drain escape; a reported failure intentionally keeps ownership and can block suspend indefinitely. No Kbuild, module object/link, symbol CRC, firmware, provider, S2MPU, CPU/STM transition, DMA, TrustZone, or device test was performed here. The additional export still requires a matched provider/NPU module build and deployment review.

The coordinator separately reported four ARM64 frontend jobs (baseline and provider/system variants) exiting cleanly with zero diagnostics. I did not inspect that private runner or reproduce those jobs; I exclude that report from this independent verdict. No object, link, or CRC evidence was supplied to this review.

No phone, SSH, ADB, USB, BOOTUP, firmware, provider, S2MPU, or TrustZone operation was performed. The old provider-before-CPU_OFF/STM order is recorded for source fidelity only and remains physically unaccepted.
