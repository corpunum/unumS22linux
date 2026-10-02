# NPU full lifecycle and shutdown profile — 2026-10-02

## Result

Added an explicit fifth, derived profile patch that applies after the four
reviewed lifecycle/refcount/callback/probe patches without changing any frozen
historical patch. On the exact source fixture below, ordinary `git apply
--check` and ordinary `git apply` succeed for all five patches in order. The
frozen shutdown-ownership patch still independently fails ordinary
`git apply --check` at `npu-vertex.c:313`, preserving the known overlap rather
than hiding it with force, fuzz, a path filter, or binary source rewriting.

The derived profile retains both sides of the overlap:

- Lifecycle close keeps `POWER` state clearing and reports the saved
  `POWER_NOTIFY` error after completing teardown. The ownership sequence stays
  boot-ref release, hardware shutdown, then session close; uncertain shutdown
  retains the session and secure-memory ownership.
- Normal bootup retains the lifecycle patch's reverse cleanup and conditional
  `lock_held`/`out_unlock` handling. The four reviewed post-lock quarantine
  gates and two wait-reacquisition gates are composed into those paths.
- Device/open/boot quarantine, recovery poisoning, recovery ref draining,
  secure boot cleanup, secure and normal bootdown failure retention, and the
  ownership patch's remaining reviewed paths are retained. The existing
  extracted-C ownership cases run against the combined final C, not against a
  Python behavioral model.

## Pinned inputs and patch order

The exact Lineage base is `4e5c5ad7d950e4de0688b5663965f2075654b2ad`. The
clean local source fixture is
`/home/corpunum/s22-workers/camera-kernel-build-20260927` at
`3fca50941422439b2019db2e4a3dc1016b2138a1`; its NPU driver files are verified
byte-identical to the pinned base. The two fixture loaders' overlapping bytes
are compared before their union is materialized in a fresh temporary fixture.
The stack fixture paths are written first; the remaining non-overlapping paths
from the verified loader union are added afterward, so every overlapping core
path is written once and the complete merged baseline exists before any patch
is applied.

The test can also run without either source-tree variable: the existing stack
and shutdown loaders fetch their individually SHA-verified files from the
pinned public base, with their existing per-file bounds and five-second
per-request timeouts. The shutdown fixture retains its 1 MiB aggregate cap;
the merged union is bounded by the stack-fixture byte total plus that cap, and
overlapping bytes must compare equal. A public fetch that is unavailable is
reported as `SKIP` with exit status 77; it is never represented as a passing
test. There is no retry loop. If one local source-tree variable is supplied,
the other loader is pointed at that same root; if both are supplied, their
canonical paths must match. The existing loaders still enforce the clean exact
derived HEAD and source hashes.

The tested order and SHA-256 inputs are:

1. `npu-session-lifecycle-fix.patch` —
   `1554436cb6624c542f9e04ac22a3b3545e55f94c59d3025ee6bdc1ec43168251`
2. `npu-refcount-lifecycle-profile.patch` —
   `8385e4210a807f96f972757cd6ca74a8077b0127ab112d8b877d012ccdc3cb7b`
3. `npu-default-boot-callback-fix.patch` —
   `f5ce216e34df11d8c6adee4a99c36d63f73593cf379e29de3a9de828ec2ee1e7`
4. `npu-probe-unwind-fix.patch` —
   `d3e2e590d4d3c956b10c724a15db996dacd07def204332f50a1c0513f56b4948`
5. `npu-shutdown-lifecycle-profile.patch` —
   `b986e1896305fda55f1d702ed6f12dde646e4a84b3ce91009203b9d77b7a00e7`

The frozen ownership input remains unchanged at SHA-256
`a8af77122b4049fd38e21adfd01a8577d3e8f9bef1f68d0cfa091a5884a4d9f3`.
The profile changes only `npu-device.c`, `npu-device.h`, `npu-hw-device.c`,
and `npu-vertex.c` in the temporary source fixture.

## Verification

Run from this worktree with both source-tree variables pointing to the exact
fixture for the local-source route:

```sh
S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
S22_NPU_SHUTDOWN_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
python3 tools/hardware/test-npu-full-lifecycle-profile.py
```

To exercise the default public-SHA loader route, unset both variables and run
the same command. `STACK.run_preflight` requires a source directory and does
not accept `None`; the test supplies its exact temporary five-patch fixture
for `--source`, so no local/public source identity is fabricated.

The local-source route passed with both variables set to the same fixture,
with only `S22_NPU_PROBE_SOURCE_TREE` set (`python3 -O`), and with only
`S22_NPU_SHUTDOWN_SOURCE_TREE` set (`PYTHONOPTIMIZE=1 python3`). The default
public route also passed with both variables unset; the loaders reported the
public pinned base `4e5c5ad7d950e4de0688b5663965f2075654b2ad`; the shutdown
loader's exact-source subset was 147,641 bytes, and the deduplicated union was
1,214,674 bytes across 19 paths. Every successful run applied and checked the
complete five-patch sequence, checked the frozen-patch blocker and six
quarantine points, then compiled and ran the actual extracted combined C at
both `-O0` and `-O2` with warnings as errors. Negative loader guardrails
rejected mismatched local roots and deliberately altered source bytes.

The public-unavailable handler was exercised with a simulated bounded-fetch
failure and returned 77 with an explicit `SKIP`; the actual default public run
was available and passed.

The extracted-C coverage includes the existing 11 refcount transaction cases
and five normal-bootup lock-path cases, plus all 16 shutdown/recovery ownership
cases. The full-profile harness adds a seventeenth shutdown case: a failing
`POWER_NOTIFY` still completes the ordered close and returns the saved
lifecycle error. The ownership cases cover recovery failure/success, unequal
leaf references and DNC exclusion, zero-ref and poisoned-final-put handling,
recovery caller retention, sticky quarantine, uncertain-close session
retention, ordinary/already-down close, secure bootup cleanup and failed
resume, both late-quarantine races, and secure/normal bootdown failure
retention. They were run against the combined extracted C at both C
optimization levels in every Python mode.

The preflight also remains deliberately refused:
`artifact_preflight_pass=false`, `bootup_ready=false`, and
`bootup_authorized=false` (status 2; required local firmware fixtures are
absent). No firmware was created or substituted.

## Limits that remain open

This is source composition and host extracted-C evidence only. It is not a
kernel build, module-load, runtime, or device acceptance result. In particular:

- Pinned `npu_device_shutdown` still masks errors from early/protocol close
  paths; this profile does not claim to fix that return contract.
- VFS/session-manager/remove lifetime interactions remain unproven.
- The normal bootdown reference/count mismatch remains unproven.
- BOOTUP stays unauthorized until a separate reviewed preflight can establish
  readiness from the required real artifacts; this work does not fabricate
  firmware or authorize a device action.

No SSH, ADB, phone, module, package, service, reboot, kernel build, or push
operation was used for this profile.
