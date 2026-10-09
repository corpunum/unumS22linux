# Independent NPU full-lifecycle source review — 2026-10-02

## Scope and result

Reviewed frozen author commit `2a7b84b3190c65fb58181fccb82a09d6b40fc2d1`
in `/home/corpunum/s22-workers/npu-full-profile-20261002` (parent
`2524ff036a6190bb4a22dc94606a2da5a739ebe4`). It was clean at initial
inspection; the author's later uncommitted portability follow-up is outside
this review. For the reproducible rerun below, the frozen test bytes were
verified and run from integration checkout `100856561e27eaa74c6c7016ef6c14e350e7f34d`.
The tested source fixture was clean at
`/home/corpunum/s22-workers/camera-kernel-build-20260927`, commit
`3fca50941422439b2019db2e4a3dc1016b2138a1`; its NPU driver inputs match the
pinned source base `4e5c5ad7d950e4de0688b5663965f2075654b2ad`.

No blocker was found in the bounded source-composition and host extracted-C
review. This is not a kernel-build, module, firmware, runtime, or device
acceptance result. The profile test requires an explicit local source fixture
through `S22_NPU_PROBE_SOURCE_TREE` and
`S22_NPU_SHUTDOWN_SOURCE_TREE`; this review does not establish a CI-default
source-fixture path or CI readiness.

## Independent verification

Ran from integration checkout `100856561e27eaa74c6c7016ef6c14e350e7f34d`
with both variables set to the exact fixture above, in all three modes. Before
and after the runs, the test SHA-256 was
`b98494686c27ea5174262feabb09eb32b8d4a0c7ad8ec067919e3b8606e573df` and the
profile-patch SHA-256 was
`b986e1896305fda55f1d702ed6f12dde646e4a84b3ce91009203b9d77b7a00e7`; both
matched `git show 2a7b84b3190c65fb58181fccb82a09d6b40fc2d1:<path>` before and
after execution.

```sh
S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
S22_NPU_SHUTDOWN_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
python3 -B tools/hardware/test-npu-full-lifecycle-profile.py

S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
S22_NPU_SHUTDOWN_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
python3 -B -O tools/hardware/test-npu-full-lifecycle-profile.py

PYTHONOPTIMIZE=1 \
S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
S22_NPU_SHUTDOWN_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
python3 -B tools/hardware/test-npu-full-lifecycle-profile.py
```

All three passed. Every run verified hashes/source equality and ordinary
`git apply --check` followed by ordinary `git apply` for the five ordered
patches: lifecycle, refcount, default-boot callback, probe-unwind, then the
new shutdown-lifecycle profile. The frozen shutdown-ownership patch hash
remained `a8af77122b4049fd38e21adfd01a8577d3e8f9bef1f68d0cfa091a5884a4d9f3`;
its independent ordinary `--check` against the four-patch prefix still fails
at `npu-vertex.c:313`. The test does not force or fuzz that patch. The profile
hash is `b986e1896305fda55f1d702ed6f12dde646e4a84b3ce91009203b9d77b7a00e7`.

The test confirms the four post-lock quarantine checks (secure/normal
bootup/bootdown) and two wait-reacquisition checks remain in the composed C.
Its injected-lock tests exercise all six late-uncertainty points. It also
confirms lifecycle close ordering: boot-reference/protocol teardown precedes
hardware shutdown, and session close follows only successful hardware
shutdown; a failed shutdown retains the session and latches uncertainty. A
`POWER_NOTIFY` error still completes the safe teardown sequence and is
returned afterward. Recovery shutdown failures now return and quarantine
rather than reaching the former panic path.

For each run, the harness compiled and ran the combined extracted driver C at
C `-O0` and `-O2`: the 11 refcount-transaction cases, five normal-bootup
lock-path cases, and 17 shutdown/recovery cases (the frozen 16-case set plus
the added `POWER_NOTIFY` close-order case). The BOOTUP preflight remained
refused with status 2, `artifact_preflight_pass=false`, `bootup_ready=false`,
and `bootup_authorized=false`; the required firmware fixtures were absent.

## Evidence limits

The executable harness compiles selected final driver functions extracted
from the patched source with host-provided structs, synchronization and
hardware/session/memory shims, using `-Wall -Wextra -Werror`. This gives useful
control-flow and error-order evidence, but does not reproduce Linux locking,
VFS/session-manager/remove lifetime, firmware, hardware callbacks, or kernel
build behavior. The pinned shutdown API still masks errors from early/protocol
close paths, and the normal-bootdown reference/count mismatch remains open.
No phone, SSH, ADB, module, package, service, reboot, kernel build, or push
operation was performed.

## Portability and fixed-CI follow-up review

Reviewed frozen follow-up `204973c6a310f92f2810db129a13a07a2e29d263`
(parent `2a7b84b3190c65fb58181fccb82a09d6b40fc2d1`) in the clean author
worktree. It changes only the profile test and its research note; the lifecycle
patch remains byte-identical at SHA-256
`b986e1896305fda55f1d702ed6f12dde646e4a84b3ce91009203b9d77b7a00e7`. The
follow-up test SHA-256 is
`a4c84821b8d28efc8056c6033645839bda808a79a7bebed13585c52dd6d16a59`, matching
the test blob at both the author commit and CI commit `5ca727fa3fce12d242749dbbeefb7f8b9f425a23`.

The source selection normalizes either single configured root for both
loaders; two configured roots must resolve to the same canonical path. Missing
local roots, mismatched roots, wrong revision, dirty/wrong-hash local sources,
and bad public-source hashes fail closed rather than falling through to the
public path or returning the unavailable status. The built-in guardrails
rejected mismatched roots and deliberately wrong pinned bytes. I also ran the
test with a nonexistent configured local root and with mismatched environment
roots; both exited 1, not 77. A local `PYTHONOPTIMIZE=1` run with only
`S22_NPU_PROBE_SOURCE_TREE` set passed and reported the clean exact-derived
fixture, confirming one-root normalization.

With both source variables unset, I ran the test in normal and `-O` modes.
Both fetched pinned base
`4e5c5ad7d950e4de0688b5663965f2075654b2ad` successfully and exited 0. Each
reported the 147,641-byte shutdown subset and the same deduplicated union of
1,214,674 bytes across 19 paths. The stack loader pins each source file by
SHA-256 and caps each at 512 KiB; the shutdown loader pins each file, caps each
at 128 KiB, and caps its set at 1 MiB. Both use HTTPS raw-source URLs at the
fixed commit, reject redirects, read at most limit-plus-one bytes, set a
five-second timeout per request, and do not retry. The merged union compares
overlapping bytes before writing and is capped at stack-input bytes plus the
shutdown 1 MiB cap. These are per-request/per-input limits, not one total
wall-clock deadline.

Unavailable URL/network errors are converted to the specific
`SourceFixtureUnavailable` types and `main()` returns 77 with an explicit
`SKIP`; hash/identity/configuration mismatches are ordinary failures and are
not caught as unavailable. The actual public-source requests were available
for both runs, so an exit-77 run was not observed; the review does not count a
skip as a pass.

The public and local runs confirmed the follow-up preflight now receives the
actual temporary five-patch tree: the test applies the ordered series into
`full_root`, reads the final patched source map, then passes that same root and
map to `STACK.run_preflight`. Both public runs emitted the extracted-C
regression markers at C `-O0/-O2`, the full five-patch BOOTUP-gate marker,
preflight status 2, and `artifact_preflight_pass=false`,
`bootup_ready=false`, `bootup_authorized=false`; the firmware artifacts were
absent. No source/device files were changed.

Also reviewed the exact CI wiring commit
`5ca727fa3fce12d242749dbbeefb7f8b9f425a23`: it adds only the same explicit
test path to `REVIEWED_HOST_TEST_PATHS`, `HOST_TESTS`, and
`EXPECTED_HOST_TEST_PATHS` (three insertions in two files). No dynamic test
discovery, skip policy, runner logic, exclusions, or live-device path changed.
The CI commit's test blob SHA matches the reviewed follow-up SHA above. The
direct public-source runs provide a CI-default usability proof when the
pinned source host is reachable; an actual fetch outage remains a visible
exit-77 skip, not a passing test.
