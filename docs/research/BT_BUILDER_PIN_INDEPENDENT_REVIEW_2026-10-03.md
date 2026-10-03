# BT candidate-builder pin review — 2026-10-03

## Verdict

**PASS_LIMITED** for the exact legacy-runner provenance gate and the isolated
read-only builder preflight. The candidate builder accepts only the corrected
current runner digest, keeps the consumed public trial evidence and legacy
artifact identities pinned, and preserves the one-attempt state. The requested
20-test suite passed under all three Python modes. This does not establish a
candidate build, bridge execution, controller behavior, or Bluetooth hardware
acceptance.

I reviewed frozen HEAD
`4280f1fa5a6c7848af5c525a426464d0e856c1a8`, author correction
`5deeda5e0e753a92f836a4e07f8093f4014846fc`, the full builder and unittest
source, and the author reconciliation report/receipt.

## Legacy runner and consumed receipt

The builder's public source pin is `79c539ea55a1663e946ed881606232519873fd93`.
The historical one-shot runner reproduced from that commit hashes to
`26bbe8bdb5ff687de29d5ce67f17d58cfa460a48fe9fc5be9043dccd54cf9253`. The
current runner hashes to
`7696d15fcdd7af33d130cb4cdfa78c823553d84a3eb2d830142c5bbd0a09198a`. Its
complete diff from the pinned source is one line: the trusted deployment
helper digest changed from
`d0c54cc306e3a8a80cb79d79958fceedf6bb5945dabc7606e02b12d27dc00fd3` to
`4138413c16b9c90c70a46da6ed3b4f60b0f41f1c6c69b4b2047421895903a47f`.
That exact digest matches the current helper. The helper's source diff tightens
remote receipt validation by requiring exact primitive types as well as
values, including rejecting Python's bool/integer equality ambiguity. It
narrows acceptance.

The builder has a single `LEGACY_RUNNER_SHA256` value for the corrected
current runner. There is no stale-hash compatibility branch. The regression
passes with the exact current runner and rejects it when the in-memory pin is
changed back to the historical digest. Since the runner's only source change
from the pinned revision is that helper digest, its other trial, authorization,
receipt acknowledgment, consumed-identity, and no-retry logic remains
byte-identical.

The consumed public receipt
`evidence/s22-hardware-continuation-20260927.json` is tracked and has the same
SHA-256 at the pinned source commit and the frozen tree:
`eb8f1963049cb4cdbca7236169bbdc91f2c929227ea995bdbb310972399518dc`. The
builder and the exact-pin regression require the recorded trial identity,
exactly one attempt, durable guard success, the original bridge-source pin
`476f148246dfac8330f7ade2790627b64a38ee7b1cb4f713934b6d438864a7a4`, and the
original artifact SHA-256
`f4ba76613e1339314898ebbf067338d846ed9ee231907a44306b82f54f2f1684` / GNU
build ID `a5be9451d95335ae2a5d292d721a766208f55a87`. The consumed identity is
`bt-hci-plain-h4-20260927`, with `attempts_for_this_identity == 1` and
`durable_guard_outcome == "success"`. The builder also requires the new
candidate artifact location to be distinct from the consumed artifact. The
malformed-receipt and changed-attempt-count controls reject. No private raw
logs, trace, or firmware were opened or copied. The private headers were read
only by the gated hash and dependency scan; their contents were not published
or copied.

## Builder and check-only behavior

The complete `--check-only` path runs the same source, receipt, private-input,
compiler-identity, dependency-closure, resource, and output-directory gates as
preflight, then returns `preflight_only_no_compile`. Its dependency query uses
the cross-compiler `-M` mode; it does not invoke the static build command or
create objects, a link map, a dependency file, a build log, or a candidate.
`run_build()` is selected only by `--build-candidate`; it refuses an existing
output path and uses exclusive directory creation, preserving failed outputs
without a blind retry. The tests construct the fixed build command but never
execute it.

After reading the complete builder and tests, I ran the real isolated command
`python3 -I -S -B tools/hardware/build-bt-transport-candidate-20261002.py --check-only`.
It returned `preflight_only_no_compile`, verified eight public inputs and two
private-input checks, and preserved the consumed receipt. The candidate output
directory was already present with mode `0700` before the command and remained
present with that mode afterward. I did not inspect its contents. This is
evidence for the existing-output preflight case only; it does not demonstrate
the absent-output case. No attempt was made to rename, remove, or recreate the
directory.

## Independent tests

| Invocation | Python optimize | Tests passed | Skipped | Result |
| --- | ---: | ---: | ---: | --- |
| `python3 -B tools/hardware/test-build-bt-transport-candidate.py` | 0 | 20 | 0 | PASS |
| `python3 -O -B tools/hardware/test-build-bt-transport-candidate.py` | 1 | 20 | 0 | PASS |
| `PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-build-bt-transport-candidate.py` | 1 | 20 | 0 | PASS |

All three exact local compiler/private-input fixture tests ran in each mode.
The suite has no bare `assert` statements in the builder or test file, so the
runner's `unittest` checks remain active under `-O`. It covers the exact
current legacy pin, old-pin refusal, current consumed receipt, malformed JSON,
changed attempt count, clean child environment, redirected compiler/temp
environment refusal, source ancestry and selected-input integrity, and the
existing-output no-reuse guard.

## Scope and remaining limit

This review ran host checks only. It did not select `--build-candidate`, invoke
the BT bridge or one-shot runner, connect to a phone, use SSH/ADB/USB, or
exercise hardware. The prior one-shot trial remains consumed; this review does
not authorize another attempt. The candidate artifact is not claimed to exist
or to build successfully. The exact absent-output preflight/build case remains
unobserved here; the existing output directory was left unchanged. No CI run is
claimed by this review.
