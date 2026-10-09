# Eleventh frozen S22 host regression run — 2026-10-03

## Result

The reviewed runner-policy tests passed 7/7. The frozen combined invocation
`python3 -I -B tools/hardware/run-host-regressions.py --mode both` ran at
repository HEAD `4f9e7760b18c2c25a013fca69937ef01d69e5c5a` and exited 1. Its
final streamed summary reported **3 failure(s)**. The visible failing lines
were optimized-mode pinned-source fixture downloads for:

- `tools/hardware/test-npu-reconciled-stack.py` — pinned-source fetch timed
  out.
- `tools/hardware/test-npu-shutdown-ownership.py` — pinned fixture unavailable
  (`Network is unreachable`, exit 77).
- `tools/hardware/test-npu-publication-drain-ownership.py` — pinned-source fetch
  timed out.

The first combined run’s stdout was streamed through the tool but not saved as
a durable raw log. Its final exit and three-failure summary, plus the above
failure lines, were observed; the complete raw transcript is unavailable, so
this record does not claim a fully audited per-mode failure/skip count beyond
those observed facts. In particular, the local run remains failed; later
hosted CI or local fixture supplements do not overwrite that result.

The runner’s frozen allowlist was checked independently: it contains 59
normal invocations and 56 optimization-safe `-O` invocations. The three
explicit optimized-mode skips are:

- `tools/hardware/test-bt-qca6490-patch-receipt.py` — standalone contract
  checks use bare `assert`.
- `tools/hardware/test-close-range-kernel-fix.py` — standalone contract
  checks use bare `assert`.
- `tools/pi-web/test_agent_web.py` — launcher deliberately refuses Python
  optimization.

Other individual test skips observed in the stream included unset optional
audio source/O-tree inputs and three AVB tests whose pinned `avbtool.py` was
absent. Because the first combined stdout was not persisted, these are
recorded as observed reasons, not a complete aggregate skip inventory.

## Exact-source supplements

Only the three failed optimized source-download cases were rerun, separately,
through their existing supported local-source environment options. The
fixture was `/home/corpunum/s22-workers/camera-kernel-build-20260927`, clean at
commit `3fca50941422439b2019db2e4a3dc1016b2138a1`, tree
`5aad5cf1dbaa0f430377737141f0547e971b0a2d`, before and after the supplements.
Each completed with exit 0:

- Reconciled-stack test, `-O`, with
  `S22_NPU_PROBE_SOURCE_TREE`: four-patch profile and host C regressions
  passed. The test itself labels this WIP source evidence and excludes the
  later shutdown patch, kernel build/runtime, and device validation.
- Shutdown-ownership test, `-O`, with
  `S22_NPU_SHUTDOWN_SOURCE_TREE`: extracted-C host regressions passed; its
  output explicitly excludes kernel/device evidence.
- Publication-drain-ownership test, `-O`, with both supported source-tree
  variables: exact nine-patch baseline versus the tenth patch and host-C
  checks passed. It still reports **unresolved liveness**: the caller remains
  blocked while synchronous publication is held because production drain has
  no timeout. The supplemental pass does not close that limitation.

These supplements are separate from, and do not convert, the failed public
source-download invocations in the combined run.

## Run and source integrity

The runner-policy test command passed:

```sh
python3 -I -B tools/hardware/test-host-regression-runner.py
```

The combined command was run without inherited source overrides. The
repository worktree was clean at HEAD `4f9e7760b18c2c25a013fca69937ef01d69e5c5a`
before and after the run. The source fixture worktree remained clean at its
pinned commit before and after the separate supplements. Logs were created
owner-only under
`/home/corpunum/.local/state/s22-driver-continuation-20261002/`; hashes and
run statuses are recorded in the adjacent JSON evidence file.

A second attempt was started solely to capture durable stdout, then stopped
gracefully on coordinator direction before completion (session exit 130).
Its partial log is preserved and identified as **cancelled/incomplete**, not
as a suite result. The first run remains the authoritative local combined
result.

The coordinator separately reported a successful hosted run at exact revision
`1edf236` (CI job `37075233538`, 59 normal / 56 optimized, zero runner
failures). That is separate hosted evidence; it does not relabel this local
run’s exit 1.

## Evidence boundary

This is host regression coverage only. Tests use pinned source fixtures,
synthetic C shims, temporary filesystems, or mocked transports as described by
their scripts. No kernel build, real module load, firmware execution, phone
access, partition write, reboot, or BOOTUP action was performed. Host tests
and source-harness passes establish no device/runtime acceptance.
