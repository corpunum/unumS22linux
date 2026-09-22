# NPU normal BOOTUP preflight — 2026-09-22

Host-only result from `tools/hardware/npu-boot-preflight.py`. The checker does
not open `/dev/vertex*`, invoke an ioctl, stage firmware, reboot, or use a
phone. It exits nonzero until the source-level BOOTUP unwind is independently
fixed.

## Verified prerequisites

The captured exact configuration is:

```
CONFIG_NPU_USE_BOOT_IOCTL=y
CONFIG_NPU_USE_HW_DEVICE=y
CONFIG_DSP_USE_VS4L=y
CONFIG_EXYNOS_IMGLOADER=m
CONFIG_NPU_MAILBOX_VERSION=9
CONFIG_NPU_SECURE_MODE is unset
```

For this configuration, `npu-binary.h` selects `AIE.bin`. Because
`CONFIG_EXYNOS_IMGLOADER=m`, the active signature path in `npu-binary.c` calls
`imgloader_boot()` with `imgloader.fw_name = "AIE.bin"`; the direct
`request_firmware()` branch is not the active compiled path. Firmware search
therefore depends on the recovery kernel's imgloader/firmware-class setup and
its mounted root, not the host filesystem and not merely the existence of a
host `/vendor/firmware` directory.

The private host artifact checks pass:

* `AIE.bin`: SHA-256
  `a9843bbf520c08263c563f3200f0cbceb09c68fbdd1279d24236c3da704d9dc2`.
* `dsp_reloc_rules.bin`: SHA-256
  `468c0d2cc7c3a11fa80c3799385217bb4c36bb4cc369e754f73baae50e8b0a5d`.

`dsp_reloc_rules.bin` is loaded by `dsp_kernel_manager_dl_init()` during
`npu_hwdev_dsp_init(on=true)`, not by the NPU-only `hids=0x2` normal path. It
is retained as a module/combined-DSP prerequisite, not a normal NPU-only
BOOTUP blocker. `vectors.bin` has no caller in the pinned normal path.

## Early ordering and failure risk

The normal path is `npu_system_probe()` -> `npu_system_open()` -> platform
start/resume. On cold resume, `npu_system_resume()` allocates firmware log
memory, clears the mailbox area, and calls `npu_firmware_load()`; under the
active imgloader configuration this invokes `imgloader_boot()`. Any firmware
load error exits before the VS4L BOOTUP ioctl path.

For `NPU_NW_CMD_POWER_CTL`, `npu_hwdev_normal_bootup()` currently does:

1. `npu_hwdev_bootup()` (hardware refs),
2. `__vref_get()` (vertex boot ref),
3. `npu_sessionmgr_regHW()`,
4. `npu_session_NW_CMD_POWER_NOTIFY(true)`,
5. STM/HWACG enable and normal-count publication.

The current error label after step 4 only returns; it does not prove shutdown,
vertex-ref release, session-manager unregister, or lock release. A positive
POWER enqueue can also wait indefinitely on the raw session callback. Thus a
60-second recovery reboot scheduled before an unproven ioctl is not a safe
containment mechanism: a missing/invalid firmware failure can take a different
early path, while a POWER callback timeout/failure can leave refs and locks in
the kernel before the reboot, and panic/BUG paths are not guaranteed to honor
the schedule. Holding the vertex fd does not solve those kernel-owned refs.

## Safe host-only gate

Run:

```sh
python3 tools/hardware/npu-boot-preflight.py
```

Expected current outcome is a passing host preflight with matching required
artifact/config/source-route checks, `live_probe_validated: false`, and a
non-empty `known_lifecycle_gaps` section; it also reports
`device_access: false` and `staging: false`. A future device probe requires,
at minimum, a source-validated BOOTUP error unwind and callback lifetime
repair, confirmation of the recovery image's imgloader firmware search root,
and an externally supervised shutdown plan that does not rely on a userspace
alarm to cancel queued work. No probe C program is supplied or executed here;
there is no safe “hold fd then reboot” protocol until those kernel ownership
conditions are met.
