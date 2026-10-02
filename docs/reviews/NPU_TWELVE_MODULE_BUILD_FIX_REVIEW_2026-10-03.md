# Independent NPU12 module-build wrapper review — 2026-10-03

## Verdict

Review of frozen author commit `567b262861fb5877227c9df170d7d685b5a14a14`
(tree `6c25c2cc22950b44c617a110d99a42c44b931d50`, parent
`98b1d0a29060098ecfb06890dabb7c34431008cd`) passes for the narrow purpose of
one host-only `drivers/vision/npu.ko` build. I recommend the coordinator issue
the separate, exact build GO after a fresh launch-time source/output/resource
check. This review is not that GO and authorizes no image build, package,
module load, phone access, or BOOTUP.

No `olddefconfig`, Kbuild, real module compile, package, or device action was
performed here. The result is a review of orchestration and its hardware-free
tests, not evidence that a module builds or works at runtime.

## Findings

The frozen follow-up changes four files only: the wrapper, its new test, and
the corresponding research note and JSON receipt. It leaves the pinned kernel
source, patches, config, toolchain, and sole build target unchanged. Wrapper
SHA-256 is
`8dab1a2c0ea2b01555645717b46c0f1506ff951a60a9256669d90bae6859d61d`; test
SHA-256 is
`f27de2c491b6e71d383087cbbdc6efde246688534312879c07dc4077e86e43c7`.

Both defects in the preserved initial wrapper are fixed in the actual
execution path. `collect_plan()` now returns the verified toolchain mapping
that is recorded in the build phase. The generated `include/config/kernel.release`
is checked only after the monitored module target exits zero without a
resource abort. The pinned Makefile dependency chain puts release-file
generation under `archprepare`, reached through `prepare`/`modules_prepare`
from the single-module target; `olddefconfig` is configuration-only. The new
check still rejects a missing, symlinked, or wrong release file.

The execution boundary is appropriately narrow: it requires `--execute`, the
exact current wrapper hash, and a source-and-wrapper-bound coordinator token.
The only Kbuild command is `make -j1 V=1 drivers/vision/npu.ko`, invoked via
`nice` and `env -i` with the pinned tool path and explicit `LC_ALL`, empty
`LOCALVERSION`, and `TMPDIR`. The wrapper checks source commit/tree/parent and
cleanliness, config and patch hashes, all 17 pinned tool invocation
identities, helper hashes, and exclusive output paths. It rechecks source and
the operation start gate before the module target, and accepts success only
after no resource abort, stable source/config, the exact generated release,
one expected `.ko`, and no `Image`.

The resource policy remains scoped: the wrapper requires 12 GiB available
memory and 24 GiB free on `/home/corpunum/s22-linux/builds` at start; the
unchanged monitor aborts at 8 GiB available memory or 16 GiB free disk. Its
separate full-profile 32 GiB initial-disk gate is explicitly checked and
unchanged. The monitor is hash-pinned and its bounded owned-process-group
cleanup behavior is covered by its separate 29-test suite. Partial outputs
are retained rather than cleaned or retried.

## Independent checks

- The new seven-case test suite passed with effective Python optimization
  levels 0 (normal), 1 (`-O`), and 1 (`PYTHONOPTIMIZE=1`). It calls the actual
  `collect_plan()` and `execute_build()` functions against temporary
  directories, a controlled `Popen`, and a fake monitor. Unexpected child
  commands fail; no real subprocess or kernel build can start through this
  fixture. Coverage includes toolchain propagation, the success boundary,
  config/olddefconfig refusal, both start checks, resource-abort despite a
  zero child exit, `SystemExit(0)`, existing-output preservation, and
  missing/wrong post-target release values.
- I loaded the exact original wrapper bytes from commit
  `98b1d0a29060098ecfb06890dabb7c34431008cd` (SHA-256
  `c4a25c93696217c1ae905df51bb57ece4ca652291b6f001a47c2ab23e8aca842`)
  into memory and ran the new regression cases against those real old
  functions. The toolchain case failed because the old plan omitted the
  mapping; the successful orchestration case failed at the premature release
  check. A separate controlled execution synthesized the release only after
  fake `olddefconfig` to isolate dispatch; with the old plan’s toolchain field
  absent, the old `execute_build()` recorded `KeyError` and returned 2. This
  synthetic release is only a test seam, not a claim about generated output.
- The actual wrapper's read-only plan passed in normal, `-O`, and effective
  `PYTHONOPTIMIZE=1` invocations. Each reported the pinned source
  `e9c3016233a72ceccb13e537f0b7ef72426582b9`, config digest, helpers and 17
  tool identities, with execution `NOT_RUN` and both reserved paths absent.
  An `--execute` invocation with the correct wrapper SHA but no GO token
  refused with exit 2; the module output and filtered Symvers paths remained
  absent.
- The separate pinned monitor suite passed all 29 tests, including bounded
  cleanup of only the owned process group and residual-descendant checks. This
  validates that helper separately; the new wrapper test uses a fake monitor
  and does not claim an end-to-end monitored Kbuild run.

The latest independent plan-only resource sample was at
`2026-10-02T22:43:03Z`: 30,931,292,160 bytes free on the build filesystem and
27,091,759,104 bytes available memory. The wrapper's 24/12 GiB start gate
passed at that instant, as did the 16/8 GiB monitor thresholds. This is a
point-in-time observation, not a reservation; repeat the gate immediately
before any authorized build. `/srv/s22` is a different filesystem and is not
build-capacity evidence.

## Test commands and boundary

The orchestration suite passed under:

From the review worktree root:

```sh
/usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C /usr/bin/python3 -I -B tools/hardware/test-build-npu-twelve-module-only.py
/usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C /usr/bin/python3 -I -O -B tools/hardware/test-build-npu-twelve-module-only.py
/usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C PYTHONOPTIMIZE=1 /usr/bin/python3 -B tools/hardware/test-build-npu-twelve-module-only.py
```

The read-only plan matrix and no-token refusal were also exercised. The
remaining authorized next step is at most the single reviewed host module
build after a separate coordinator GO and fresh checks. No test or plan result
here establishes a successful kernel target, ABI acceptance, loaded-module
identity, firmware response, BOOTUP, or device/runtime acceptance.
