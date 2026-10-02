# Bluetooth transport error-path regressions — 2026-10-02

This is a host-only bridge implementation record. No phone, SSH, ADB, physical
UART, Bluetooth controller, radio, firmware, or private trace was accessed.
Tests use the actual bridge C source with controlled syscall outcomes, local
PTYs, and Unix socketpairs. They do not establish controller or radio
behavior.

## Reproduced baseline defects

Before editing the C source, five focused regressions were run against the
original implementation. Four failed and the combined short-write case passed:

| Case | Baseline observation |
| --- | --- |
| `poll()` interrupted by `EINTR` | Returned immediately after one poll with `errno=EINTR`; it did not resume waiting for writable readiness. |
| Backpressure poll timeout | Returned `-1` with stale `errno=EAGAIN` (`11`) rather than `ETIMEDOUT`. |
| `write()` returned zero for a nonempty request | Returned `-1` with `errno=0`, leaving callers without a defined error. |
| Partial write, write `EINTR`, write `EAGAIN`, then writable | Reassembled and delivered all six bytes correctly. |
| Actual CLI cleanup over a PTY, after malformed H4 | Left the caller's PTY termios changed: `c_cflag` changed from `2239` to `2147489981`, and input/output speed changed from `15` to `4109`. |

The first four write cases execute `write_full()` from the compiled C source
with deterministic syscall outcomes. The termios case runs the actual CLI
against a local PTY and compares the caller's terminal state before and after
an error exit.

## Implementation

`write_full()` now retries an interrupted `poll()` while the one-second write
deadline remains, distinguishes stop cancellation (`ECANCELED`) from timeout
(`ETIMEDOUT`), maps a zero-byte write to `EIO`, and returns a defined timeout
errno after `poll()` reports no writable readiness. It checks `POLLNVAL`,
`POLLHUP`, and `POLLERR` before retrying a write. The controlled regression
also verifies exact payload retention through partial writes, a write-side
`EINTR`, and backpressure.

The standalone CLI snapshots the UART termios before changing it and restores
that snapshot on its shared cleanup path, including when later setup or bridge
operation fails. A failed restoration is reported; it changes a clean result
to failure and does not replace an earlier primary error. The embedded
N_HCI-attachment path, H4-versus-IBS mode selection, queue bound, and
consume-before-detach/no-retry ownership behavior are unchanged.

## Verification

All three requested Python modes passed the expanded 30-test host suite:

```sh
python3 tools/hardware/test-bt-h4-ibs-bridge.py
python3 -O tools/hardware/test-bt-h4-ibs-bridge.py
PYTHONOPTIMIZE=1 python3 tools/hardware/test-bt-h4-ibs-bridge.py
```

Each reported `Ran 30 tests` and `OK`. The bridge also compiled warning-free
at both optimization levels:

```sh
cc -std=c11 -Wall -Wextra -Werror -O0 \
  tools/hardware/bt-h4-ibs-bridge.c -o bridge-O0 -lutil
cc -std=c11 -Wall -Wextra -Werror -O2 \
  tools/hardware/bt-h4-ibs-bridge.c -o bridge-O2 -lutil
```

`git diff --check` passed. The existing local provenance validator was called
without any device preflight. It refused the modified bridge source with:

```text
build source fingerprint changed: tools/hardware/bt-h4-ibs-bridge.c
```

The source/artifact pin was deliberately not refreshed. Any later artifact
review and authorization remain separate requirements.

## Evidence limits

The syscall seam proves how this C routine handles the scripted POSIX results;
it cannot prove a UART driver's timing or error behavior. The PTY check proves
termios restoration for that host PTY cleanup path. Existing queue, parser,
plain-H4, IBS lifecycle, and single-detach tests are software regressions only.
No test in this record proves that a phone registers an HCI controller, that a
controller accepts traffic, or that radio operation succeeds.
