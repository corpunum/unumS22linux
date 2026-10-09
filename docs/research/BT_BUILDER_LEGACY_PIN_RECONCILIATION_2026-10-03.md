# BT candidate-builder legacy runner pin reconciliation

**Verdict: `PASS_LIMITED` for the host-side legacy provenance gate and read-only preflight only.** The frozen builder rejected the current exact public legacy runner because its SHA-256 pin still named the runner from the original pinned source revision. The runner comparison shows a single-line pin refresh to a stricter receipt validator; the consumed trial identity, artifact/source pins, and one-shot/authorization guards are unchanged. I refreshed the builder to the exact current runner digest, without accepting the stale digest as an alternative.

## What was reconciled

The builder is based on source commit `79c539ea55a1663e946ed881606232519873fd93`, where the runner had SHA-256 `26bbe8bdb5ff687de29d5ce67f17d58cfa460a48fe9fc5be9043dccd54cf9253`. At the frozen review head, its current public SHA-256 is `7696d15fcdd7af33d130cb4cdfa78c823553d84a3eb2d830142c5bbd0a09198a`.

The runner diff from the builder's pinned source commit to the frozen review head is exactly one changed constant: the digest of `deploy-audio-recovery.py` it trusts. That helper's corresponding change tightens remote receipt validation: it requires the mode to be exactly a string and each expected JSON value to have the exact expected primitive type as well as value. This closes bool/integer equality ambiguity; it does not loosen acceptance. The runner's Bluetooth bridge digest, artifact digest/build ID, trial identity, explicit separate-controller authorization requirement, single-use receipt refusal, operation lock, and cleanup/proof flow are unchanged. No dual-hash compatibility branch was added.

The exact consumed public evidence remains SHA-256 `eb8f1963049cb4cdbca7236169bbdc91f2c929227ea995bdbb310972399518dc`. The builder still requires trial `bt-hci-plain-h4-20260927`, exactly one attempt, durable guard outcome `success`, and the original artifact SHA-256 `f4ba76613e1339314898ebbf067338d846ed9ee231907a44306b82f54f2f1684` / GNU build ID `a5be9451d95335ae2a5d292d721a766208f55a87`. The original bridge source pin remains `476f148246dfac8330f7ade2790627b64a38ee7b1cb4f713934b6d438864a7a4`.

## Checks and limits

The portable builder suite passed all 20 tests under normal Python, `python3 -O`, and `PYTHONOPTIMIZE=1`. The exact local private-input/compiler fixture was present: all three local-fixture tests ran and passed in every mode (none skipped). Added regressions show that the prior runner digest refuses the current runner, the exact current runner and consumed receipt pass, malformed receipt JSON is rejected, and a receipt claiming two attempts is rejected.

An isolated host `--check-only` completed with `preflight_only_no_compile`, AArch64 target, eight public inputs, two private-input checks, the exact new legacy runner pin, and one preserved consumed attempt. The candidate output directory existed before the check and still existed afterward; its presence state did not change. The preflight only queried compiler identity/dependencies and did not compile or write an artifact. Since the output directory was already present, this run does not demonstrate the absent-directory case, and it is intentionally left untouched.

No Bluetooth runner/bridge, SSH, ADB, USB, phone, or hardware operation was invoked. No private receipt was opened or copied; only the hash-pinned public evidence fields needed by the builder gate were checked. This is host provenance/preflight evidence only: it does not establish candidate-build success, controller behavior, Bluetooth transport functionality, physical cleanup, or hardware acceptance. The historical one-shot authorization remains consumed; this review does not authorize or repeat that trial.
