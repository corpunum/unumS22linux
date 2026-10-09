# Independent NPU system-resume correction review — 2026-10-03

## Verdict

**BLOCKED for portable regression/CI use.** The NPU14 source correction passes
the narrow CPU_ON/STM uncertainty-ordering case in the actual extracted-C
harness. However, the public-source runner depends on an author-local Git
commit object to obtain its frozen pre-correction negative-control patch. A
fresh tracked-source fixture with no such object successfully fetched the
pinned public C files and composed the selected NPU12 patch stack, then failed
before compiling any C. The local/public-route passes below used this host's
shared Git object store and are not evidence that the regression runs from a
portable checkout.

This verdict is limited to the runner fixture dependency. It does not claim a
new source-ordering defect. The earlier blockers remain: the void image-loader
shutdown cannot report provider completion, remove/release ownership is not
covered, and file-static firmware log-buffer lifecycle is unresolved.

## Frozen inputs

- Author correction: `9553c8b7f1e7a7a0afd5d8d208a53aed2958d3ef`.
- Reviewed import in this worktree: `3e7101e2b5ce646763f7a3fb6c7dcab4e6c4cada`
  (tree `c92dd13` plus the author correction; no implementation edits by this
  reviewer).
- Raw kernel base: `4e5c5ad7d950e4de0688b5663965f2075654b2ad`.
- Current NPU14 patch SHA-256:
  `f1af656f9e1b8ca2bf15e934031e0adf828c442267a17761ba64f7d7c611729a`.
- Extracted-C harness SHA-256:
  `bb9f2f631f7598e79a9638d0373a6a781a0a06a7e4c5e584aad3501a2edbd547`.
- Runner SHA-256:
  `e9215fb8abc54e208058df2a9c396726338f2372ea99f88a199b95ecdb2a0293`.
- The runner requests old-patch commit
  `4a22948184f101f2bd80d2f44eef44b46004b790`, whose patch hash is
  `464a78b43f7ef0cc7e26b5f69075980460211f9c789d0a418b36d44be548968e`.

## Source-order result: narrow correction passes

The patch adds CPU_ON uncertainty quarantine at the beginning of
`npu_system_suspend()` and the corresponding non-BOOT_IOCTL STM-enable guard
before the existing interface-close and firmware-loader stages
(`tools/hardware/npu-system-resume-error-unwind.patch:197-216`). These guards return through
the error path without clearing ownership. On uncertain acquisition they
prevent interface close, `npu_imgloader_shutdown()`, CPU/STM inverse, clock
disable, and wake release. The actual extracted-C regression checks zero
calls to those modeled helpers, retains the `FW_LOAD`/uncertainty bits and
wake dependency, and confirms a repeated suspend does not retry. The selected
BOOT_IOCTL CPU_ON case and the alternate runtime-PM/STM case both pass.

The normal known-on CPU_OFF sequence remains distinct: the harness asserts
the existing image-loader shutdown request occurs once before the first
CPU_OFF attempt; if CPU_OFF fails, the next suspend does not repeat the
shutdown or inverse (`tools/hardware/npu-system-resume-error-unwind-harness.c:746-788`). The
pre-correction negative control reproduces the original ordering: one
shutdown-wrapper call occurs while CPU/STM state is still modeled live
(`tools/hardware/npu-system-resume-error-unwind-harness.c:1002-1030`). This counts only the wrapper call; it does not
execute or prove S2MPU/provider permission release.

## Portable-runner blocker

The test runner hard-codes the pre-correction author commit at line 42 and
executes `git -C ROOT show <commit>:tools/hardware/npu-system-resume-error-unwind.patch`
at lines 212-223, called during composition at lines 308-312. That exact
commit is present only through a local author branch in this worker; it is
not reachable from the reviewed remote branch. In a fresh isolated source-only
Git repository, `git cat-file -e 4a229...^{commit}` returned 128, and calling
the runner's real `legacy_npu14_patch()` raised
`RuntimeError: frozen pre-correction NPU14 patch commit is unavailable`.

I also ran the complete runner from that isolated tracked-source fixture with
no `4a229...` object. It fetched all four pinned public raw files and applied
the eight selected native-eight patches, reproducing the expected NPU12
selected-file hashes; then it exited at `legacy_npu14_patch()` before any C
compile/run job. The isolated repository had no object alternates or private
source worktrees. The exact old patch bytes are published in reachable commit
`23e6f6c1ac1cc8e98570cc8eb277290174df5b58` (same SHA-256), but this runner
still requests `4a229...`; no author code was changed in this review.

Consequently, the runner's successful public-fetch invocations on this host
are not portable-public evidence: the shared local Git object store supplied
the missing author commit, and the runner also verified the optional local
derived and composed source worktrees. A public-source-only invocation must
obtain the pre-correction control from an explicitly available, hash-pinned
fixture rather than an unadvertised commit object.

## Independent test results

Each completed invocation compiled and ran 12 extracted-C jobs: baseline,
pre-correction negative control, and corrected source at C `-O0` and `-O2`
for BOOT_IOCTL and the alternate runtime-PM/STM path. Python optimization
flags were observed from `sys.flags.optimize`.

| Route actually executed | Python mode | `optimize` | C jobs | Interpretation |
| --- | --- | ---: | ---: | --- |
| Exact local derived fixture, `--local-only` | normal | 0 | 12 passed | Local fixture path passes |
| Exact local derived fixture, `--local-only` | `python3 -O` | 1 | 12 passed | Local fixture path passes |
| Exact local derived fixture, `--local-only` | `PYTHONOPTIMIZE=1 python3 -B` | 1 | 12 passed | Local fixture path passes |
| Public raw fetch in shared worker worktree | normal | 0 | 12 passed | Raw fetch passed; local Git/private fixtures were available |
| Public raw fetch in shared worker worktree | `python3 -O` | 1 | 12 passed | Same local-object limitation |
| Public raw fetch in shared worker worktree | `PYTHONOPTIMIZE=1 python3 -B` | 1 | 12 passed | Same local-object limitation |
| Isolated source-only repository, public route | normal | 0 | 0 | Blocked at unavailable author commit before C |

An explicitly supplied nonexistent composed-fixture path was rejected with
`optional clean composed NPU12 fixture tree is absent or a symlink`; it did
not silently fall back to another source. This is a fail-closed negative
control, not a successful public-only run.

## Preserved limits

- `npu_imgloader_shutdown()` is `void`; its provider can log and return early
  when S2MPU permission release fails, while the caller clears `FW_LOAD`.
  This correction does not make provider completion observable.
- `npu_system_release()` and remove/unbind lifetime paths do not gain these
  uncertainty guards. No release, remove, or unbind operation was run.
- The log buffers are file-static, and the pinned free implementation remains
  a TODO returning zero. Tests model injected helper failures, not actual
  buffer reclamation or cross-device ownership.
- The C harness uses controlled helper shims; it does not exercise kernel
  PM scheduling, S2MPU, clocks, wake-source behavior, firmware completion,
  DMA quiescence, physical CPU state, or NPU hardware.
- No kernel/module build, firmware action, phone/SSH/ADB access, NPU BOOTUP,
  or hardware acceptance was performed or authorized.
