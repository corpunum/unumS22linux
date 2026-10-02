# NPU candidate patch-stack integration — 2026-10-02

Status: expected integration blocker reproduced. The regression test passes by detecting and validating the known patch-application failure; the four-patch stack is not integration-ready or build-ready.

Target base: LineageOS `android_kernel_samsung_s5e9925` commit `4e5c5ad7d950e4de0688b5663965f2075654b2ad`. Source fixture: clean derived worktree `3fca50941422439b2019db2e4a3dc1016b2138a1`; the test reuses `test-npu-probe-unwind.py`'s bounded fixture loader, pinned SHA-256 checks, 512 KiB per-source cap, and five-second public-fetch timeout. Four ordered patch hashes are pinned in the regression script.

## Ordered application result

The test reconstructs temporary copies of only the hash-verified source files and runs plain `git apply --check --whitespace=error-all`, followed by `git apply --whitespace=error-all` after each successful check. It does not use `--3way`, force, or fuzz options.

1. `npu-session-lifecycle-fix.patch`: check and apply pass.
2. `npu-refcount-transaction-fix.patch`: check fails at `drivers/vision/npu/core/npu-vertex.c:1224`:

   ```text
   error: patch failed: drivers/vision/npu/core/npu-vertex.c:1224
   error: drivers/vision/npu/core/npu-vertex.c: patch does not apply
   ```

The refcount patch's hunk at lines 432–440 expects the baseline `vertex->normal_count = 1;`, unlock, return, and `p_err_check:`/`p_err:` labels. The lifecycle patch replaces this `npu_hwdev_normal_bootup()` body: it tracks `lock_held`, routes errors through `p_err`, unwinds acquired resources, and funnels exits through `out_unlock:` guarded by `if (lock_held) mutex_unlock(&vertex->lock);`. The replaced body has no baseline `p_err_check:` hunk. The regression verifies those lock-state/cleanup markers remain in the lifecycle-patched function before testing the stale refcount hunk. This is evidence for a later reviewed reconciliation; it is not authority to drop or recreate either historical patch.

Because the required second patch does not apply, the third `npu-default-boot-callback-fix.patch` and fourth `npu-probe-unwind-fix.patch` are hash-verified but not applied as part of this ordered stack. They, and the not-yet-frozen shutdown-ownership patch, remain untested in a combined source stack.

## Negative application checks and BOOTUP gate

The executable test also verifies through actual `git apply` calls that:

- Swapping the first two patches (refcount then lifecycle) is rejected at `npu-vertex.c:1188`.
- A missing required refcount patch is not silently skipped.
- Mutating the refcount patch's `normal_count` context is rejected.
- Mutating that line in the pinned source fixture is rejected.

On a temporary source tree after applying the lifecycle patch, the test runs `npu-boot-preflight.py` with the pinned S5E9925 defconfig. The expected AIE firmware and DSP relocation-rule files are absent from this checkout; they are passed as unavailable paths, not synthesized. Preflight exits `2`, with `artifact_preflight_pass=false`, `bootup_ready=false`, and `bootup_authorized=false`. This is a host refusal check on the patched prefix only, not a full-stack or firmware acceptance result.

Run from this worktree:

```sh
S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 python3 tools/hardware/test-npu-candidate-stack.py
S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 python3 -O tools/hardware/test-npu-candidate-stack.py
S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 PYTHONOPTIMIZE=1 python3 tools/hardware/test-npu-candidate-stack.py
```

Each invocation reports `EXPECTED_INTEGRATION_BLOCKER` and exits zero only if the ordered conflict and negative checks are reproduced. A zero exit means the blocker detector passed; it does not mean the stack is ready to build.

## Evidence limits

No kernel build, phone/SSH/ADB access, hardware test, firmware staging, module load, or BOOTUP operation was performed. The integration test executes patch application and the existing host preflight only. No files in the read-only kernel source worktree were modified. The lifecycle/refcount conflict blocks the mandatory stack before its later hunks; shutdown integration remains pending.
