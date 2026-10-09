# Independent NPU system-resume unwind review — 2026-10-03

## Verdict

**BLOCKED.** The frozen NPU14 patch tracks many failed resume stages more
carefully, but its unwind can call the void image-loader shutdown before it
checks whether the NPU CPU-on operation left the CPU in an unknown state. On
the pinned SM-S901B configuration, this reaches the image-loader path that can
release S2MPU firmware permission while the extracted control still models the
CPU live. The later quarantine preserves the CPU uncertainty bit, but cannot
undo or report that earlier permission transition. This is a host-source
finding, not evidence that a provider call or remote NPU shutdown occurred on a
device.

There is also a separate error-reporting limit: the image-loader shutdown API
is `void`; the pinned provider logs and returns early if permission release
fails, while NPU14 unconditionally clears its `FW_LOAD` stage. The extracted
tests substitute a successful helper shim and do not cover this provider
failure. Quarantine also does not guard the remove/release path.

## Frozen inputs and identity

- Reviewer selection: `gpt-6-luna`, reasoning effort `max`, explicitly selected
  configuration only; this is not backend attestation.
- Review worktree: branch `codex/s22-npu-system-resume-review-20261003`, frozen
  import `4edfc5c34242ee7d0b54c4f1a4691a0afefa291a` (clean before this review).
- Author input: frozen commit
  `4a22948184f101f2bd80d2f44eef44b46004b790`.
- Pinned kernel: `4e5c5ad7d950e4de0688b5663965f2075654b2ad`.
- Composed source fixture checked by the runner: NPU12 commit
  `e9c3016233a72ceccb13e537f0b7ef72426582b9`, followed by NPU13 then NPU14.
- Frozen author-file SHA-256 values:

  | File | SHA-256 |
  | --- | --- |
  | `tools/hardware/npu-system-resume-error-unwind.patch` | `464a78b43f7ef0cc7e26b5f69075980460211f9c789d0a418b36d44be548968e` |
  | `tools/hardware/npu-system-resume-error-unwind-harness.c` | `09eab198f0cd83b0f55765b115e7d8f682ccad04fd518ba671843b62b2f72d38` |
  | `tools/hardware/test-npu-system-resume-error-unwind.py` | `116448ad91e317f6a1f8f3f499e55dd4bf19f5f36a99abed05745043b6a3eb92` |
  | `docs/research/NPU_SYSTEM_RESUME_ERROR_UNWIND_2026-10-03.md` | `edf1629d7a4b896718ffdd5edab50ffe8d83b5feb7084e5a5f67e71d6925f0f6` |

  The pinned source identities most relevant to these findings are
  `npu-system.c` `96eaa6bf1511f3e6414e3e376d62592229454a2ea1687d760b7bb8e5952b1a05`,
  `imgloader.c` `e9898c3ac9b0c5210603028b7c0b7bc3623869762a99f35438d3d480d33e27f7`,
  `npu-binary.c` `87b08d398d3468de827853544177af5a1264a35ee69a4648a728e35306ef5d36`,
  `npu-device.c` `98be21e422ca864cc971dfe6a78e71b292d7691cb625c1f029bf4502100644ab`,
  and `s5e9925_defconfig`
  `de87dbdff5a4082b2aa6fd511a69b9766ddfb0369738c76885c9766178f2b4f5`.

## Source findings

### 1. Firmware-loader teardown precedes CPU uncertainty quarantine

In the frozen patch, `npu_system_soc_resume()` sets
`NPU_SYS_RESUME_SOC_CPU_ON_UNCERTAIN` before `npu_cpu_on()` and retains that
marker if the operation returns an error (`npu-system...patch:64-71`). The
outer `npu_system_resume()` error path then invokes `npu_system_suspend()`
while preserving the initiating errno (`:178-192`). But suspend processes
`NPU_SYS_RESUME_FW_LOAD` first, calls `npu_imgloader_shutdown()`, and clears
that stage (`:234-242`); only afterward does it invoke the SoC suspend gate
(`:244-256`). That gate notices the CPU-on uncertainty and returns
`-EUCLEAN` (`:88-93`). Thus the quarantine is too late to prevent the earlier
image-loader action.

This is on the selected target path, not only the alternate STM path. The
pinned `s5e9925_defconfig` sets `CONFIG_NPU_USE_BOOT_IOCTL=y` (line 6988),
`CONFIG_EXYNOS_IMGLOADER=m` (6235), `CONFIG_EXYNOS_S2MPU=m` (6239), and
`CONFIG_EXYNOS_NPU_DRAM_FW_LOG_BUF=y` (6961); STM-enable code is compiled out
by the BOOT_IOCTL conditional. The pinned NPU DT node advertises
`samsung,imgloader-s2mpu-support` (line 15997).

The provider trace is specific but not a runtime observation:

- `npu_imgloader_shutdown()` is a void wrapper around `imgloader_shutdown()`
  (`drivers/vision/npu/core/npu-system.c:75-84`).
- `imgloader_shutdown()` calls its S2MPU release helper when enabled and
  returns early on a reported failure (`drivers/soc/samsung/imgloader.c:123-131,
  271-283`). The provider API is void, so NPU14 cannot observe whether this
  operation succeeded.
- The NPU image-loader ops set `.shutdown = NULL`
  (`drivers/vision/npu/core/npu-binary.c:103-110`), and this provider's
  `imgloader_notify()` is a no-op (`imgloader.c:90-96`). Therefore this review
  does **not** claim that a remote NPU OFF event was sent or observed. The
  negative control counts only entry into the host-side shutdown wrapper; it
  does not execute or attest the S2MPU provider.

An independent bounded negative control compiled the actual extracted patched
functions in the harness's `TEST_BOOT_IOCTL` variant and injected a partial
CPU-on result (`-ETIMEDOUT`, modeled CPU still live). It added an oracle that
requires zero image-loader shutdown calls before the uncertainty gate. The
oracle failed as intended: one shutdown-wrapper call was counted while the
CPU remained live; no CPU-off inverse was called, and the later SoC uncertainty
guard quarantined the state. This reproduces the ordering defect in the
configured variant using helper counters, not hardware/provider execution.

The bounded BOOT_IOCTL CPU-off control is distinct: on a normal known-on
shutdown, the wrapper is called once before the first CPU-off attempt. When
that attempt is injected to fail, the CPU remains modeled live and the
CPU-off-uncertain bit is retained; a second suspend makes no second wrapper or
CPU-off attempt. This proves the no-repeat behavior *after* quarantine, not
zero initial shutdown before CPU-off uncertainty. The marker is only created
when that first inverse is attempted. The same firmware-before-SoC source order
exists in the non-BOOT_IOCTL build, but its STM-enable uncertainty is not the
selected default-config trigger.

### 2. A void provider failure is treated as completed cleanup

At patch lines `234-242`, `NPU_SYS_RESUME_FW_LOAD` is cleared after
`npu_imgloader_shutdown()` returns. The wrapper and provider both return
`void`. In the pinned provider, an S2MPU release error is logged and causes an
early return before later provider steps. The caller therefore has no result
to propagate and clears the ownership stage even on that provider failure
path. NPU14's test shim increments a counter and returns no failure; it cannot
exercise this provider behavior. This is a separate source-level gap in the
claim that cleanup only clears an owner after a successful inverse.

### 3. Remove/release is outside the new quarantine checks

The patch guards ordinary open, close, resume, and suspend against residual
stage bits, but does not alter `npu_system_release()`. In pinned source,
`npu_device_remove()` calls it and returns zero regardless of its logged
release error (`drivers/vision/npu/core/npu-device.c:1022-1074`). The release
routine destroys the wake lock, performs configuration-dependent PM/clock
release, releases SoC STM state, and releases the image-loader descriptor
without checking resume-stage ownership (`npu-system.c:1435-1468`). No unbind,
module unload, or remove behavior was executed here; source inspection shows
the new quarantine does not cover that object/provider lifetime path.

### 4. Static log-buffer ownership is only partly testable

The actual allocator uses file-static report/profile buffers and can return on
the second allocation after the first buffer is already allocated
(`npu-system.c:560-610`). NPU14 marks a partial global allocation uncertain,
which the extracted test exercises. But the pinned free implementation is a
TODO returning zero (`npu-system.c:613-617`); NPU14 consequently clears the
buffer ownership stages after this no-op reports success (`patch:277-295`).
The pinned `npu_memory_close()` is also a TODO/no-op
(`npu-memory.c:253-259`). Therefore I do not claim that current close frees
the buffer or causes an immediate use-after-free. The tests inject a helper
failure for the free branch and reset their static fixture between cases;
they do not establish cross-device/reprobe ownership or a real buffer-free
lifecycle. A zero return from the present TODO is not evidence that the
file-static storage was released.

### 5. Other stage behavior reviewed

The patch preserves the initial resume errno while logging a separate unwind
error (`:178-192`), refuses a new resume/open/close while stage bits remain,
and clears CPU/STM/interface inverse ownership only after successful inverse
returns. It uses one-shot `test_and_set_bit()` guards for fallible inverses.
The STM path is excluded in the selected BOOT_IOCTL configuration; in the
alternate build the pinned `npu_stm_disable()` decrements a shared
`enable_cnt` before its fallible SFR-disable operation
(`npu-stm.c:692-715`), so avoiding a blind retry is materially important.
Runtime-PM callback behavior and the NPU12 default-boot caller were extracted
and exercised with host shims; they do not establish real PM-core, clock,
IRQ/workqueue, or hardware behavior.

## Independent verification

The runner verified raw pinned source digests, ordinary patch checks/application
on the clean derived fixture, and ordered NPU13-then-NPU14 application on the
composed NPU12 fixture. It extracted the actual pinned system/device/hw-device
function bodies into the author harness and ran baseline and patched C at
`-O0` and `-O2` for BOOT_IOCTL and runtime-PM configurations.

| Python invocation | `sys.flags.optimize` | Source route | Result |
| --- | ---: | --- | --- |
| `python3 tools/hardware/test-npu-system-resume-error-unwind.py` | 0 | Bounded raw-pinned public fetch, with required local fixtures present | 8 C compile/run jobs passed |
| `python3 tools/hardware/test-npu-system-resume-error-unwind.py --local-only` | 0 | Clean derived pinned fixture | 8 C compile/run jobs passed |
| `python3 -O tools/hardware/test-npu-system-resume-error-unwind.py --local-only` | 1 | Clean derived pinned fixture | 8 C compile/run jobs passed |
| `PYTHONOPTIMIZE=1 python3 tools/hardware/test-npu-system-resume-error-unwind.py --local-only` | 1 | Clean derived pinned fixture | 8 C compile/run jobs passed |

Total: 32 bounded extracted-C compile/run jobs. The author-reported earlier
public fetch timeout before compilation remains a historical timeout, not a
pass; this independent public fetch did succeed. It does **not** establish a
portable public-source fixture: after fetching, the runner still unconditionally
requires the private clean-derived and composed NPU12 worktrees
(`test-npu-system-resume-error-unwind.py:195-215`). It therefore cannot run in
a hosted environment lacking those fixtures as written. Separately, the
runner exits a `TemporaryDirectory` context before calling
`build_composed_source()` (`:220-222`), which then writes/recreates paths under
that expired destination (`:167-180`); this is a test-harness tempdir-lifetime
defect, not an additional kernel finding.

No NPU14 kernel/module build, firmware load, phone/ADB/SSH access, S2MPU
provider operation, or physical CPU/STM/clock transition was performed. C
helpers, PM callbacks, allocations, and provider effects in the harness are
controlled host shims. The selected worker configuration is selection
evidence, not backend attestation.

## Disposition

Do not treat this patch or its tests as NPU14 acceptance or authorization for
deployment. The CPU-on rollback ordering and void image-loader error reporting
need correction and focused regression coverage; remove/release lifetime also
needs an explicit ownership policy. No phone trial, boot, firmware action, or
NPU_BOOTUP is authorized by this review.
