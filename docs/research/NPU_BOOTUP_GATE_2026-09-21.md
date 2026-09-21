# NPU BOOTUP gate — 2026-09-21

`tools/hardware/npu-boot-probe.c` verifies ABI constants on the host only.
It refuses execution before any device access. The host test is
`tools/hardware/test-npu-boot-probe.sh`.

## Exact ABI

Pinned kernel checkout `lineage/android_kernel_samsung_s5e9925` is SHA
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`. Its `vs4l.h` defines a 24-byte
`struct vs4l_ctrl` under `CONFIG_NPU_USE_BOOT_IOCTL` and
`VS4L_VERTEXIOC_BOOTUP` as `_IOWR('V', 13, struct vs4l_ctrl)`, numeric
`0xc018560d` on 64-bit Linux. NPU is `NPU_HWDEV_ID_NPU=0x2`; non-secure
boot-up is `ctrl=0` (both secure and up/down bits clear). All memory fields are
zero. These values describe the audited ioctl, not an executed operation.

## Firmware/search path

`npu-binary.h` selects `AIE.bin` with `CONFIG_DSP_USE_VS4L`. The recorded
kernel command line sets `firmware_class.path=/vendor/firmware`, therefore the
expected ordinary BOOTUP firmware path is `/vendor/firmware/AIE.bin`.
`AIE.bin` was recovered. The separate `vectors.bin` request belongs to the
firmware-test path, not ordinary BOOTUP; its absence is not a normal boot
blocker. This work writes no firmware and accesses no phone node.

## Lifecycle hazards and status

Open creates a session/queue and changes scheduler state. BOOTUP enters
`npu_system_resume()`, which allocates firmware buffers, loads firmware,
resumes the SoC/interface, and initializes mailbox/protocol. Its error path
sets emergency recovery but returns zero, so ioctl success is not boot proof.
`npu_device_bootup()` then has further `dsp_dhcp_init`, protocol-open, and late
open unwind paths. In the normal non-secure path, failures after
`npu_session_NW_CMD_POWER_NOTIFY(true)` can retain `boot_cnt` while the power
state bit is not yet set; release cleanup is therefore not a proof of safety.
The source also contains `BUG_ON(1)` in emergency recovery close paths.

On release, `npu_vertex_close()` sends power-notify false when powered, closes
the session, drops refs, and calls `npu_hwdev_shutdown()`; errors can leave
emergency state. The retained tool does not open the device or enter this
path. Kernel firmware and mailbox waits can be uninterruptible, so a
userspace signal/alarm would not make a live boot bounded or safe.

Only host compile/dry-run assertions are authorized and were run. Firmware
boot, hardware readiness, ENN usability, and inference remain unproven.

## Rejected unwind proposal and safe artifact

No kernel patch is retained. The apparent fix is unsound: the existing
`!hdev` branch explicitly drops `boot_cnt`, while close can drop it again;
moving `NPU_VERTEX_POWER` earlier therefore risks a double put. `POWER_NOTIFY`
also waits in `wait_event()` without a timeout, and its valid-session path can
wait indefinitely. Manual shutdown likewise collides with close ownership.
These defects require a real kernel repair and recovery design, not a host
text patch.

`npu-boot-probe.c` is now ABI-only. `--execute` explicitly refuses before any
open/ioctl; only `--dry-run` prints the exact ABI. Static assertions cover
the structure size and value offset. The host test verifies refusal and no
device access.

The extracted `evidence/native-linux-20260919/preflight/config.gz` confirms
`CONFIG_NPU_USE_BOOT_IOCTL=y` and `CONFIG_DSP_USE_VS4L=y`. It does not contain
`CONFIG_NPU_SECURE_MODE`, so that option is unset in the captured config; the
generic defconfig is not used to claim otherwise. `vectors.bin` is not a
normal BOOTUP blocker here: the source request is tied to the firmware-test
path, while ordinary BOOTUP selects `AIE.bin`; its absence only blocks any
vector firmware-test claim.
