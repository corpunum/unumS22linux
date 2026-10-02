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

## NPU mailbox source/liveness review

Reviewed the frozen author commit
`0faa2c2ed2076115a2e3dd3f03c19608ee9a3f0e` and integrated commit
`6be90f259f707bb6fb278e7332f0e5e3b825d2f6`. The three reviewed paths
(`test-npu-publication-liveness-c.py`,
`npu-mailbox-missing-callback-reclaim.patch`, and
`NPU_PUBLICATION_C_LIVENESS_2026-10-02.md`) are byte-identical between those
commits. Tests ran from an archive of the integrated commit; this review
worktree's history and the pinned source fixture were not changed.

The patch is limited to `npu-if-protodrv-mbox2.c`, SHA-256
`f109b57381b3f2afcf2638b518f50db59ef8c9f784debd949488c74f8ea5c39b`. Its
single addition claims the issued message ID in the `CONFIG_NPU_USE_BOOT_IOCTL`
branch when `nw_post_request` is unavailable, reclaiming that slot instead of
leaking it. The extracted allocator and mailbox C are compiled and exercised
both before and after applying that exact patch. The cases cover reclaim of
valid issued IDs, retention when the callback succeeds, no claim/post for an
exhausted allocator result, and repeated absent-callback operation without
pool exhaustion. The legacy wrapper compiles the actual extracted mailbox
function with the BOOT_IOCTL branch disabled and verifies the `POWER_DOWN`
msgid-zero overwrite does not claim slot zero or damage its existing owner;
the no-free-slot case also remains unclaimed.

The header-derived `MSGID_POOL_MAGIC`, NPU ID, and DSP ID are parsed from
SHA-verified inputs; there is no hardcoded fallback for those constants. The
test deliberately scales its synthetic pool to four slots for deterministic
exhaustion. The three additional pinned input files total 19,710 bytes:
`npu-if-protodrv-mbox2.c` (14,582 bytes; SHA-256
`d13247bd90ab27dd58cfe9241ec1a07460ab3b353d86a268f8a6fadefc6ff755`),
`npu-util-msgidgen.c` (3,842 bytes; SHA-256
`271dfe4f0b591a9a6d5a3f996e5fd2fe7a05d9b839513ce8691863bae79d3e2e`), and
`npu-util-msgidgen.h` (1,286 bytes; SHA-256
`ff3cd551102edf433e3ef0c372ee2b8ed2cd8caf672ac00853d3ea9a134736a8`).
The loader enforces per-file limits, verifies hashes, and checks exact overlap
and aggregate union accounting with the existing stack and shutdown source
sets; the reported unique union is 1,234,384 bytes. The shutdown subset is
147,641 bytes. The source/preflight fixture's pinned `s5e9925_defconfig`
records `CONFIG_NPU_MAILBOX_VERSION=9`, `CONFIG_DSP_USE_VS4L=y`,
`CONFIG_NPU_USE_BOOT_IOCTL=y`, and `# CONFIG_EXYNOS_NPU_CORE is not set`.
This is not the separate preserved build `.config`: the build helper's
preserved config pins `CONFIG_EXYNOS_NPU=m` and
`CONFIG_NPU_USE_BOOT_IOCTL=y` (along with its other required options). The
preflight fixture must not be read as saying the actual build disables the
NPU driver.

With both local-fixture environment variables unset, the public pinned-source
loader completed and reported the expected pinned tree and source union. The
exact archived test also passed against the local pinned fixture in normal,
`-O`, and `PYTHONOPTIMIZE=1` modes. Each run compiled and ran the baseline and
patched extracted C cases with warnings-as-errors, then completed the
preflight refusal checks. All retained `preflight status 2`,
`bootup_ready=false`, and `bootup_authorized=false`; required firmware files
were absent. No source fallback silently converted the public-loader case to
the local fixture.

The review boundary matters here: the allocator, mailbox function, power
branch, and waiter code are extracted from pinned source, but are executed in
a host harness with synthetic synchronization, queue, completion, mailbox,
and scheduler implementations. In particular, the legacy wrapper tests the
actual extracted function under `#undef CONFIG_NPU_USE_BOOT_IOCTL`, but the
test supplies compact synthetic command enum values instead of compiling the
full legacy `npu-common.h`/Kconfig ABI. That is a limit on legacy ABI
validation, not evidence of full kernel-build compatibility. The harness
does not establish Linux locking/memory-ordering, IRQ context, teardown, real
MMIO, kernel allocation, or device behavior. Source inspection also confirms
the synchronous `wait_for_completion(&waiter->publish_done)` remains
unbounded; the controlled callback-stall case is released by the harness and
does not resolve or prove real-world liveness.

**NPU review disposition:** no blocking defect was found in the scoped patch,
header-derived constants, hash/cap/union checks, or listed host cases. This is
source and host-harness clearance only; it is not kernel-build, BOOT_IOCTL
hardware, mailbox-MMIO, firmware, boot-up, or NPU acceptance.

## Six-patch build helper and host-CI allowlist review

Reviewed author commit `9998ea4b1d8492e8ff2e0490469ae32348297c16` and
integrated commit `a1b7da2`; the builder, focused test, and research receipt
are byte-identical. The reviewed builder SHA-256 is
`5eb0900960fa687be99ca2e4fdc617d1ea2ecec6859c02780c06086973de46cd`. This is
the later-reviewed helper, not the helper SHA recorded by the already-launched
build (`13cec8b3b0cbb28f45856a374d843702c8e79cc7c80c286b3b19525b19aa3de1`);
the active build was neither restarted nor controlled by this version.

Static review found the helper gates the exact clean direct-child source
commit/tree and ordered six patch replay, checks pinned patch and preserved
config hashes, identifies the configured Clang/LLD/LLVM helper payloads,
limits jobs to one or two, requires a fresh scoped output path, checks startup
and ongoing memory/disk thresholds, and stores build phase/log/artifact
metadata. It creates a new output directory and log exclusively, uses a
separate process session for the build, caps the displayed failure-log tail,
and emits `bootup_ready=false` and `bootup_authorized=false` in phase/build
metadata. The research receipt correctly says the active build was still
running at its snapshot and that the reviewed future helper did not control
it.

The twelve focused builder tests passed from the integrated archive in normal,
`-O`, and `PYTHONOPTIMIZE=1` modes. They exercise fail-closed source/config/
toolchain/output checks, exact ordered patch replay in a synthetic Git repo,
and mocked process/resource monitoring. They do not launch Kbuild or prove
real process-group teardown. CI commit `5a2936133425fab9c71778007951ad2f09bd333a`
adds only the publication-liveness and build-helper test paths to both the
runner's explicit sequence and its independent expected sequence. Its seven
runner-policy tests passed in all three Python modes. Those policy tests use
a fake runner for invocation accounting; they do not run the full host suite.
No full host-suite or hosted-CI pass is claimed here.

### Blocking process-group cleanup finding

The builder's `stop_own_process_group()` sends SIGINT to the owned process
group, then waits only for the direct `Popen` leader. If that leader exits
while a same-group descendant remains alive, the helper returns `True`
without escalating SIGTERM/SIGKILL or checking whether the process group is
empty. This makes its “owned process group stopped/reaped” result stronger
than the observed state and can leave a build child behind on monitor-error
or resource-abort cleanup.

Reproduction used only a controlled local process group, not a build: the
leader spawned a descendant and waited for it to install a SIGINT-ignore
handler before reporting its PID. The helper sent SIGINT to the group; the
leader exited with return code `-2`, while the descendant ignored SIGINT and
remained live. `stop_own_process_group()` nevertheless returned `True`.
The reviewer then SIGKILLed and reaped the controlled descendant in `finally`;
no process was left running. The current mock-based tests cover bounded waits
when the direct process itself remains unreaped, but not this leader-exits-
first case.

**Builder/CI disposition:** the helper is not cleared for process cleanup
until it continues bounded group-wide termination after the direct leader
exits and has a regression covering a surviving same-group descendant. The
CI allowlist delta is internally consistent and the policy tests pass, but
that does not clear the builder finding or establish full-suite/CI success.
No kernel build result, `Image`, module completion, or build receipt is
claimed by this review.
