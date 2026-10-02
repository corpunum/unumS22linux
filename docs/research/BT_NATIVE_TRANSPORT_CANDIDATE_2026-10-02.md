# Native BT transport candidate: host build record

Status: one bounded host crosscompile succeeded. This is a distinct, unexecuted candidate artifact; it is not staged, deployed, or authorized for a phone or powered-hardware test.

The build used source commit `79c539ea55a1663e946ed881606232519873fd93`. All eight pinned C source inputs were clean and matched their SHA-256 pins at preflight and after compilation. The candidate uses the reviewed bridge source fingerprint `32bfebfc96864b6f5150195518fc7da996f62b7b21311d79c9bfea2725f9b1a1`. The builder and test were added as unrelated worktree files; the source gate allowed them without weakening checks on the selected C inputs.

The sole compile was the existing standalone probe route: AArch64 GCC 13, `-static -std=c11 -Wall -Wextra -Werror -O2`, one source target, and the existing private include-root identifier. The exact compiler driver SHA-256 was `cd90adc7801f4595267f61a5d25bd3a0c6beb2f9f1f107ab919a97a12972dc9a`; selected `cc1`, `collect2`, assembler, linker, and static runtime hashes are in the sanitized receipt.

## Artifact and input evidence

The output is identified only by its private build-directory name, `bt-native-transport-candidate-20261002`. The executable is `bt-qca6490-hci-bridge-probe-native-20261002`:

- SHA-256: `74b39343eaa0cd4e7176fb0fef0d6a30ee3e6abcea8954f68331381e719d8c61`
- GNU build ID: `c5c9be207d9a957fc52734f056c510218ac01be5`
- ELF64 little-endian AArch64 `ET_EXEC`, statically linked, 1,042,008 bytes, owner-only mode `0700`
- independent `readelf` inspection found no interpreter or dynamic segment; the artifact was not executed

The build pinned the eight public C input hashes and two external private-header hashes. Private inputs remain outside the worktree and are identified only by `qca_patch_profile` and `runtime_nvm_profile` plus their SHA-256 values in the sanitized receipt; no private header path or content is published.

The compiler dependency scan and compiler-produced `-MD` file resolved to the same content-hash map before and after compilation: 208 files total (8 public source files, 2 private headers, 198 system headers), totaling 1,835,278 bytes. A canonical manifest of every dependency identity and content hash was retained in the owner-only candidate output; its SHA-256 is `25dfe4c980f6bfe85bf25dee9a542c2ce88c19f3355cbac440180b7ea2ae9a95`. Public source identities are relative paths, private identities are labels, and system-header path identities are SHA-256-derived labels. The manifest contains no absolute paths or file contents.

The selected GCC driver components (`cc1`, `collect2`, assembler, and linker) were path- and content-pinned. The compiler-selected CRT objects and static `libc.a`, `libgcc.a`, and `libgcc_eh.a` were fingerprinted; the link map showed the expected CRT/libc/libgcc inputs and their hashes. These checks describe this captured toolchain/input closure; they are not a claim that arbitrary hosts or system environments produce a bit-for-bit reproducible artifact.

The builder SHA-256 is `7d17e06534356bfbd60c12f29a70bbfd859a8305db9231838122dcaf6c1ff9cf` and the test SHA-256 is `e312290aceebd44da20c8030ca3e05f0a3b897c97f02c9cd5c82509bc22a9a42` as measured after the compile. No builder edits were made after the compile in this work session, but a contemporaneous helper digest was not captured by the build invocation. Treat the post-run helper digest as an identity clue, not independent proof of the exact executed helper bytes. The dependency manifest was also materialized after the compile from the retained compiler `-MD` file; its canonical content digest was checked against the pre/post digest returned by the build. No source, private input, toolchain, artifact, or build command was changed for that bookkeeping step.

## Test and boundary record

The hardware-free suite passed 13/13 tests in each of three interpreter modes: normal, `-O`, and effective `PYTHONOPTIMIZE=1` (`sys.flags.optimize == 1` in the last two). Ten portable tests use temporary Git fixtures and real subprocess environment-refusal checks. Three optional exact-local-fixture tests also passed here, covering the pinned private headers, GCC/runtime/tool components, full dependency scan, and `--check-only` preflight; these three skip when those private inputs or the exact cross-compiler are unavailable elsewhere.

The prior runner and consumed trial remain unchanged: the runner still pins bridge SHA-256 `476f148246dfac8330f7ade2790627b64a38ee7b1cb4f713934b6d438864a7a4` and old artifact SHA-256 `f4ba76613e1339314898ebbf067338d846ed9ee231907a44306b82f54f2f1684` / build ID `a5be9451d95335ae2a5d292d721a766208f55a87`. The earlier trial identity remains at one successful attempt with its original receipt hash. This candidate has a different name, output location, bridge hash, and artifact hash; the old runner does not accept it.

No candidate execution, Bluetooth HCI transaction, device access, staging, upload, firmware operation, or powered test occurred. Successful host compilation proves only that this selected source set compiled and linked under the pinned host toolchain. It does not establish controller behavior, link reliability, device compatibility, or deployment readiness.

## Subsequent review corrections (not a rebuild)

Independent review identified missing contemporaneous helper/invocation evidence and unchecked Python startup controls in the historical build. These remain historical limitations; changing a helper now cannot attest to an earlier invocation. The retained artifact and original receipt are unchanged.

Future CLI invocations require `python3 -I -S -B tools/hardware/build-bt-transport-candidate-20261002.py --check-only` (or the existing explicit build option). Isolated startup excludes injected Python paths, and `-S` disables site customization before the helper is imported. This requirement does not cryptographically attest to executed helper bytes, interpreter, or the absence of a pre/post input race; the original review limits still apply.

The frozen 107-invocation host run at `5d15a0b` passed 105 and failed two instances of the optional local preflight test (normal and optimized): the test inherited the host runner's controlled `TMPDIR`, which the production builder correctly refuses. A focused invocation reproduced that failure. The test now supplies the existing allowlisted child environment and explicitly preserves its interpreter optimization mode. Separate real CLI regressions require injected `TMPDIR` and `CPATH` to remain refused. A startup fixture checks that isolated help does not execute injected `sitecustomize`; a nonisolated preflight is refused. These changes do not compile or execute the candidate.
