# Independent Bluetooth H4 transport review — 2026-10-02

## Scope and verdict

Reviewed the Bluetooth source/test/doc changes from author commit
`844bd3e7d03a3188e2aef8590744dab146c87794`, as integrated at
`6340e9aad355a128c34f2e23b1b44bed253eb497`. The Bluetooth source, regression
test, and implementation record in the integration worktree are byte-identical
to the author commit. The review worktree was clean at the integrated commit.

Verdict: no blocking defect found in the reviewed diff. The bounded write
error handling and standalone UART-termios restoration are consistent with
the existing bridge lifecycle. This approves the source change only; it does
not approve or establish a built/deployed artifact, HCI attachment, controller
behavior, RF operation, or Bluetooth-audio acceptance. The configured review
selection was `gpt-6-luna`, reasoning `max`; this records configuration, not
backend self-identification.

No phone, SSH, ADB, network service, physical UART, Bluetooth controller,
firmware, or radio was accessed. Tests used the actual bridge source, controlled
syscall results, local PTYs, and host sockets only.

## Source assessment

`write_full()` now enforces its existing one-second monotonic deadline across
partial writes, write-side `EINTR`, backpressure, and poll-side `EINTR`. It
checks the stop flag and deadline again after interruptions, returns
`ETIMEDOUT` on timeout, maps a zero-byte write to `EIO`, and handles
`POLLNVAL`, `POLLHUP`, and `POLLERR` as `EBADF`, `EPIPE`, and `EIO`. A hard
`poll()` error preserves the syscall's `errno`. The reviewed changes do not
add writer threads or alter the bridge's serialized poll loop; its asynchronous
stop signal remains represented by `volatile sig_atomic_t`.

The standalone CLI snapshots UART termios before changing it and uses one
cleanup path to restore the snapshot after later setup failures or bridge
errors. UART restore failure is reported and turns an otherwise successful
exit into failure, while an earlier primary error remains primary. The PTY
line-discipline cleanup and UART termios cleanup are separate operations. The
embedded N_HCI attach/detach ownership path is unchanged by this patch.

The host suite covers exact payload retention across partial write, write
`EINTR`, `EAGAIN`, and eventual writable readiness; poll `EINTR`; timeout
mapping; zero writes; and `POLLNVAL`/`POLLHUP`/`POLLERR`. Existing cases also
exercise IBS wake-ack timeout, queue overflow, repeated SIGTERM cleanup,
malformed H4 input, attach/detach error handling, and primary-error
preservation. A local PTY regression exercises the actual standalone CLI's
termios restoration after malformed H4 input and compares the caller's termios
before and after the error.

Non-blocking test gaps: the injected timeout case makes `poll()` return zero
immediately, so it checks errno mapping rather than measuring the full
one-second wall-clock bound. The new termios test covers successful restoration
after a bridge error, but does not inject `tcsetattr()` restoration failure or
an `openpty()` setup failure. Code inspection confirms those paths converge on
the same cleanup block; they would be useful future fault-injection cases.
The suite also does not stress concurrent writers, but the reviewed bridge
loop has one synchronous writer path and this diff adds no writer concurrency.

## Verification performed

The bridge regression suite passed in all requested modes:

| Command | Result |
|---|---|
| `python3 tools/hardware/test-bt-h4-ibs-bridge.py` | 30 tests, `OK` |
| `python3 -O tools/hardware/test-bt-h4-ibs-bridge.py` | 30 tests, `OK` |
| `PYTHONOPTIMIZE=1 python3 tools/hardware/test-bt-h4-ibs-bridge.py` | 30 tests, `OK` |

The integrated bridge source compiled without warnings under host GCC
`cc (Ubuntu 13.3.0-6ubuntu2~24.04.1)`:

```sh
cc -std=c11 -Wall -Wextra -Werror -O0 \
  tools/hardware/bt-h4-ibs-bridge.c \
  -o /tmp/s22-bt-transport-review-20261002.zqogFa/bridge-O0 -lutil
cc -std=c11 -Wall -Wextra -Werror -O2 \
  tools/hardware/bt-h4-ibs-bridge.c \
  -o /tmp/s22-bt-transport-review-20261002.zqogFa/bridge-O2 -lutil
```

Both commands exited successfully. The reviewed source SHA-256 is
`32bfebfc96864b6f5150195518fc7da996f62b7b21311d79c9bfea2725f9b1a1`.
`git diff --check` passed.

## Existing artifact-provenance gate

The local `run-bt-hci-bridge-once.py::validate_local_provenance()` validator
was invoked directly with the review worktree root, a deliberately nonexistent
artifact path, and no private-header inputs. It checked source fingerprints
first and refused exactly as expected:

```text
build source fingerprint changed: tools/hardware/bt-h4-ibs-bridge.c
```

The validator's reviewed source pin remains
`476f148246dfac8330f7ade2790627b64a38ee7b1cb4f713934b6d438864a7a4`; the
current changed source is `32bfebfc96864b6f5150195518fc7da996f62b7b21311d79c9bfea2725f9b1a1`.
The refusal happened before reading the artifact or private headers. No source,
artifact, authorization, or fingerprint was modified to make the gate pass.

## Evidence boundary

The syscall seam establishes behavior for scripted host syscall results. The
PTY case establishes restoration on this host PTY path. These tests do not
measure a UART driver's timing, concurrent kernel activity, controller
registration, Bluetooth power state, RF behavior, or audio. Artifact
provenance remains refused for the modified bridge until a separate authorized
build/artifact review updates its pin; this review did not build or deploy any
artifact and does not authorize a device operation.
