# S22 host audio collector review — 2026-10-02

## Scope and disposition

Independent review of audio author commit
`eb25aa24171cd8c4efa6ec31935e035bd79f5e06`, integrated at
`5a855f6646a521525543746b9ca84073050965d0`, against baseline `8c35bac`.
Reviewed `tools/hardware/audio-control-readiness.py`, its eight regression
cases, and `docs/research/AUDIO_PASSIVE_READINESS_2026-10-02.md`. Also reviewed
host-regression allowlist commit `5662393c91e1902298dcad6dede085cf6fe0e19d`
from an isolated `git archive`, without changing this review worktree.

**Blocking finding:** after starting a metadata subprocess, an unexpected
Python exception can escape `listing()` without terminating or reaping that
child. The cleanup currently covers `OSError` and normal timeout handling but
not `KeyboardInterrupt` or other uncaught exceptions. This leaves the opt-in
listing process running after the caller has stopped waiting, so host-side
clearance is withheld pending an exception-path cleanup and regression.

This review used only controlled local Python subprocesses and temporary
files. It did not invoke `amixer` or `tinymix`, inspect a real audio device,
connect to a phone, or perform a build or device operation.

## Blocking finding: exception after spawn leaks the process group

`listing()` closes its selector and pipe handles in `finally`, but its
process-group kill and `wait()` logic follows that block. If an uncaught
exception leaves the main `try`, Python runs `finally` and then propagates the
exception, skipping the kill and reap code. A user interrupt while the
selector is waiting has the same shape.

Reproduction: the review replaced the selector's `select()` with a controlled
`KeyboardInterrupt`, while starting a real local Python child that sleeps for
30 seconds. `listing()` raised `KeyboardInterrupt`; the captured child still
polled as live (`returncode is None`). The review then sent `SIGKILL` to its
process group and waited for the direct child, confirming cleanup was needed.
The eight committed tests do not exercise an exception after spawn.

Required correction: ensure every exceptional exit after `Popen` terminates
the process group and performs bounded direct-child reaping before propagating
the exception. Add a regression that injects an exception during selector
work and verifies no child is left live. Preserve the current finite cleanup
bound.

## Other audio-path review

- Default mode never looks up or invokes mixer utilities. It reads only the
  named local procfs/sysfs paths. `--list-controls` is opt-in and constructs
  only `amixer -c 0 controls` and no-argument `tinymix`; no shell is used and
  no write or PCM command arguments are built.
- File reads open with nonblocking/no-follow flags where supported, reject
  non-regular final files, and read at most the requested limit plus one byte
  for truncation detection. XML is capped at 256 KiB, hashed and parsed only
  after a complete read, and rejects DTD/entity declarations before parsing.
  The symlink check is paired with `O_NOFOLLOW` for the final path component.
- Listing output is drained from pipes, retained only to configured prefixes,
  and excess bytes are discarded. Normal exit with truncated output is
  represented as `available: true, complete: false`; timeout, nonzero exit,
  and capture errors remain unavailable. The output marks target identity
  unverified and states that successful control-node open is not verified.
- No aggregate audio-ready result is emitted. The per-field `available`
  statuses describe a local file read, XML parse, or command result; they do
  not establish route readiness, control-node permissions, DMA, firmware,
  audible output, or target identity. Empty or missing route records must not
  be interpreted as hardware acceptance.
- Repository search found no in-tree consumer of this helper. `read()` now
  returns a structured record instead of `str | None`, and JSON fields changed
  materially; no external consumer was available to check. `--mixer-xml`
  remains supported and `--list-controls` is additive.
- The executable mode is retained (`100755` in the integrated tree).

## Baseline defect reproductions

Against the actual `8c35bac` helper source, using only patched tool discovery
and controlled local Python children:

- Baseline default `main()` looked up both `amixer` and `tinymix`; baseline
  argparse rejected `--list-controls` with exit status 2.
- A child wrote 1 MiB to each output. Baseline `listing()` retained the
  advertised 262,144/4,096-byte prefixes but its two temporary files reached
  1,048,576 bytes each (2 MiB total); both truncation flags were true.
- On its fixed five-second timeout, baseline killed and reaped the direct
  sleeper but left its spawned descendant live. The review observed the
  descendant in state `S`, then sent it `SIGKILL` before leaving the temporary
  directory.

These reproduce the passive-default, disk-spool, timeout-descendant, and CLI
defects recorded in the research note. The new helper's normal timeout test
also confirms the descendant is terminated or already a dead zombie before
return.

## Host evidence

The eight audio helper tests passed independently under all requested modes:

```text
python3 -I -B tools/hardware/test-audio-control-readiness.py       8 passed
python3 -O -I -B tools/hardware/test-audio-control-readiness.py     8 passed
PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-audio-control-readiness.py  8 passed
```

These results do not cover the uncaught-exception lifecycle path above.

Allowlist commit `5662393` adds the audio regression path in both runner
sequences and the independently pinned expected path sequence. Its isolated
archive passed the seven host-runner policy tests under normal Python,
`-O`, and `PYTHONOPTIMIZE=1`. This verifies allowlist consistency only; the
full host suite and hosted CI result for that commit were not run here.

**Evidence boundary:** these are source and host-only findings. No audio
hardware acceptance is claimed.

## Exception-cleanup follow-up retest

Retested author fix `6ebc3f6a0688a2eece458e4bf7954b0638e5d04b` and integrated
commit `04dc86fde5ecc252ba2ab03398918cc27e5a8c52`. The audio helper, its test
file, and its research note are byte-for-byte identical between those two
commits. Because the integrated commit is not a descendant of this review
worktree's branch, I tested the frozen author files from a temporary archive;
the review worktree stayed on its existing history.

The original interrupt reproduction now passes: an injected `KeyboardInterrupt`
from selector `select()` is re-raised as the same exception object, and the
captured sleeping child is already reaped with return code `-9` when control
returns to the caller. No manual cleanup was needed. The implementation also
handles selector-registration and pipe-drain exceptions, uses bounded
process-group/direct-child cleanup, and attaches cleanup failures to the
original exception without replacing it. Four added regression cases cover
those paths, including failed group signaling.

As a negative-control check, I paired the four new tests from `6ebc3f6` with
the pre-fix helper from `eb25aa24171cd8c4efa6ec31935e035bd79f5e06`. All four
failed at the assertion that the direct child was already dead on return;
each test then manually killed and reaped its controlled child so the
negative-control run left no process behind.

All 12 helper tests passed from the archived author commit in each mode:

```text
python3 -I -B tools/hardware/test-audio-control-readiness.py       12 passed
python3 -O -I -B tools/hardware/test-audio-control-readiness.py     12 passed
PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-audio-control-readiness.py  12 passed
```

**Follow-up disposition:** the blocking subprocess exception-cleanup finding
above is cleared for the reviewed frozen helper and tests. The result remains
host-only and does not establish phone access, route readiness, or audio
hardware acceptance.
