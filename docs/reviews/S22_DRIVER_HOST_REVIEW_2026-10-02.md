# S22 driver host review — 2026-10-02

## Result and scope

Independent review of the integration range `d5532950994fa4cc1444051ffcb90cc6394912b7..8246b2f189b0c2790e2b68b3f3a4b4ff002b8908`, including the Pi observer, Bluetooth source pin, audio profile, bounded TrustZone collector, NPU transaction candidate, and the explicit host-test allowlist.

The Pi, Bluetooth, audio, and TrustZone host changes pass the focused checks below. The TrustZone collector is approved only for one bounded read-only capture through the sealed USB helper; that is not TEE progress or hardware acceptance. The NPU helper/caller patch and its host tests are reviewed as a source-only candidate, but NPU BOOTUP remains refused. Known callback and higher-level shutdown error gaps make this unsuitable for deployment. A later, separate NPU PM-callback patch is outside this review.

No phone, SSH, ADB, service, deployment, reboot, kernel build, or NPU BOOTUP action was performed by this reviewer. The exact pinned kernel source tree was inspected read-only.

## Changes reviewed

- Pi readiness change `09125b94c74dc4a8474f985602480f99a3d5bd17` excludes only the exact dedicated web-session Pi process after bounded command-line parsing and identity checks. The tmux query change uses the actual rendered `session:pane_pid` format; the executed test checks the rendered query, socket, and unprivileged read-only invocation.
- Bluetooth adapter pin `512a1c3b0910922f791d8871096865a6b07d6a39` matches the reviewed observer source fingerprint (`5ec8fec300d514ef9e586dfe5635ca38995718acd0437caefe50c34358bd1ac4`).
- Audio profile changes `0c681a945c21c3d5cb43f27f31a056aeaa32c307`, `4fecb1c78d0474f84ce42450c203d4c4c7d50794`, `462785d36a37eb6df18a58937d2b3ee8e15ddf5d`, and `5ea67c86485e1012eac044f43e044e3c873327a4` keep historical policy distinct from current policy, bound file/stdin summaries to 65,536 bytes, and constrain trial IDs to short public-label syntax. The labels are not a credential-redaction guarantee; publication remains manually allowlisted. The audit checks actual preprocessing of the pinned audio sources, not a full ABOX build or runtime trace.
- TrustZone collector commits `249f0dc3be7acf1758e5f0bd48930b23863b335b`, `c003ee9a4ef32e19d4874f61cf4a1a07b30563cf`, and `4f52e0e1042e4884ce6b8ad6b3dcb8117cf34144` use lazy `os.scandir` budgets, capped read-only procfs reads, a fixed two-sample window, and separate source/artifact roots for the sealed SSH helper. No source-root fallback or host-key copy was added.
- NPU transaction candidate `14dac074a928c5ffb41e86dfefc81866f23c8ea5` plus caller-lock fix `8246b2f189b0c2790e2b68b3f3a4b4ff002b8908` serialize reference transactions, propagate errors, poison uncertain final teardown, preserve parent dependencies after partial leaf boot failure, and use source-specific pre-STM aborts. The normal-boot error path now unlocks `vertex->lock`; timeout bypasses that locked-error label.
- Host CI allowlist update `78975709717d7e338c72b8a00c2f0a754f36c6c3` adds the NPU, audio-profile, and TrustZone tests as explicit reviewed paths.

## Executed evidence

| Focused check | Result |
| --- | --- |
| Pi readiness | 13/13 in normal, `-O`, and `PYTHONOPTIMIZE=1` |
| Agent web launcher | 10/10 normal; optimized import deliberately refuses execution and is excluded by the host runner |
| Audio observer, including rendered tmux query | 31/31 in all three Python modes |
| Bluetooth one-shot host regression | 31/31 in all three modes; the one-shot runner deliberately refuses optimized Python |
| Audio capture profile | 9/9 in all three Python modes |
| TrustZone fake-procfs and sealed-wrapper tests | 17/17 in all three Python modes |
| Host-regression-runner policy | 7/7 in all three Python modes |
| NPU transaction harness | Expected baseline reproductions; patched functions plus normal-boot caller pass. C builds pass at `-O0` and `-O2` under normal, `-O`, and `PYTHONOPTIMIZE=1` Python invocation |

The NPU harness fetches no mutable source when given the inspected clean local tree. Its source provenance check confirmed derived tree `3fca50941422439b2019db2e4a3dc1016b2138a1` has unchanged NPU files versus pinned base `4e5c5ad7d950e4de0688b5663965f2075654b2ad`, with these hashes:

- `npu-hw-device.h`: `43165437c7b6a4c50599c2677536376ab31579de0f5866c8b76e33ff7813e9c3`
- `npu-hw-device.c`: `14617a6f8e5b08e1bb169618daa8544f2680ad6709cb9f3b9730919d4dc8e16f`
- `npu-vertex.c`: `0e130ccedaebab85b2d6e78453a049610abed431c04ab630e4426a0f7aca077a`

The three-file patch passed `git apply --check --whitespace=error-all` against that clean source tree. The tests apply it to temporary pinned-source copies and compile extracted production C functions with pthread/device-service shims. They do not build or run a kernel. The coordinator separately reports the full allowlisted host suite passed 33 normal and 30 optimized checks, with the three deliberate normal-only exclusions; I did not rerun that entire suite.

## Read-only capture and interpretation limits

The first coordinator TZ capture attempt stopped before SSH because the isolated source worktree was incorrectly supplied as the helper artifact root. The corrected route was reviewed and tested before the coordinator performed one distinct, bounded read-only capture; the failed attempt marker was preserved. The capture reported complete enumeration, identity, and counters over 2.01 seconds, with eight worker, one iwlog, and one chub-log task pair. No scheduler or context-switch deltas were observed; the expected wait-stack pattern was not matched. This does not establish task progress, TEE request completion, or liveness. Those fields remain `unknown`, and the historical camera disposition is preserved as `not_accepted_preserved`.

Separately, the coordinator reports a read-only audio config/trace-metadata snapshot on the same boot and kernel build: `CONFIG_DYNAMIC_DEBUG=n`, `CONFIG_DYNAMIC_DEBUG_CORE=y`, and `CONFIG_DEBUG_KERNEL=y`; no matching `CONFIG_SND_PCM_XRUN_DEBUG` entry; and a tracefs directory without the queried metadata files or visible hwptr event. This narrows the captured config only. Effective per-module debug settings, complete translation-unit coverage, and runtime logging behavior remain unknown; trace-buffer and memlogger payloads were not opened.

The collector does not read TrustZone payloads, logs, or trace buffers, invoke SMCs, or write device state. Its passing fake-procfs tests establish host behavior only. No warning-setting change, deployment, or hardware acceptance follows from this capture.

## Remaining NPU limitations

- The pinned `npu_hwdev_default_boot()` callback can overwrite a failed `pm_runtime_get_sync()` result with a successful clock-enable result and publishes active status regardless of earlier failure. The transaction layer cannot poison an error the callback hides.
- Secure and normal bootdown still discard `npu_hwdev_shutdown()` errors and then update their software session counts; secure-bootup memory-failure cleanup also discards its shutdown error. Recovery close converts a propagated recovery-shutdown error into `BUG_ON(1)`. These caller behaviors remain outside this candidate.
- Host C tests do not establish Linux mutex/lockdep behavior, PM/clock effects, callback failure atomicity, firmware/STM/MMIO behavior, or device state.

Accordingly, this review clears the current NPU change only as a host-tested source candidate with explicit limitations. It does not clear NPU BOOTUP, kernel deployment, or runtime use. The later PM-callback patch requires its own review.
