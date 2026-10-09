# Audio coherent trial adapter: independent review

**Disposition: BLOCKED.** The frozen adapter has a fail-open diagnostic-coverage gate in its initial post-reboot observer. A successful but empty `dmesg` capture is classified as `assessment="incomplete"` and `coverage_complete=false`, yet the adapter accepts that snapshot as ready. The observer can then persist an `initial-observation-complete` receipt with incomplete kernel-log coverage. No device state was sampled for this review; the empty log is a source-derived host counterexample, not a claim about the phone.

## Scope and frozen inputs

Reviewed the exact adapter addition at author commit `a3f9349569660c1f9993f2a31a53b2d6cb66c881`, imported unchanged as `d48e3d9d40dd46bb909d0a8d417ab3460eea6bf0` on base `c92dd13` in the isolated worktree. The import was clean before this review; no `AGENTS.md` was present under `/home/corpunum/s22-workers`. The author change adds only these five paths:

- `tools/hardware/deploy-audio-coherent-recovery.py` — SHA-256 `859047a5018d697e510ef77855c5f841b1ef2e8890fb9890d47ba8dcb2a4aa66`
- `tools/hardware/audio-coherent-recovery-reboot-once.py` — `70dfccca7b4a36902c5f1ab8de2400c7f1dec641b59e6d3b78e0eae19cb8043d`
- `tools/hardware/test-audio-coherent-trial-adapter.py` — `9c106d7d48459a07d6c53ac8de7055641ee20883ae44874ae9c4da04211684b5`
- `docs/research/AUDIO_COHERENT_TRIAL_ADAPTER_2026-10-03.md` — `b9da3a8a8bfcbc2f956d336ad57eb8728cfb9f26875a7d02d8cbdb13c8cf1771`
- `evidence/s22-audio-coherent-trial-adapter-host-20261003.json` — `9bf1f970b01be9700df37b911a91abbbf4f5468cdfffb21ea82b75e084f0da46`

The adapter's source pins matched the repository inputs, including the currently reviewed shared flash helper `deploy-audio-recovery.py` at SHA-256 `4138413c16b9c90c70a46da6ed3b4f60b0f41f1c6c69b4b2047421895903a47f`. The independent evidence JSON records the other pinned helper digests.

## Blocking finding: incomplete kernel-log coverage is accepted

The pinned snapshot producer in `tools/hardware/audio-recovery-reboot-once.py:122-131` sets `capture_complete=true` when `dmesg` exits successfully and is within the size cap, then classifies the captured text. The classifier at `tools/hardware/trustzone_log_classifier.py:144-162` requires nonempty text for complete coverage. Thus a successful empty output is available input, but has `assessment="incomplete"` and `coverage_complete=false`. The producer at `audio-recovery-reboot-once.py:232-244` still emits `serious_fault=false` for that case (input was available and no fatal indicator was found), and includes the incomplete-coverage flags in its JSON.

The audio adapter's `capture_readiness_snapshot()` at `tools/hardware/audio-coherent-recovery-reboot-once.py:194-214` only renders the pinned snapshot script, parses its JSON, and returns it. It does not call the stricter standalone `AUDIO.validate_snapshot()` in `audio-recovery-reboot-once.py:445-454`, which requires both `capture_complete` and `coverage_complete` to be exactly true. Instead, the adapter's `validate_readiness_snapshot()` at `audio-coherent-recovery-reboot-once.py:169-175` checks only `serious_fault is false`, an empty `fatal_indicators` list, and `liveness_unresolved is false`; it ignores `capture_complete`, `coverage_complete`, and the classifier's `assessment`.

I independently executed the pinned classifier on `""` with `capture_complete=True`, mapped its outputs through the actual collector's emitted fields, and supplied that otherwise-valid snapshot to the actual adapter validator. Result:

```text
input_available=true
assessment=incomplete
capture_complete=true
coverage_complete=false
fatal_indicators=[]
liveness_unresolved=false
serious_fault=false
REPRODUCED_ACCEPT_EMPTY_DMESG_WITH_INCOMPLETE_COVERAGE
```

This is relevant to the terminal result, not just a logging cosmetic: `observe_reboot_once()` calls that validator at `audio-coherent-recovery-reboot-once.py:466-467`, then writes `status="initial-observation-complete"` and completes the observation marker at lines 484-516 after its other identity checks. Its redacted diagnostics retain coverage flags at lines 300-309, so the receipt may visibly say coverage is false while still carrying the success status. This review does not assert that an actual phone's `dmesg` is empty.

Before any initial-observation success can be recorded, the adapter should reject absent, malformed, or incomplete diagnostic coverage (including the empty-log counterexample). Add a regression that passes the exact classifier-derived empty-log fields through the adapter validator and expects rejection; retain strict JSON boolean checks.

## Other reviewed boundaries

No second blocker was demonstrated in the reviewed host scope. The profile definitions preserve the forward `b104…` camera-baseline to `6b788…` audio-candidate mapping and reverse mapping, with the expected direction-specific three loaded audio-module GNU build IDs. The adapter checks the pinned `/dev/sda16` recovery identity, exact 100,663,296-byte target, manifest/source hashes, boot/kernel identity, and module role before the relevant operation. The stage/flash adapter pins the shared renderer and boot-bound flash renderer; flash receipts bind stage/prewrite/raw receipts, same-boot postwrite identity, and exact result values/types. Flash does not reboot and does not claim audio acceptance.

The independent tests execute the exact rendered shared remote stage/flash body against a temporary fake filesystem and controlled transport. In those fixtures, forward and reverse staging cause zero modeled partition-write bytes; flash requires a full modeled readback before a success receipt. The bounded readback-corruption control returned `flash failed; remote outcome may be unknown; never retry`, left no success receipt, set the guard outcome unknown, and made reboot preflight fail before transport. This is useful host logic evidence, not evidence about SSH, the kernel block provider, real partition behavior, or flash durability on hardware.

The reboot adapter requires the bound flash receipt and marker, checks the existing boot ID and before-role audio module IDs, and delegates to the pinned boot-ID-guarded native `s22-reboot recovery` helper once. An ACK is not treated as proof of boot; ambiguous disconnects remain `UNKNOWN` and are never resent. The observer requires a changed boot ID, RECOVERY record, full target image hash, pinned kernel build ID and target-role module IDs, native/model/filesystem/network/power readiness, and it records only one initial snapshot. Pi/tmux/browser status is recorded but is not treated as universal readiness. None of these checks turns the observer into audio-hardware acceptance or a stability interval.

Execution remains inactive by default: `CURRENT_AUDIO_EXECUTION_AUTHORIZED` is literally false and the allowlist is empty (`deploy-audio-coherent-recovery.py:27-31`). CLI intent and acknowledgment text do not authorize execution (`:134-140`; reboot CLI `audio-coherent-recovery-reboot-once.py:550-573`). I confirmed that setting similarly named authorization environment variables did not override the guard: stage and reboot CLI attempts both exited 1 with `refused: audio RECOVERY execution is not currently owner-authorized`. The reserved trial receipt directory and all eight direction/operation marker paths were absent after review. The four local default plans exited 0, report false authorization/no marker/no reboot, and validate the forward/reverse image mappings; plans are not permission to execute.

## Host tests and limits

The author suite was independently rerun in all three requested interpreter modes; each run passed 20/20 tests:

- `CAMERA_EXPECT_PYTHONOPTIMIZE=0 python3 -I -B tools/hardware/test-audio-coherent-trial-adapter.py` (`sys.flags.optimize=0`, `isolated=1`)
- `CAMERA_EXPECT_PYTHONOPTIMIZE=1 python3 -O -I -B tools/hardware/test-audio-coherent-trial-adapter.py` (`optimize=1`, `isolated=1`)
- `CAMERA_EXPECT_PYTHONOPTIMIZE=1 PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-audio-coherent-trial-adapter.py` (`optimize=1`, `isolated=0`)

Those cases cover both profile directions, rendered stage/flash behavior, malformed/type-confused/replayed receipts, baseline/module/boot mismatches, partial readback and unknown transport, one-shot reboot handling, and inactive authorization. They do not cover the incomplete-coverage validator defect found here. All filesystem/transport mutation in the suite is confined to temporary fixtures; the real trial receipt/marker paths remained absent.

No SSH, phone/device access, partition write, reboot, PCM/audio test, kernel build, or package/repack action was performed. No hardware/provider behavior, actual flash readback durability, new-boot readiness, or audio acceptance is established. Preserve the existing closed/not-accepted trial state and inactive authorization. This review is **BLOCKED** on the source-level observer gate above; passing host fixtures do not clear it.
