# Audio stability and camera logging correction review — 2026-10-03

## Independent verdicts

Audio bounded observer: **BLOCKED** for a stability claim. The host-only suite and both inactive CLI plans pass, but a source-pinned synthetic transport can replay the same valid remote state at host offsets 0, 90, and 180 seconds and the observer records `bounded-stability-observed`. The code establishes separate host query attempts and host-clock spacing; it does not establish that returned remote samples are fresh.

Camera logging correction: **PASS_LIMITED**. The narrow `mwarn(format-only)` to `warn(format-only)` correction matches the pinned logging macro definitions, and the actual extracted C regression accepts the corrected call and rejects the old call. This is host C evidence, not an ARM64 frontend rerun, kernel build, camera quiescence, or hardware acceptance.

## Frozen input and review scope

Reviewed the clean worktree `/home/corpunum/s22-workers/audio-camera-review-20261003`, branch `codex/s22-audio-camera-review-20261003`, HEAD `da92be3561449632c7a94e6ca198c218a47cda10`, based on `da92be3561449632c7a94e6ca198c218a47cda10` before adding this review. The audio change is commit `da92be3561449632c7a94e6ca198c218a47cda10`; the camera logging correction is `c9119f4c1256b057b8f3af5edb3e9552d69f37dd`.

This review used only pinned source, synthetic fake-filesystem fixtures, fake clocks/transports, read-only source inspection, and local default CLI plans. No phone, SSH, ADB, USB, production marker, kernel/module build, package/image repack, inference, or live service was contacted or changed. The full ARM64 camera frontend receipt was inspected as author evidence but its eight jobs were not independently rerun.

## Audio observer findings

### Blocking freshness counterexample

The source does reissue the readiness and full-hash identity commands for each pair. The readiness collector also reads `/proc/uptime`. Those facts establish separate command attempts. They do not bind the returned JSON to a new remote capture: the acceptance path only checks that each readiness `uptime_seconds` is at least 180 (`tools/hardware/audio-coherent-recovery-reboot-once.py:1041-1071`); sample offsets and `stability_seconds` are derived from the local monotonic clock (`:1147-1197`). `_sample_record` does not preserve the remote uptime in its receipt (`:796-817`). There is no requirement for uptime to advance between samples and no remote capture nonce/timestamp.

I independently replayed the same valid readiness object and the same valid full identity object through the source-pinned synthetic transport. Each readiness object reported remote uptime `240.0`; host readiness-query starts were at `0.0`, `90.0`, and `180.0` seconds. The result was `status=bounded-stability-observed`, `stability_seconds=180.0`, and `sample_count=3`. The synthetic fixture made three readiness and three identity calls, then one initial reboot fixture call; it made no device connection. Therefore the current code cannot distinguish three new captures from a cached/replayed response and the result receipt cannot independently evidence remote observation duration.

The production collector source does execute a fresh remote script and read `/proc/uptime` each time, so this review does not claim that the live SSH path actually cached a response. It identifies that the observer accepts stale/replayed data if returned, and does not record enough remote evidence to rule it out. Before treating the result as a remote stability interval, require finite same-boot remote uptime to advance consistently across accepted samples, require the remote uptime difference to cover at least 180 seconds as well as the host-clock interval, and persist those exact per-sample uptime values. An unchanged quiet `dmesg` fingerprint is valid and must not itself be treated as a duplicate.

### Guards, identity, faults, and gates

The production adapter remains inactive: `CURRENT_AUDIO_EXECUTION_AUTHORIZED` is literal `False`, the allowlist is empty, and `require_execution_authorized` checks both before dispatch. Direct forward and reverse default plans exited 0 and reported authorization, execution, reboot, stability execution, writes, and acceptance as false. These plans validate the host artifacts; they are not runtime evidence.

The prior successful initial observation is revalidated against the profile image, kernel ID, modules, readiness, and diagnostic gates. For an acknowledged reboot, the reboot marker binds the exact result path and SHA-256. For an `UNKNOWN` reboot outcome, the unresolved marker remains unmodified and the follow-up makes only read-only calls; the guard correctly refuses new guarded operations while any marker is unresolved. The `UNKNOWN` marker stores no result digest, and the observer checks its trial/kind/status rather than binding the exact unknown result bytes to that marker. This is an audit-link limitation in the read-only branch, not a path to device mutation in this inactive adapter.

Consumed stability paths and markers are exclusive; the tests refuse old receipts and consumed markers without overwriting them. Startup and later boot changes, wrong image/module IDs, fatal indicators, hung-task reports, exhausted transport, timeout, nonprogressing host clock, and later gate regressions fail closed. `trace_only` is not promoted to fatal, but cannot start or satisfy a stability sample. Empty/incomplete logs fail the readiness gates. Valid JSON with an incomplete readiness gate may receive the one bounded later startup poll; malformed JSON/identity responses and terminal contradictions become `UNKNOWN`. Application checks cover native readiness, idle model slots, local model health, desktop readiness, Wi-Fi state, battery/thermal coverage, pinned RECOVERY boot identity, kernel ID, and exact profile module IDs. The observer does not request inference or assert audio hardware acceptance, full-boot log coverage, TrustZone progress, or camera quiescence.

The 600-second monotonic deadline and per-query remaining-time caps are enforced. The documented 505-second maximum query/wait schedule is conservative: up to 188 seconds for the startup uptime probes and up to 317 seconds through startup readiness and the final pair, including the single worst-case reconnect. The remaining 95 seconds are only a local-work allowance; the hard 600-second deadline remains authoritative. This budget check does not repair the remote-freshness gap.

### Additive module-role interpretation correction

The author receipt `evidence/s22-audio-bounded-observer-host-20261003.json` lists `identity_binding.expected_profile_module_ids` as:

| Profile | Values recorded there | Role those values represent | Stability target role and IDs |
| --- | --- | --- | --- |
| `audio-forward` | ABOX `34a5354a75980688ee7dbeb6a848e04a7d54558f`; rainbow `510b984887b640ad4ad3c1e9a556ef16c30b38c9`; offloader `4f35c50b0eca0d22b2060f7b8d84f03359feacd1` | Prewrite baseline (`before=True`) | Candidate: ABOX `26347c3373e155fa6badf7883ff162f1d9f6723f`; rainbow `8a7227b58cb7f4faf73ea92974781d34870bbfac`; offloader `8c9b0d4787ea32eae7de0086a4d662b0f351675d` |
| `audio-reverse` | ABOX `26347c3373e155fa6badf7883ff162f1d9f6723f`; rainbow `8a7227b58cb7f4faf73ea92974781d34870bbfac`; offloader `8c9b0d4787ea32eae7de0086a4d662b0f351675d` | Prewrite candidate (`before=True`) | Baseline: ABOX `34a5354a75980688ee7dbeb6a848e04a7d54558f`; rainbow `510b984887b640ad4ad3c1e9a556ef16c30b38c9`; offloader `4f35c50b0eca0d22b2060f7b8d84f03359feacd1` |

The observer code is correct: `_expected_profile_modules` calls `expected_profile_modules(profile_name, before=False)` (`tools/hardware/audio-coherent-recovery-reboot-once.py:169-170`), and each stability identity is validated against those target IDs (`:851-858`). The role ambiguity is in the author receipt field name and mapping, not the runtime check. This review preserves that frozen receipt and supplies the postboot target map additively.

### Independent audio execution

All 35 host-only tests passed independently in each requested Python optimization mode: normal (`optimize=0`), `python3 -O` (`optimize=1`), and `PYTHONOPTIMIZE=1` (`optimize=1`). The suite uses temporary profile/guard/receipt roots and synthetic 4,096-byte fixtures; the production authorization remains false. The tests exercise the rendered collector, stage/flash fake filesystem, receipts, reboot guard, source pins, and the bounded observer. Stability success coverage is forward-profile only; reverse default-plan behavior and target role mapping were independently inspected, but a reverse synthetic stability interval was not executed.

## Camera logging correction findings

The pinned `is-common-config.h` defines `mwarn(fmt, object, args...)`, which dereferences `object->instance`, while `warn(fmt, args...)` is object-free. The changed `is_cleanup` line now uses `warn` with the same message; no clock, ownership, or cleanup logic changes in this frozen correction. Both host harness definitions now require the `mwarn` object parameter.

I independently ran all ten camera host cases in the same 0/1/1 optimization matrix. `test_pinned_logging_macro_rejects_objectless_warning` builds the corrected extracted C call under the actual pinned macro and confirms the old format-only `mwarn` call fails at both `-O0` and `-O2`. The configured private source matched commit `4e5c5ad7d950e4de0688b5663965f2075654b2ad`, tree `5c46cbe12dadbcdb64eec4344c9e8ff0f8a75dee`, and was clean when checked. The host test is not an ARM64 frontend or kernel build. The author frontend receipt records eight configured ARM64 syntax jobs passing after the fix; that receipt was inspected but not independently rerun.

The verdict is limited to this compile correction and its C regression. Existing camera concurrency, clock-provider behavior, DMA activity, external teardown, camera capture/quiescence, and hardware acceptance remain unresolved. The prior powered candidate remains `not_accepted`; no new device authority follows from this review.

## Executed verification

Audio commands, each reporting `Ran 35 tests ... OK`:

- `AUDIO_EXPECT_PYTHONOPTIMIZE=0 python3 -B tools/hardware/test-audio-coherent-trial-adapter.py`
- `AUDIO_EXPECT_PYTHONOPTIMIZE=1 python3 -O -B tools/hardware/test-audio-coherent-trial-adapter.py`
- `AUDIO_EXPECT_PYTHONOPTIMIZE=1 PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-audio-coherent-trial-adapter.py`

Camera commands, each reporting `Ran 10 tests ... OK`:

- `CAMERA_EXPECT_PYTHONOPTIMIZE=0 python3 -B tools/hardware/test-camera-sensor-clock-unwind.py`
- `CAMERA_EXPECT_PYTHONOPTIMIZE=1 python3 -O -B tools/hardware/test-camera-sensor-clock-unwind.py`
- `CAMERA_EXPECT_PYTHONOPTIMIZE=1 PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-camera-sensor-clock-unwind.py`

Both direct audio observer plans also exited 0 and printed `current_deployment_authorized=false`, an empty execution allowlist, `reboot_performed=false`, `stability_observation_execution=false`, `partition_written=false`, and `audio_hardware_acceptance=false`.
