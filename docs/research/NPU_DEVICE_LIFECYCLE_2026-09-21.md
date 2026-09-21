# Samsung Exynos NPU device lifecycle — 2026-09-21

## Evidence boundary

The pinned source is kernel commit
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`, with a clean working tree.
The parent-supervised open-only receipt is private at
`rootfs/hardware-reuse-20260921/npu-open-once/`: `/dev/vertex10` opened and
was `fstat`-verified as character rdev `82:10`, closed successfully, left no
NPU vertex fds, and preserved the boot/guardian health state. This proves only
the open/close lifecycle, not firmware boot or inference.

The host-only closure is staged at
`rootfs/npu-firmware-closure-20260921/`. It contains recovered exact-basename
copies of `AIE.bin`, `dsp_reloc_rules.bin`, `liblog.elf`, and `libivp.elf`,
bound to their inode-suffixed recovery names and hashes in `manifest.json`.
It was not copied to the phone.

## Compiled path and firmware requirements

The defconfig enables `CONFIG_NPU_USE_BOOT_IOCTL=y`,
`CONFIG_NPU_USE_HW_DEVICE=y`, and `CONFIG_DSP_USE_VS4L=y`.
`npu_vertex_fops` routes open/release to `npu_vertex_open`/
`npu_vertex_close` (`drivers/vision/npu/core/npu-vertex.c:492-501`). The
separate `VS4L_VERTEXIOC_BOOTUP` ioctl is guarded by the same boot-ioctl
configuration (`drivers/vision/vision-core/vision-ioctl.c:825-835`). Its ABI
is `_IOWR('V', 13, struct vs4l_ctrl)`; with this config the structure contains
`ctrl`, `value`, `mem_size`, `mem_addr`, `mem_addr_h`, and `reserved`
(`vision-core/include/vs4l.h:89-98, 228-230`). The implementation accepts
only `value == NPU_HWDEV_ID_NPU` (`0x2`) or `value == NPU_HWDEV_ID_DSP`
(`0x4`), stores it as session `hids`, and dispatches that exact value
(`npu-vertex.c:1360-1394`). It is not a combined `0x6` selector.

For this configuration, `npu_device_bootup` calls
`npu_system_resume`, `dsp_dhcp_init`, `proto_drv_open`, and late-open
(`drivers/vision/npu/core/npu-device.c:727-762`). On a cold boot,
`npu_system_resume` calls `npu_firmware_load`; the compiled source selects
`AIE.bin` because `CONFIG_DSP_USE_VS4L` defines `FW_BASE_NAME "AIE"`, and the
request searches the kernel firmware namespace for that name
(`npu-binary.h:23-37`, `npu-system.c:1642-1710`, `npu-binary.c:113-169`).
Thus AIE is a main-image input for a cold BOOTUP path, not for the accepted
open-only trial.

`vectors.bin` is not called by `npu_system_resume` or ordinary
`npu_device_bootup`. The only pinned-source caller found is the firmware-test
vector loader (`npu-fw-test-handler.c:78-125`), which calls
`npu_fw_vector_load`; that helper requests exactly `vectors.bin`
(`npu-binary.c:196-232`). Therefore vectors is required for that firmware-test
mode, but its necessity for every normal ENN/NPU mode is not established by
this source audit.

The main AIE load occurs in common `npu_system_resume` before hids dispatch.
`npu_hwdev_bootup` then iterates only devices whose ID bit matches the
selected hids (`npu-hw-device.c:382-414`; IDs are in `npu-hw-device.h:20-28`).
For pure NPU hids `0x2`, the NPU init callback is empty
(`npu-hw-device.c:181-194`), so the DSP callback is not reached. Thus the
missing DSP files are not a universal blocker for a source-level NPU-only
BOOTUP route; AIE is the main-image prerequisite for that route. “AIE alone
suffices” remains unproven at runtime because firmware signing/imgloader,
service/device setup, mailbox behavior, and exact userspace ABI are not
validated here.

DSP hids `0x4` is a distinct path. With VS4L enabled,
`npu_hwdev_dsp_init(on)` calls `dsp_system_load_binary` and
`dsp_kernel_manager_open` (`npu-hw-device.c:211-233`). The manager directly
requests `dsp_gkt.xml`, `dsp_reloc_rules.bin`, `liblog.elf`, and `libivp.elf`
(`dsp-kernel.c:495-556`); the DTS also names `dsp_ivp_pm.bin` and
`dsp_ivp_dm.bin` as memory-area firmware inputs (`s5e9925.dts:15959-15960`).
Only the reloc file and two common ELF files were recovered for this closure;
the other names remain missing. Paired shutdown uses the same hids and
decrements the corresponding boot/init references (`npu-hw-device.c:416-445`).

The ENN init trace requested `/vendor/etc/enn/custom_mode_config.json`, but
the recovered vendor index (`evidence/npu-audit-20260920/vendor-assets-index.txt`)
and local asset trees contain no such file. It is not fabricated or staged.
This is an ENN userspace configuration dependency, distinct from the kernel's
AIE/DSP firmware names.

## Remaining gate and cleanup

The next BOOTUP experiment would require source/recovery review followed by a
supervised device action using the exact NPU-only `value=0x2` ABI, exact
Android firmware paths, AIE provenance, matching ENN/kernel ABI, and
before/after health plus kernel/strace receipts. It must not be inferred from
open success. Any failed BOOTUP path
must use the driver's paired shutdown/close lifecycle: the source's error path
calls protocol close/system suspend/system close, while userspace must always
close the vertex fd; no signal-handler cleanup or retry is authorized.
