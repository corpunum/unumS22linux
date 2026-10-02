# S22 NPU probe and ABOX trace source review — 2026-10-02

Status: independent source/host review of the frozen NPU probe-unwind and ABOX IPC-observation candidates, plus the frozen NPU stack-blocker detector. The isolated probe and audio candidates have no blocking source finding in the reviewed scope. The mandatory ordered NPU stack is blocked before the callback and probe patches can be combined. NPU shutdown ownership is explicitly not reviewed in this receipt; its frozen input is still pending.

No phone, SSH, ADB, kernel build, module load, firmware operation, BOOTUP, deployment, reboot, or hardware test was performed.

## Inputs and source identity

The pinned kernel base is LineageOS `android_kernel_samsung_s5e9925` commit `4e5c5ad7d950e4de0688b5663965f2075654b2ad`. Hash-verified source fixtures came from the clean derived tree at `3fca50941422439b2019db2e4a3dc1016b2138a1`.

Reviewed frozen inputs:

- NPU clock acquisition/probe unwind: author commit `bd3204afb92ab43c53138d654b2850b362401781`; integrated code is identical at `de837b823f6ba7a722056f3103e9d407976e0ea2`.
- ABOX IPC tracepoints: author commit `58dd9c8370f4f20ac9e7c32c6410ef4a2ceecd21`; integrated code is identical at `d16a7fd46f9265eb65ace1e849bed558c6dcd447`.
- NPU stack blocker detector: author commit `873894f1ad9139429192a6b215be05b06b9b0828`; integrated test/note are identical at `4c1131bf674d489321d331666457c968ce0f07ff`.

The review ran in `codex/s22-source-wave-review-20261002` at `785d74b2695d3bee8bb9f9ecde3d083e2b2140c7`. The only owned repository change in this review worktree is this document.

## NPU clock acquisition and probe unwind

The candidate makes `npu_clk_get()` strict when `clock-names` is absent and adds `npu_clk_get_optional()` for the hardware-device nodes whose pinned type/DT contract has no direct clock list. The exact pinned S5E9925 DT has DNC (`type 0x03`) with required `dnc_noc`; NPU/DSP (`0x07`), CL1 (`0x04`), and MIF/INT (`0x08`) omit direct clock names. The probe selects the required path for non-DVFS `CLKCTRL` devices and the absent-only optional path otherwise. A present empty, malformed, or provider-deferred list remains an error.

The helper starts and exits with an empty consumer state, zeroes its arrays, preserves OF/provider errors including `-ENODATA`, `-EILSEQ`, and `-EPROBE_DEFER`, and maps an unexpected NULL provider result to `-EINVAL`. On acquisition failure the probe returns before `pm_runtime_enable()`, `dev_set_drvdata()`, or publication in `g_hwdev_list`. It leaves successful `devm_clk_get()` references to the driver core's devres unwind on probe failure; successful removal calls the existing `npu_clk_put()` once. I found these ownership/error paths consistent with the pinned callsites and helper contracts.

The exact committed regression test reproduced the baseline poisoned-clock-slot failure with a deterministic `0xa5` host allocator. It then compiled the extracted pinned OF helper, clock helpers, probe, and remove functions under host shims. The shim is not the Linux driver core/devres implementation. This evidence does not include a kernel compile, actual provider deferral/reprobe, or device behavior.

## NPU mandatory stack blocker

The separate detector hash-verified the ordered inputs and reproduced the actual plain-apply result on temporary copies of pinned sources:

1. `npu-session-lifecycle-fix.patch` applies and retains the `lock_held`/`out_unlock` cleanup markers.
2. `npu-refcount-transaction-fix.patch` fails at `drivers/vision/npu/core/npu-vertex.c:1224` because its hunk expects the baseline `p_err_check`/`p_err` layout replaced by the lifecycle patch.
3. The callback and probe patches are not applied by this ordered stack test after that failure; the shutdown patch is not included.

The reverse order also fails at `npu-vertex.c:1188`. The detector rejects mutated patch/source context. Its three Python-mode runs passed by reproducing the blocker; a zero exit is detector success, not stack success. Its own note correctly marks the ordered stack not ready to build.

One test-claim limit remains open for follow-up: the “missing required patch” case runs `git apply --check` on a deliberately nonexistent patch path and confirms the file-open failure. It does not exercise an integration/series builder with the refcount patch omitted, so that individual negative check does not prove such a builder refuses omission. This does not affect the independently reproduced ordered lifecycle/refcount conflict, but the detector's missing-input coverage is not cleared pending the frozen precision fix.

The probe test separately checks the probe patch alone and a ref-transaction + PM-callback + probe application stack. That result omits the earlier mandatory lifecycle patch and does not override the blocker above. Reconciliation must preserve both historical changes and show the exact selected patch profile and its source/test coverage; silently dropping the conflicting hunk is not supported by this review.

The detector's temporary preflight returned status 2 with `artifact_preflight_pass=false`, `bootup_ready=false`, `bootup_authorized=false`, and `device_access=false`; the AIE and DSP relocation-rule fixtures were unavailable in that test root. This is a host refusal check only.

## ABOX IPC observation

The audio patch adds three opt-in trace events for queue attempts, local `abox_ipc_send()` returns, and selected non-backend RDMA pointer-handler messages. The event fields contain routing/result metadata only. The IPC wire header and payload definitions are unchanged; the correlation sequence is stored in the internal queue entry and is not copied into the IPC message. Queue insertion and later sender result remain distinct, direct synchronous sends are not presented as queued sends, and the pointer value is neither emitted nor used as a correlation token. The trace markers do not claim firmware acknowledgement or DMA progress.

Source review covered every `abox_ipc_queue_put()` and `__abox_process_ipc()` callsite in the pinned ABOX sources. The worker path carries the local sequence to the sender marker; the direct atomic+synchronous path passes zero. Disabled events avoid sequence/timestamp work, the queue slot clears stale sequence metadata on reuse, and the RDMA marker follows channel selection and the backend exclusion. The pinned Makefile places `abox.o` and `abox_rdma.o` in `snd-soc-samsung-abox.o`, so both need to be rebuilt together.

`struct abox_ipc` is embedded in the exposed `struct abox_data`. Although the wire payload is unchanged, changing this internal layout can affect genksyms CRCs for exported interfaces that expose the containing type and can affect other consumers. A whole-composite rebuild alone does not establish compatibility with every loaded consumer. Before any module/image deployment, verify the exact artifact's `MODULE_VERSION`, relevant symbol CRCs, loaded-source identity, and all consumers. No matching build artifact exists in this review.

The host test applies the patch to four hash-verified pinned fixtures and executes extracted queue, worker, request, send, and RDMA functions. Its trace shim executes the patch's real `TP_fast_assign` statements, and all nine scenarios compile at C `-O0` and `-O2`. It does not compile the kernel tracepoint macros through Kbuild or prove runtime tracefs availability, event ABI, or audio behavior.

## Executed host checks

I ran each frozen test from an archive of its author commit, with the derived source tree configured as shown. All commands exited 0. The NPU stack test's passing result means the expected blocker and negative cases were detected.

| Frozen test | Python invocation | Result |
|---|---|---|
| `test-npu-probe-unwind.py` | normal, `python3 -O`, `PYTHONOPTIMIZE=1` | Pass in all modes; baseline repro and patched extracted C passed at C `-O0`/`-O2`; standalone and refcount + callback + probe apply checks passed. |
| `test-audio-ipc-observation.py` | normal, `python3 -O`, `PYTHONOPTIMIZE=1` | Pass in all modes; nine extracted production-path scenarios passed at C `-O0`/`-O2`. |
| `test-npu-candidate-stack.py` | normal, `python3 -O`, `PYTHONOPTIMIZE=1` | Pass in all modes as blocker detection; ordered apply failed at `npu-vertex.c:1224`, reverse order at `:1188`, and BOOTUP remained refused. |

All three used `/home/corpunum/s22-workers/camera-kernel-build-20260927` as the source fixture. The probe/stack tests used `S22_NPU_PROBE_SOURCE_TREE`; the audio test used `--source-tree`. Source evidence stays host-only.

## Disposition

No blocking source defect was found in the isolated probe-unwind or ABOX observation candidate within the paths exercised and reviewed. Keep both classified as source/host-tested candidates only. The mandatory NPU patch series remains blocked by the exact lifecycle/refcount apply conflict, with later callback/probe/shutdown integration unproven. NPU shutdown ownership/error handling is not reviewed here. No NPU BOOTUP, deployment, runtime, DMA, audio output, or physical acceptance is authorized or established by this receipt.
