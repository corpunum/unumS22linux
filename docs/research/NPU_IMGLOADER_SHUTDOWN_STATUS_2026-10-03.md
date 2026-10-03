# NPU image-loader shutdown status

## Result and boundary

This host-only change closes the source-observed false-success gap between
`imgloader_shutdown()` and NPU suspend. The legacy exported `void
imgloader_shutdown(struct imgloader_desc *)` entry point stays intact for
existing callers. A new checked provider entry point returns a negative errno
for nonzero permission-release, optional callback, or notify results. NPU
suspend consumes that result and keeps `FW_LOAD` ownership when the provider
reports failure.

Before entering the provider, NPU suspend atomically claims a new
`FW_SHUTDOWN_UNCERTAIN` stage with `test_and_set_bit()`. It remains set for
every failure, including a failure after permission release may already have
partially succeeded. Later suspend returns `-EUCLEAN`; open, close, and resume
continue to reject residual ownership. A successful checked provider return
clears `FW_LOAD` first and only then releases the transient claim. This order
prevents a concurrent/reentrant suspend from repeating the permission-release
call while its first result is outstanding. A positive unexpected return is
normalized to `-EIO` in both the provider and NPU wrapper, so it cannot appear
as PM success.

The existing order remains: early CPU-on / (non-BOOT_IOCTL) STM uncertainty
refusal, interface close, checked image-loader provider call, then the
existing SoC suspend sequence. BOOT_IOCTL still requests CPU_OFF in that later
SoC stage; runtime-PM still disables STM before CPU_OFF. Subsequent clock,
firmware-buffer, and wake-lock cleanup is not reordered. Secure warm-boot and
non-cold paths retain their existing provider-skip behavior.

This result is only provider-reported status, not evidence of remote firmware
or CPU quiescence. The pinned NPU provider ops set `.shutdown = NULL`, and the
pinned `imgloader_notify()` implementation is a no-op. The later CPU_OFF is a
separate call which still must succeed. Host tests model that call; they do not
perform it.

The provider descriptor and ops structure layouts are unchanged. The one
affected header is `include/soc/samsung/imgloader.h`, which adds a prototype
and config-disabled inline stub for the new API. The additional exported
symbol is a deployment compatibility boundary: future provider and NPU
modules must be built and deployed as a matched pair, with symbol CRC/version
and dependency checks. This patch alone does not update or validate module
CRCs, build modules, deploy, or authorize replacing a running NPU module.

## Pinned inputs and selected composition

The raw public source base is LineageOS
`android_kernel_samsung_s5e9925` commit
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`. The runner fetches each source
from that exact SHA, enforces the existing per-file size cap, and verifies the
SHA-256 before composing only selected paths in temporary directories. The
composition is explicit: the existing native-eight tuple, NPU13, corrected
NPU14, then this provider-status patch. Every patch is digest-pinned and
selected-path application runs through ordinary `git apply --check` and
`git apply`; the runner does not query a local Git object as a source
fallback.

| Order | Selected patch | SHA-256 |
| ---: | --- | --- |
| 1 | `npu-session-lifecycle-fix.patch` | `1554436cb6624c542f9e04ac22a3b3545e55f94c59d3025ee6bdc1ec43168251` |
| 2 | `npu-refcount-lifecycle-profile.patch` | `8385e4210a807f96f972757cd6ca74a8077b0127ab112d8b877d012ccdc3cb7b` |
| 3 | `npu-default-boot-callback-fix.patch` | `f5ce216e34df11d8c6adee4a99c36d63f73593cf379e29de3a9de828ec2ee1e7` |
| 4 | `npu-probe-unwind-fix.patch` | `d3e2e590d4d3c956b10c724a15db996dacd07def204332f50a1c0513f56b4948` |
| 5 | `npu-shutdown-lifecycle-profile.patch` | `b986e1896305fda55f1d702ed6f12dde646e4a84b3ce91009203b9d77b7a00e7` |
| 6 | `npu-shutdown-error-propagation.patch` | `08374e96792f24d1e0e4fbca594bfce35537af8acace9296b66f27b531564e43` |
| 7 | `npu-mailbox-missing-callback-reclaim.patch` | `f109b57381b3f2afcf2638b518f50db59ef8c9f784debd949488c74f8ea5c39b` |
| 8 | `npu-mailbox-debug-walk-bounds.patch` | `20c700bfa11f13836c76c88cca28a4f8dfa459e5cf146292a814880cd5850b29` |
| 9 | NPU13 `npu-interface-open-unwind.patch` | `95e63b45d60e0a2611c1f2dcab4428e5658a03ff9954b8197d175ac6fec139cb` |
| 10 | corrected NPU14 `npu-system-resume-error-unwind.patch` | `f1af656f9e1b8ca2bf15e934031e0adf828c442267a17761ba64f7d7c611729a` |
| 11 | this patch, `npu-imgloader-shutdown-status.patch` | `e7355e8906b22f1decf00de59cd644aac7bfcf6d861d97cdb3ff9bf084428385` |

The native-eight patches do not alter the selected `npu-system.c` input; NPU13
and corrected NPU14 are then applied in order. The optional read-only NPU12
fixture at `e9c3016233a72ceccb13e537f0b7ef72426582b9` was checked separately
for exact commit, clean status, and equality of the five selected raw source
files. It is corroboration only; the public-source test route does not require
that private tree. Missing default optional fixtures are skipped, while an
explicitly supplied wrong fixture is rejected.

| Source | Raw pinned SHA-256 | After this patch SHA-256 |
| --- | --- | --- |
| `drivers/soc/samsung/imgloader.c` | `e9898c3ac9b0c5210603028b7c0b7bc3623869762a99f35438d3d480d33e27f7` | `5904d2ef6a27e9f27d7fe337a7dd47b59d5298cbb864c4e193a9f68fa14ba928` |
| `include/soc/samsung/imgloader.h` | `5b000a5eba96435dc011f239ea867334385c5ca94541d31b190e25b296440cec` | `b0c41549fd3781a00fca1c4257fe376876093f91e489b24cb16950116abbb76f` |
| `drivers/vision/npu/core/npu-system.c` | `96eaa6bf1511f3e6414e3e376d62592229454a2ea1687d760b7bb8e5952b1a05` | `c9305d36ce64ffe4f683dcaccdabc6a664a926e79cafba90f43ddddaa6862dd1` |
| `drivers/vision/npu/core/npu-binary.c` | `87b08d398d3468de827853544177af5a1264a35ee69a4648a728e35306ef5d36` | unchanged |
| `drivers/media/platform/exynos/mfc/mfc_core_ops.c` | `99c26285e07223a26625f006f89241c8b07b19772b0c4f9f49c965798778c669` | unchanged |

The caller audit found the existing exported provider void API remains
available; NPU's `npu_imgloader_shutdown()` wrapper is retained, the NPU ops
still have `.shutdown = NULL`, and the two direct MFC callers keep using the
legacy API. No descriptor/ops fields or layouts change. The new NPU suspend
caller uses the checked API only for its `FW_LOAD` teardown stage.

## Regression method and results

`tools/hardware/test-npu-imgloader-shutdown-status.py` extracts the real
provider permission-release helper, provider shutdown API, NPU wrapper, open
and close guards, `npu_system_suspend()`, and SoC suspend function from the
hash-verified public files. It applies the explicit NPU patch stack in a
temporary source fixture and compiles these extracted C bodies with host stubs
at `-O0` and `-O2`. The permission helper is the extracted source function;
provider callback, notify, CPU/STM, clocks, buffers, and wake-lock interactions
are controllable host seams. In particular, notify failure is injected at the
provider helper seam because the actual pinned notify helper is a no-op.

The pre-change negative controls reproduce three false-success paths: failed
permission release returns through the old void API, after which NPU clears
`FW_LOAD` and continues to CPU_OFF; callback failure is only stored in the
descriptor flag; and notify failure is only logged. The checked path verifies
negative errno preservation and positive-to-`-EIO` normalization for all
three provider steps. Permission-release failure stops before callback,
notify, and CPU_OFF. Callback/notify errors after the permission-release
attempt quarantine all remaining owner stages. Repeated suspend/open/close
does not retry provider permission release or tear down dependent resources.
A provider shim re-enters suspend while permission release is outstanding;
the atomic claim rejects the nested attempt and the helper is called once.
This deterministic re-entry check is not a threaded race stress test.

Controls also cover clean S2MPU and non-S2MPU routes, the image-loader
config-disabled stub, secure warm-boot skip, CPU_ON uncertainty refusal before
provider/interface calls, the legacy void signature/behavior, unchanged
interface/provider/STM/CPU_OFF/clock/buffer/wake order, and source-level
runtime BOOTUP refusal while resume ownership remains. The runtime BOOTUP
check asserts the existing resume owner guard returns `-EBUSY` before firmware
allocation. No attempted physical provider call is represented by any of
these controls.

The three required Python modes each passed 32 C compile/run jobs (96 total):

| Command | `sys.flags.optimize` | Result |
| --- | ---: | --- |
| `PYTHONDONTWRITEBYTECODE=1 python3 tools/hardware/test-npu-imgloader-shutdown-status.py --skip-optional-local-fixtures` | 0 | 32 jobs passed |
| `PYTHONDONTWRITEBYTECODE=1 python3 -O tools/hardware/test-npu-imgloader-shutdown-status.py --skip-optional-local-fixtures` | 1 | 32 jobs passed |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONOPTIMIZE=1 python3 tools/hardware/test-npu-imgloader-shutdown-status.py --skip-optional-local-fixtures` | 1 | 32 jobs passed |

Each set covers baseline/status at both C optimization levels and BOOT_IOCTL
/ runtime-PM variants for S2MPU-supported, non-S2MPU, and image-loader
disabled configurations; four additional secure-mode combinations cover
BOOT_IOCTL/runtime-PM and `-O0`/`-O2`. The effective C compiler was
`cc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0`. Two initially parallel Python
invocations encountered the pinned fetcher's five-second public TLS timeout
before compilation; sequential reruns completed all three required modes
against the same verified public pin.

## Limits and deployment boundary

These tests are host-side extracted-C evidence only. They do not run Kbuild or
module CRC generation, verify a kernel configuration/link, execute the
provider or S2MPU, transition firmware/CPU/STM, observe remote NPU shutdown,
test DMA or TrustZone state, or establish device acceptance. No phone, SSH,
ADB, USB, BOOTUP, firmware, provider, S2MPU, or TrustZone operation was
attempted. The additional export requires a future matched provider + NPU
module build and deployment review. Failure quarantine can retain the wake
source and block suspend indefinitely; no automatic retry, timeout, rollback,
or marker clearing is added.
