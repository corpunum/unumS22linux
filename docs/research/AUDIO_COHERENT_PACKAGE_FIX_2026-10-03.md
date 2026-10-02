# Audio coherent package follow-up fixes — 2026-10-03

## State and scope

This follow-up addresses two deterministic preflight/host-tool blockers in the
three-module packager. It changes only the existing builder and its focused
test, plus this note. The packager has not been run against the retained camera
image or candidate modules; no candidate CPIO, ramdisk, recovery image, or
output directory was created. The earlier author worktree and commit
`291a892d79ad07c133cb2c83e9e5b1aaf342984e` remain unchanged.

No kernel/Image build, module execution or load, phone/SSH/ADB operation,
firmware, deployment, AVB-footer creation, or boot activity occurred. The tiny
boot image described below is a temporary synthetic header-v2 fixture created
only by the pinned public `mkbootimg.py` smoke test.

## Pre-fix reproductions

The pinned static-preflight helper defines its report `name` as the basename
of the caller-provided `relative` label (`PurePosixPath(relative).name`), not
as the ELF module's internal name. With the selected external WLAN, a read-only
call using label `selected external Lineage WLAN` returned that exact report
name, while pinned `modinfo -F name .../wlan.ko` returned `wlan`. The old
builder compared the report label with the required internal name `wlan`, so
it would reject this correctly pinned WLAN every time. This was a reporting
identity mismatch, not evidence of WLAN ABI incompatibility.

The old isolated child invocation was also reproduced directly, without a
candidate input:

```text
python3 -I -S -B /home/corpunum/s22-linux/tools/mkbootimg/mkbootimg.py --help
exit 1
ModuleNotFoundError: No module named 'gki'
```

The exact imported source is
`/home/corpunum/s22-linux/tools/mkbootimg/gki/generate_gki_certificate.py`,
2,991 bytes, SHA-256
`1bb1feec68a13da18d581aa2c631798f86f6bc10b55d587b2dd31446a0f8a203`. It has
no `gki/__init__.py`; the Android `mkbootimg.py` imports
`gki.generate_gki_certificate` at module start. Pinning only `mkbootimg.py`
was therefore incomplete for the actual isolated invocation.

## Narrow fixes

The WLAN check now calls the already-pinned `modinfo -F name` tool separately
and compares that internal ELF name with `wlan`. The preflight's human-readable
label is retained separately as `inspection_label`; it is no longer treated as
module identity. A regression test accepts `wlan` despite the distinct label
and rejects both an unrelated internal name and the label itself as a false
identity.

The builder now pins and records the GKI certificate helper SHA-256 alongside
the pinned `mkbootimg.py` identity, and verifies both sources before starting
the child. It still launches Python with `-I -S -B`; the small bootstrap adds
only the explicit pinned Android tool directory to that child's `sys.path`,
sets the script argv, and runs the verified `mkbootimg.py`. It does not use a
general `PYTHONPATH`, remove isolated mode, substitute a GKI stub, or bypass
signature handling. Header-v2 creation does not invoke GKI certificate
generation; the imported upstream helper is required for the script's import
contract.

## Regression evidence

Seven new tests cover the separate WLAN internal-name check, rejection of a bad
GKI-helper hash before child launch, missing-helper failure without a success
output, the isolated import bootstrap with a controlled no-`__init__.py`
namespace-package fixture, actual pinned-tool `--help`, and actual pinned-tool
creation of a tiny synthetic Android header-v2 image. The actual-tool checks
skip explicitly only when either pinned public-tool file is absent; if present,
both hashes and non-symlink file identities must match before the checks run.
The synthetic import-contract test remains runnable without the external tool
fixture.

The 15-test focused suite passes in all three modes:

```text
TMPDIR=/tmp python3 -I -S -B tools/hardware/test-build-audio-coherent-recovery.py
TMPDIR=/tmp python3 -O -I -S -B tools/hardware/test-build-audio-coherent-recovery.py
TMPDIR=/tmp PYTHONOPTIMIZE=1 python3 -S -B tools/hardware/test-build-audio-coherent-recovery.py
```

The environment-optimization run deliberately omits `-I`, which ignores
`PYTHONOPTIMIZE`; its interpreter was checked at effective optimization level
1. The builder CLI's isolated startup gate remains covered by a subprocess
regression. On this host, the actual pinned `mkbootimg.py --help` and tiny
header-v2 fixture tests both ran and passed; their temporary files were
discarded. No real package command was run.

These fixes clear only the two deterministic host-tool blockers. They do not
change prior static ABI evidence or limitations, and are not a package, module,
device, radio, audio, or deployment acceptance result. Independent review of
this exact follow-up and coordinator GO are still required before the first
real host packaging run.
