# Independent audio-control ESRCH regression-test review — 2026-10-03

## Scope and disposition

Reviewed `ceeb217ffec81f93a45ea0a6cbce0aaee146892d`, which changes only
`tools/hardware/test-audio-control-readiness.py`. The production
`audio-control-readiness.py` is unchanged. This is a narrow host-test fixture
robustness correction, not a production behavior change or device result.

The timeout fixture owns the process group it starts: a Python test child
creates its own Python sleeper descendant, writes that descendant's PID to a
temporary file, and then sleeps. The readiness collector is expected to time
out and clean up that group. The test polls `/proc/<that-child-pid>/stat` to
accept either disappearance or a dead zombie. Commit `ceeb217` adds
`ProcessLookupError` to the existing `FileNotFoundError` catch and adds a
controlled regression case that injects `ProcessLookupError(errno.ESRCH, ...)`
for exactly one such `/proc/*/stat` read. It then reuses the same owned-child
fixture and asserts exactly one injection. No production process-management
or safety check was edited or weakened; process cleanup targets only the
test-created group/descendant.

## Controlled failure and test results

I executed the pre-`ceeb217` test source in memory with the same injected
ESRCH condition. It failed at the expected test-only `/proc/<owned-child>/stat`
read with `ProcessLookupError(errno=ESRCH)`, once. This reproduces the fixture
race; it is not evidence of a production collector regression. Against the
updated test file, all 13 tests passed in each requested mode:

- `python3 -B tools/hardware/test-audio-control-readiness.py`
- `python3 -O -B tools/hardware/test-audio-control-readiness.py`
- `env PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-audio-control-readiness.py`

The test suite uses local temporary files and its explicitly created short-
lived Python child/descendant fixtures. It does not access a phone, load a
module, open PCM nodes, perform mixer writes, or invoke remote/device control.
The explicit control-listing case mocks the fixed metadata commands. The
pre-fix negative control and the updated suite therefore support only the
specific host-side test correction and do not establish hardware readiness.
