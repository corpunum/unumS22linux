# S22 NPU probe and ABOX trace source review — 2026-10-02

Status: independent source/host review of the frozen NPU probe-unwind and ABOX IPC-observation candidates, the stack-blocker detector, and a four-patch NPU refcount/lifecycle source profile. The historical unprofiled lifecycle/refcount sequence remains blocked at the baseline-only vertex hunk; the exact profile composes lifecycle, refcount, callback, and probe patches in host source tests. This is not a full NPU stack or deployment clearance. The separate NPU shutdown-ownership patch is excluded and not reviewed in this receipt.

No phone, SSH, ADB, kernel build, module load, firmware operation, BOOTUP, deployment, reboot, or hardware test was performed.

## Inputs and source identity

The pinned kernel base is LineageOS `android_kernel_samsung_s5e9925` commit `4e5c5ad7d950e4de0688b5663965f2075654b2ad`. Hash-verified source fixtures came from the clean derived tree at `3fca50941422439b2019db2e4a3dc1016b2138a1`.

Reviewed frozen inputs:

- NPU clock acquisition/probe unwind: author commit `bd3204afb92ab43c53138d654b2850b362401781`; integrated code is identical at `de837b823f6ba7a722056f3103e9d407976e0ea2`.
- ABOX IPC tracepoints: author commit `58dd9c8370f4f20ac9e7c32c6410ef4a2ceecd21`; integrated code is identical at `d16a7fd46f9265eb65ace1e849bed558c6dcd447`.
- ABOX host-test source portability: author commit `c89e208cb1de4dfcfbee7c5c7b518e993810410a`; corrected no-Content-Length body-cap coverage: follow-up author commit `8e673c54a1d7d1026be2a56923d68435debe80d9` (parent `c89e208`).
- NPU stack blocker detector: author commit `873894f1ad9139429192a6b215be05b06b9b0828`; integrated test/note are identical at `4c1131bf674d489321d331666457c968ce0f07ff`.
- NPU required-input negative-check precision fix: author commit `8c6e4dfd1d86e1d8778516fb100cd320a8bce76a`; integrated test/note are identical at `87bb76a1a23dc44e833aed8b79464f37ea351f69`.
- Four-patch NPU refcount/lifecycle profile: author commit `1c36220c47219853d18512723e6c20024a486be5` (on top of `a5baa605f56d7287c39ccfd5c00336f5311367ac`).

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

The initial detector's “missing required patch” case only ran `git apply --check` on a deliberately nonexistent patch path, which proved only the CLI file-open error. The frozen precision fix adds a separate negative case that invokes the actual `patch_paths(repo_root)` reader used by `main()` on a controlled temporary repository containing exact-hash lifecycle, callback, and probe patches but no refcount patch. It checks the specific required-input diagnostic before any source application; the old nonexistent-path case is now explicitly labelled CLI-only. This closes the detector missing-input coverage finding for that reader. It does not test a separate external integration builder that might consume a different manifest implementation.

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

## Follow-up: required-input precision and CI allowlist

I verified that the precision-fix commit's parent is the original detector commit, archived the frozen author commit, and confirmed its test and patch hashes. Its controlled missing-manifest case copied the three non-refcount patch files only after verifying their expected SHA-256 values, called the test's actual required-patch reader with the controlled repository root, and required the exact `required ordered patch is missing: npu-refcount-transaction-fix.patch` error. The separately retained missing-path CLI check reports only a `git apply` open-file failure. The test and research note at integrated commit `87bb76a1a23dc44e833aed8b79464f37ea351f69` match the frozen author versions.

I ran the archived `test-npu-candidate-stack.py` from author commit `8c6e4dfd1d86e1d8778516fb100cd320a8bce76a` with `/home/corpunum/s22-workers/camera-kernel-build-20260927` configured through `S22_NPU_PROBE_SOURCE_TREE`, under normal Python, `python3 -O`, and `PYTHONOPTIMIZE=1`. Each exited 0 and printed the new `PASS negative manifest-loader` result, the separate CLI-only diagnostic, the lifecycle/refcount apply blocker, reverse-order rejection, and BOOTUP refusal. These remain host-only detector results; no full stack was applied or built.

I also inspected CI commit `2c28c37acc67c12993ba5f79c2a703833b792de5` statically. Its only two changed files add exactly the three reviewed host scripts—NPU probe unwind, NPU candidate stack, and ABOX IPC observation—to the runner's fixed reviewed-path tuple, its fixed execution tuple, and the policy test's expected tuple. No discovery, caller-supplied script path, or live-device script was added. I did not run the CI suite or any live script; the coordinator reported its full-suite result separately.

## Follow-up: audio fixture portability

The portability change replaces the rig-only default with a fixed allowlist of four public source paths at the pinned commit. It bounds each file to 256 KiB, verifies the optional decimal `Content-Length`, reads at most `max_bytes + 1`, checks a declared response length when present, hashes the complete bytes against fixed SHA-256 values, and rejects a final URL different from the requested URL. The explicit `--source-tree` path is separate: it requires the exact derived HEAD and base commit, checks the pinned Git blobs and worktree files, and raises on missing/mismatched input instead of falling back to network. The audio patch itself is unchanged between `c89e208` and `8e673c5`.

The first portability commit's fake response used `headers or {"Content-Length": ...}`, so its `{}` test case silently gained a `Content-Length` and repeated the declared-header cap check. Its all-green runs did not cover the no-length body-cap branch. Commit `8e673c5` fixes the distinction (`None` means synthesize a length; `{}` means no headers), records reads, requires the header-cap rejection to happen before reading, and requires the no-header case to read exactly `max_bytes + 1` and fail through the body-cap diagnostic. I independently ran the corrected frozen archive; this closes the specific coverage finding against `c89e208`.

I ran the archived test from `8e673c54a1d7d1026be2a56923d68435debe80d9` in normal Python, `python3 -O`, and `PYTHONOPTIMIZE=1`, both with no `--source-tree` and with `/home/corpunum/s22-workers/camera-kernel-build-20260927`. All six invocations fetched/verified the public fixtures or verified the explicit derived tree, printed all eight offline loader checks, passed all nine C scenarios at C `-O0` and `-O2`, and applied the patch to temporary fixtures. Each exited 0. I also ran missing-tree, wrong-HEAD, and deliberately tampered-worktree inputs under all three Python modes; each rejected with exit 1 rather than skipping or falling back. The tampered tree was a temporary shared clone; the read-only derived tree was not changed. Manual checks of the frozen fetch function classified HTTP 429/503 as `SourceFixtureUnavailable` and HTTP 404 as `RuntimeError`.

The loader's environmental-fetch exception is reported by `main()` as `SOURCE_FIXTURE_UNAVAILABLE` with exit 77. It is not a successful test result; the explicit host runner treats nonzero child exits as failures and applies its own 180-second process timeout. My six public/explicit-tree runs completed successfully with no unavailable-fixture skip. `urllib.request.urlopen(timeout=5)` supplies a timeout for blocking operations such as connection attempts, not a strict five-second end-to-end deadline for a slow-drip response; standalone invocations therefore have no hard total-fetch deadline, while the host runner bounds the complete child process. [Python's `urllib.request` documentation](https://docs.python.org/3/library/urllib.request.html#urllib.request.urlopen) defines the timeout in terms of blocking operations. The offline suite covers a generic `URLError`; its HTTP status classification was separately smoke-tested here. Its positive exact-byte loader case uses a local fake response; public reachability was separately exercised by the six actual fetch-mode runs above.

## Follow-up: reconciled NPU refcount profile

I archived frozen author commit `1c36220c47219853d18512723e6c20024a486be5` and ran its exact `test-npu-reconciled-stack.py` with the pinned derived source configured by `S22_NPU_PROBE_SOURCE_TREE`. The profile patch contains only the canonical refcount patch's `npu-hw-device.c` and `.h` diff sections; the test byte-compares both complete sections to the original patch, confirms the original third section is the baseline `npu-vertex.c` unlock hunk, and checks that the profile explains it as superseded by lifecycle's `lock_held`/`out_unlock`. It applies the original C/H sections with an explicit `--include` only to a comparison fixture. The candidate four-patch series itself is applied without path filters, using `git apply --check --whitespace=error-all` and then plain `git apply --whitespace=error-all`; it does not use force, fuzz, or three-way application.

The candidate order is lifecycle, refcount profile, default-boot callback, probe-unwind. The test rejects missing and SHA-tampered profile inputs; its swapped-order test rejects lifecycle/profile reversal before changing any fixture bytes. It separately reapplies lifecycle followed by the unprofiled canonical refcount patch and preserves the expected failure at `npu-vertex.c:1224`. On the profiled path it snapshots `npu-vertex.c` after lifecycle and byte-compares it with the final tree after the other three patches, so the profile and later patches do not alter that lifecycle-owned caller. All of these checks passed in normal Python, `python3 -O`, and `PYTHONOPTIMIZE=1`.

The exact final refcount helper/callback extraction retained all 18 canonical C function bodies and the expected init-abort/probe registrations. Eleven actual extracted refcount C scenarios passed at both C `-O0` and `-O2`: concurrent first-reference failure/success, source-specific abort behavior, parent error propagation, partial child boot rollback, final-error poisoning, underflow, and distinct STM teardown/abort behavior. The tests are host pthread/atomic/service shims, not Linux mutex/atomic behavior. The 11 scenarios do not dynamically exercise the `INT_MAX` overflow or negative-count guards, although the exact canonical helper bodies are preserved and included in the comparison.

Five final extracted `npu_hwdev_normal_bootup()` caller C cases also passed at both C optimization levels: low-level hwdev error, vertex-ref acquisition error, POWER_NOTIFY-on error, secure-count timeout, and successful warm boot. Each asserts balanced lock/unlock counts, no invalid unlock, and mutex availability at return. The POWER_NOTIFY error case asserts that inverse POWER_NOTIFY, session unregister, and hardware shutdown are called, but does not assert their order or check the core `__vref_put` call count. The adapted service shims default to success for registration, restore/resume, STM, HWACG, and cleanup APIs; the harness does not cover their error branches or a non-warm successful path through STM/HWACG. Thus these five cases support the specified lock/error/timeout/warm-success paths but are not proof of every cleanup branch or reverse-order unwind.

The final host preflight still refused readiness and authorization. Its AIE and DSP-relocation-rule fixtures were absent in the archived test checkout; this says nothing about files or firmware on the phone. No kernel build, Linux mutex/lockdep validation, runtime, firmware operation, hardware, or inference was performed. The shutdown-ownership candidate is not part of this four-patch series and remains separately blocked/unreviewed.

## Disposition

No blocking composition defect was found in the exact four-patch source profile; it is cleared only as a host-tested WIP source composition for further review. The historical unprofiled lifecycle/refcount patch order still fails at `npu-vertex.c:1224`; the profile explicitly preserves C/H sections and leaves the lifecycle vertex bytes untouched. The caller harness's selected paths do not prove all cleanup call ordering or error branches. The separate shutdown-ownership candidate remains excluded and unreviewed, so this is not whole-stack, kernel-build, runtime, deployment, BOOTUP, DMA, audio-output, inference, or physical acceptance.
