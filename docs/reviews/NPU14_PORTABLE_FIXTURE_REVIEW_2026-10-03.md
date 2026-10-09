# Independent NPU14 portable-fixture review — 2026-10-03

## Verdict

**PASS_LIMITED for source-only host-test portability and negative-control
fixture integrity.** The frozen runner's Git-history dependency is removed:
the tracked pre-correction patch is loaded as a bounded, hash-pinned test
input. The public pinned-source route composes and tests the baseline,
pre-correction negative control, and corrected NPU14 sources from a small
source-only checkout with no `.git` directory and no private kernel tree.
Malformed historical fixtures fail before public fetch or C compilation;
explicit malformed optional-tree environment inputs remain fatal even when
default optional-fixture discovery is skipped.

This does **not** clear the broader NPU14 source review or establish runtime
safety. The earlier source-order finding is covered by the separate corrected
source patch and its extracted-C negative control, but the void image-loader
completion result, remove/release ownership, and file-static log-buffer
lifecycle remain unresolved. No NPU module build, firmware/provider action,
device access, or BOOTUP acceptance occurred.

## Frozen scope and fixture identity

- Review worktree: `/home/corpunum/s22-workers/npu14-portable-review-20261003`,
  branch `codex/s22-npu14-portable-review-20261003`, based at
  `d06bb09bcb69fd1418863ae000181afa66fa1938`.
- Portable author commit: `7986c0559cf170006eb33e5699f95ff194463796`, tree
  `3a12f0853e553a5fe07fe15cf861bf031d9668ef`.
- The portable commit changes the test runner, tracked fixture, research note,
  and its host receipt. It leaves the NPU14 source patch
  (`f1af656f9e1b8ca2bf15e934031e0adf828c442267a17761ba64f7d7c611729a`) and
  extracted-C harness
  (`bb9f2f631f7598e79a9638d0373a6a781a0a06a7e4c5e584aad3501a2edbd547`)
  byte-identical to the parent of that commit.
- Historical fixture:
  `tools/hardware/fixtures/npu14-pre-correction.patch`, 11,982 bytes,
  SHA-256 `464a78b43f7ef0cc7e26b5f69075980460211f9c789d0a418b36d44be548968e`.
  It is byte-identical to the frozen historical patch at commit
  `4a22948184f101f2bd80d2f44eef44b46004b790`. The current NPU14 patch has a
  different hash.
- The current runner's `legacy_npu14_patch()` reads only that tracked file. It
  rejects a symlink (and a symlinked/missing fixture directory), missing or
  non-regular file, files above the 512-KiB limit, and any SHA mismatch. The
  call occurs before public-source fetch or C compilation. No Git object or
  history fallback remains in this loader.

The author’s `build-npu-six-profile.py` and
`build-npu-twelve-module-only.py` use explicit patch tuples and contain no
reference to the negative-control fixture. In the regression runner, the
historical patch is applied only to its separate `legacy-npu14` temporary
copy; the corrected patch is applied to a separate `corrected-npu14` copy.
The pre-correction fixture is not an artifact-builder input.

## Independent source-only execution

I copied the runner's exact 15 required inputs into
`/tmp/npu14-portable-source-only-20261003.5C3zEP`. The copy contains the runner,
existing source loader and build-profile list, the selected patch files, the
actual-C harness, and the historical fixture. It has no `.git`, object store,
alternates file, or kernel source tree. The commands unset both optional local
source-tree variables and use `--skip-optional-local-fixtures`, so the two
host-default private tree paths are skipped without being probed. The runner
still fetches the four raw source files from the pinned public kernel revision
and composes the explicit native-eight, NPU13, and NPU14 selected paths.

| Invocation | Observed `sys.flags.optimize` | Result |
| --- | ---: | --- |
| `python3 -B tools/hardware/test-npu-system-resume-error-unwind.py --skip-optional-local-fixtures` | 0 | 12 actual-C compile/run jobs passed |
| `python3 -O -B tools/hardware/test-npu-system-resume-error-unwind.py --skip-optional-local-fixtures` | 1 | 12 actual-C compile/run jobs passed |
| `PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-npu-system-resume-error-unwind.py --skip-optional-local-fixtures` | 1 | 12 actual-C compile/run jobs passed |

The 12 jobs per invocation comprise baseline, pre-correction, and corrected
extracted C for both BOOT_IOCTL and runtime-PM/STM configurations at `-O0` and
`-O2`: 36 successful C compile/run jobs total. The corrected tests preserve the
source-order check that CPU-on/STM uncertainty returns before interface or
firmware-loader teardown; the tracked historical fixture reproduces the prior
loader-shutdown-before-quarantine ordering. The BOOTUP caller's existing
host-shim refusal/error-path cases remain exercised, but no actual BOOTUP is
run.

Host compiler: `/usr/bin/cc`, Ubuntu GCC 13.3.0,
SHA-256 `1b99826121ae6682a634e5efe09bd3e3df58ce58e0b28f849114ab5b89139c26`.

## Negative controls and failure ordering

Each of the following was run from a separate copy of the source-only input
tree with `--skip-optional-local-fixtures`:

- Missing historical fixture: rejected with exit 1 before public fetch and C.
- One-byte digest tamper: rejected with exit 1 before public fetch and C.
- Historical fixture replaced by a symlink: rejected with exit 1 before
  public fetch and C.
- Historical fixture expanded to 524,289 bytes: rejected with exit 1 before
  public fetch and C.

With the valid fixture, explicit missing
`S22_NPU_PROBE_SOURCE_TREE` and
`S22_NPU_SYSTEM_COMPOSED_SOURCE_TREE` paths were each rejected even with
`--skip-optional-local-fixtures`. In these two cases the runner completed
public fetch and composition, then failed on the explicitly supplied path
before any C compile. Thus the option skips only auto-discovered defaults; it
does not forgive explicit malformed inputs.

I also extracted the old `legacy_npu14_patch()` implementation from the
historical runner revision `9553c8b7f1e7a7a0afd5d8d208a53aed2958d3ef` and
executed it with the same no-history source-only root. Its `git show` lookup
failed as previously documented with
`RuntimeError: frozen pre-correction NPU14 patch commit is unavailable`,
before source fetch or C compilation. This was a review-only historical
reproduction; the current runner itself has no `git show` fixture lookup.

## Limits retained from the source review

The portability change does not modify kernel source or the C harness. The
prior source review remains **BLOCKED** beyond this test-portability scope:

- The pinned image-loader shutdown interface returns `void`; the source
  cannot prove provider/S2MPU permission release completed before `FW_LOAD`
  ownership is cleared on the known-on path.
- `npu_system_release()`/device remove do not use the new stage quarantine.
- Firmware report/profile storage is file-static and its current free helper
  is a TODO that returns zero; cross-device/reprobe ownership is unproven.
- Extracted helper, PM, provider, and refcount behavior is modeled by host
  shims. No actual BOOTUP, firmware transition, provider call, kernel/module
  compile, phone/SSH/ADB access, or device run occurred.

The portability claims in the new research section and receipt match the
independent matrix and fixture controls above. A minor unrelated prose nit in
the research note repeats the sentence describing the allocator fixture in
the file-static-buffer subsection; it does not change the recorded limitation.

## Disposition

Clear only the portable source-only host-regression route and its fixture
handling for the stated 15-file input set. Preserve the historical source
review’s remaining blocker status and all NPU boot/deployment refusals.
