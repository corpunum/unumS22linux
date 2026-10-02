# NPU firmware report/profile lock unwind

## Change

The pinned NPU source has four `-ENOMEM` returns after taking a spinlock with
`spin_lock_irqsave()` and before its matching `spin_unlock_irqrestore()`:

| Function | Lock held at the NULL-buffer return |
| --- | --- |
| `npu_fw_report_store()` | `fw_report_lock` |
| `npu_fw_profile_store()` | `fw_profile_lock` |
| `fw_will_note_to_kernel()` | `fw_report_lock` |
| `fw_will_note()` | `fw_report_lock` |

The patch adds the matching unlock/IRQ restore before each existing
`return -ENOMEM`. It does not change the error value, normal store behavior,
gather scheduling, mailbox pointers, or firmware contents. In particular, the
profile early return is paired with `fw_profile_lock`, not the report lock.

The two note functions call `npu_log.log_ops->fw_rprt_gather()` before checking
the report buffer. The pinned `fw_rprt_gather()` takes `interface.lock`, calls
`npu_fw_report_store()` for copied ring segments, and advances the report
read-pointer without checking that store's return. Both functions and that
actual gather body are included in the extracted-C test. The test feeds it
only short synthetic bytes, suppresses all kernel logging, and never emits the
fixture text.

The test also compiles the existing
[`npu-mailbox-msgid-validation.patch`](../../tools/hardware/npu-mailbox-msgid-validation.patch)
`msgid_get_pt_type()` body together with the lock-patched report functions;
both patch files are independently checked/applied to their pinned source
files in temporary fixtures. The pinned result managers perform
`interface.msgid_get_type(msg.mid)` before `mbx_ipc_get_msg()`. The real
`msgid_get_pt_type()` body is tested with an out-of-range ID: the existing
validator patch routes that case through the actual `fw_will_note()` and then
returns `-1`; this new lock patch makes that diagnostic's NULL-buffer return
restore the lock and IRQ state. This is source/harness behavior only, not a
firmware or device result.

## Reproduction and evidence

Run from this repository worktree, using the clean bounded local fixture:

```sh
S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
  python3 tools/hardware/test-npu-fw-report-lock-unwind.py
S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
  python3 -O tools/hardware/test-npu-fw-report-lock-unwind.py
S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
  PYTHONOPTIMIZE=1 python3 tools/hardware/test-npu-fw-report-lock-unwind.py
```

Each invocation compiles and runs the extracted pinned C at both `-O0` and
`-O2`, for the original source and the patched composition. The local fixture
was clean at derived commit
`3fca50941422439b2019db2e4a3dc1016b2138a1`; the three loaded source files were
bounded and SHA-256 checked by the existing NPU fixture loader:

| Pinned source | SHA-256 |
| --- | --- |
| `drivers/vision/npu/core/npu-log.c` | `e98c22dc4d005b350d9c82bf067ada5b01a0059f766f13bce39594cc44172d5c` |
| `drivers/vision/npu/core/interface/hardware/npu-interface.c` | `c2deaa0abd990184b64373bb13983f048b925e0f5f421f6623de7de85f667108` |
| `drivers/vision/npu/core/npu-util-msgidgen.c` | `271dfe4f0b591a9a6d5a3f996e5fd2fe7a05d9b839513ce8691863bae79d3e2e` |

For the four NULL-buffer branches, baseline extracted C reproduces
`-ENOMEM` with the relevant lock held and IRQs left disabled. The patched
branches preserve `-ENOMEM`, clear the correct lock, and restore the saved IRQ
state (including an initially disabled state). The high-ID baseline reaches
the original `BUG_ON` after entering the actual note/gather path; the combined
patched validator/report path returns `-1` without the BUG or lock leak. The
host check also exercises the actual report/profile init/deinit functions,
normal and wraparound stores, a wrapped gather-ring read, newline handling,
and the `interface.lock` balance. A synthetic segment is also passed through
the actual gather body with report storage absent: baseline leaks
`fw_report_lock`, while the patch restores it and preserves the pinned behavior
of advancing the mailbox read-pointer despite the ignored store error. No
report payload is printed by the harness.

All three Python-mode invocations passed against the local fixture, with both
C optimization levels passing in each run. The patch was checked and applied
using ordinary `git apply --check --whitespace=error-all` / `git apply` in a
temporary fixture. The public fetch route remains bounded through the same
loader, but this run used only the verified local source tree; public fetch was
not retried after the rig's reported GitHub DNS failure.

## Limits

These are host-extracted-C regressions with controlled spinlock, IRQ, mutex,
mailbox-ring, and logging shims. They do not establish Linux kernel lockdep,
IRQ semantics, runtime NPU behavior, firmware acceptance, DMA progress, or
physical-device behavior. They do not test the whole NPU patch stack or build
a kernel/module.

This change only balances the locks on the four NULL-buffer error exits. It
does not repair or claim safety for the existing pre-lock reads of store
buffer size/write position, other report/profile ownership or teardown races,
buffer bounds/underflow, or any firmware-result/ID issue outside the separately
identified NPU message-ID patch. `fw_rprt_gather()` still ignores a store error
and advances its mailbox read position; this patch does not change that
contract. No phone, SSH, ADB, device, firmware, stream, or power operation was
performed.
