# Audio kernel-log coverage correction: independent review

**Disposition: PASS_LIMITED.** The correction closes the prior initial-observation fail-open for empty, unavailable, incomplete, and type-confused kernel-log classification data. I independently reproduced acceptance with the pre-fix observer, then executed the pinned rendered collector through the corrected validator and observed rejection. The new one-shot integration test also confirms that this failure cannot produce an observation-success receipt and leaves the temporary guard marker `unknown`.

This is host-only evidence. It neither authorizes an operation nor establishes boot, stable runtime, full-boot log coverage, TrustZone progress, or audio hardware acceptance.

## Frozen inputs and scope

The correction is author commit `473de37bb9590bee05e3173a961ff42e6841e37b`, imported as `6028839b9fda74b283bac172935fc5abbffeb4d1`. The review worktree was clean at `d06bb09bcb69fd1418863ae000181afa66fa1938`; the imported correction is its ancestor, and the audio observer/deployment/readiness/classifier/guard source files have no later diff from that import. The previous BLOCKED review is retained and was not edited. The correction changes only the observer's log gate/export/redaction, its tests, and the research note.

## Counterexample and correction

I loaded the pre-correction observer source from the import's parent and executed its rendered pinned readiness collector with successful empty `dmesg` output; local HTTP and non-dmesg subprocess probes were disabled by test stubs. The real classifier returned `assessment="incomplete"`, `capture_complete=true`, `coverage_complete=false`, no fatal indicators, `liveness_unresolved=false`, and `serious_fault=false`. The pre-fix validator accepted that snapshot.

I then executed the current rendered collector on the same empty output. The corrected `_readiness_script()` verifies its pinned collector has exactly one JSON output marker and inserts a fixed expression at that boundary to serialize `log_classification.input_available`; the test does not fabricate that value. The actual output included `input_available=true` while retaining the classifier's `assessment="incomplete"` and `coverage_complete=false`. The current validator rejected it with `kernel diagnostic input/capture/coverage is unavailable or incomplete`.

The corrected gate at `tools/hardware/audio-coherent-recovery-reboot-once.py:171-189` requires exact boolean `true` for `input_available`, `capture_complete`, and `coverage_complete`; an empty fatal-indicator list; exact integer zero hung-task and call-trace counts; exact boolean false unresolved liveness; exact string assessment `no_indicators`; and an exact boolean `full_boot_log_coverage`. The current collector's full-boot flag remains false. The observer preserves either boolean value in the redacted result without promoting current-ring coverage into full-boot coverage.

`trace_only` remains operation-specifically insufficient, not a fatal classification: an actual rendered `Call Trace:` sample yields `assessment="trace_only"`, one call-trace line, and an empty fatal-indicator list, then fails the `no_indicators` readiness gate. No TrustZone thread-name allowlist or progress claim is introduced.

## Failure and unchanged safety boundaries

The 28-case suite includes a one-shot end-to-end fixture: it issues one guarded reboot request through the injected fake transport, supplies the rendered empty-log snapshot to observation, then checks that there is no success observation receipt, an `UNKNOWN` failure receipt exists, and the temporary observation marker is `unknown` rather than successful. The existing unknown reboot outcome remains non-retriable.

The correction does not change the image/module identity checks, shared flash renderer, readback receipt validation, deployment/reboot lock or marker implementation, or the one-request reboot sequence. Comparing the correction import with its pre-fix parent shows the observer readiness predicate, collector export, and diagnostic redaction as the production changes; current HEAD adds no later audio-path changes. Source-pin tests pass. The inactive production gate remains `CURRENT_AUDIO_EXECUTION_AUTHORIZED=False` with an empty trial allowlist. Four default forward/reverse deploy and reboot/observer plans exited zero and report no authorization, operation marker, reboot, or audio acceptance. The reserved trial receipt directory and eight real marker paths remain absent.

The regression matrix exercises missing/type-confused availability, capture, coverage, assessment, and full-boot fields. I also independently tested both count fields (`hung_task_warning_count`, `call_trace_count`) with each of missing, `False`, `True`, `0.0`, `"0"`, and nonzero integer `1`; all 12 controls were rejected. Exact `bool` and `int` checks therefore remain effective under the tested Python modes.

## Verification and limits

The 28-test host suite passed in all three requested modes:

- `AUDIO_EXPECT_PYTHONOPTIMIZE=0 python3 -B tools/hardware/test-audio-coherent-trial-adapter.py` — 28/28, `sys.flags.optimize=0`.
- `AUDIO_EXPECT_PYTHONOPTIMIZE=1 python3 -O -B tools/hardware/test-audio-coherent-trial-adapter.py` — 28/28, `sys.flags.optimize=1`.
- `AUDIO_EXPECT_PYTHONOPTIMIZE=1 PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-audio-coherent-trial-adapter.py` — 28/28, `sys.flags.optimize=1`.

The rendered collector tests stub `dmesg`, local HTTP, and subprocess probes and use fixture state; rendered deployment tests use a temporary fake filesystem. These do not exercise SSH, the actual block provider, hardware, or a real new boot. No phone/device access, SSH, partition write, reboot, PCM test, kernel build, or package/repack action was performed.

The observer is still one initial post-reboot sample, not a stability interval. The research note correctly leaves a separate, future, owner-authorized and independently reviewed ten-minute read-only observation (11 samples, seconds 0 through 600) unimplemented and unauthorized. Full-boot diagnostic coverage needs an independent coverage source; TrustZone progress needs a separate relevant progress signal. Neither is established here, and no audio-hardware acceptance follows from this PASS_LIMITED review.
