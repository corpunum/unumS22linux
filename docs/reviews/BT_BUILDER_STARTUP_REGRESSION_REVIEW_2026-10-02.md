# Independent BT builder startup regression review — 2026-10-02

## Scope and disposition

Reviewed implementation commit
`21c3f64c301b0c808b51fd8cdd08466535858970` in the isolated startup-review
worktree. The change touches only the candidate builder, its host test, and
the candidate research note. The requested reviewer selection was
`gpt-6-luna`, reasoning `max`; that records the requested configuration, not
model self-identification.

Disposition: approve the focused future CLI isolation gate and TMPDIR test
repair for the operational `--check-only` and `--build-candidate` paths. The
gate requires both `sys.flags.isolated` and `sys.flags.no_site` before
preflight. It does not change the candidate source, artifact, receipt, or
legacy runner. The prior review's builder/invocation and interpreter
attestation limits remain historical; this change does not establish what
helper or interpreter produced the already-retained artifact.

## Startup gate and its boundary

The new main-path check requires `-I` and `-S` before `run_preflight()` for
both operational modes. Under the documented isolated command, Python
ignores `PYTHONPATH` and other `PYTHON*` variables and does not run site
customization at startup. The
`sitecustomize` fixture sets `PYTHONPATH` to a temporary module that would
create a marker; the isolated invocation returned successfully without the
marker. The separate nonisolated `--check-only` subprocess returned status 2
with the expected isolation refusal before preflight.

Two details bound that conclusion:

- `argparse` handles `--help` before the `sys.flags` check, so nonisolated
  `--help` can exit successfully. It performs no preflight or build. The
  tests check that injected startup code is absent under isolated `--help`,
  not that nonisolated help is refused.
- If a caller starts Python without `-I -S`, interpreter startup hooks can
  run before the helper reaches its refusal. The check prevents the helper's
  preflight/build path from proceeding; it is not a sandbox for a caller
  that ignores the documented startup mode. The builder enforces `-I -S`;
  `-B` appears in the documented/test command but is not checked by the
  helper.

These are operational boundaries, not defects in the tested isolated
preflight/build paths. The research note's command is appropriate when read
as the required way to invoke those paths.

## TMPDIR regression

The fixed host runner deliberately supplies a controlled temporary
`TMPDIR` to test subprocesses. The prior candidate test copied the parent
environment into its positive `--check-only` subprocess, while the builder
correctly rejects any inherited `TMPDIR`. The new `isolated_cli()` helper
uses the existing `SAFE_CHILD_ENV` for successful preflight and still
propagates `-O` when the parent test process is optimized, because `-I`
ignores `PYTHONOPTIMIZE`.

The refusal remains intact. The unit environment check includes `TMPDIR`,
and a real isolated CLI subprocess with `TMPDIR` injected exits 2 with
`unsafe environment variable set: TMPDIR`. The corresponding CPATH refusal
also remains covered. Thus the positive test removes only the host runner's
ambient variable; it does not relax the production guard.

The research note's historical 107-invocation result is consistent with the
runner's controlled-TMPDIR environment and the earlier test's use of
`os.environ.copy()`. This review did not rerun that frozen aggregate; it
verified the failure mechanism in the checked-in old/new source and ran the
repaired candidate suite under `TMPDIR=/tmp`.

## Verification

The 16-test suite passed in all three requested modes. All 3 optional exact
local-fixture tests passed in each run:

| Invocation | Effective optimization | Result |
| --- | ---: | --- |
| `TMPDIR=/tmp`, normal Python | 0 | 16 passed |
| `TMPDIR=/tmp`, `python3 -O` | 1 | 16 passed |
| `TMPDIR=/tmp`, `PYTHONOPTIMIZE=1` | 1 | 16 passed |

The local preflight uses `--check-only`; it performs compiler identity
queries and a dependency scan but does not link an artifact. No candidate
build or execution was run. The recorded candidate SHA-256 remains
`74b39343eaa0cd4e7176fb0fef0d6a30ee3e6abcea8954f68331381e719d8c61`, and
the checked-in sanitized receipt SHA-256 remains
`925a8d8592bad1eba85340338709b7076f4a127a3348bca020ad7a0ba16e3976`.
`git diff --check` passed for the implementation commit.

## Evidence boundary

This review supports the future invocation gate, startup fixture, environment
refusals, and corrected positive-test environment. It does not revise or
retroactively strengthen the historical build receipt's provenance, attest
the Python executable or helper bytes used by a future caller, prove a stable
input snapshot during compilation, or establish Bluetooth behavior. No
candidate artifact was rebuilt or executed; no phone, HCI device, firmware,
private upload, staging, or deployment action occurred.
