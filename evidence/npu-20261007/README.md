# NPU attempt — 2026-10-07 (owner-approved single sequence, stopped at first failure)

Sequence approved by the owner: stage AIE.bin at runtime → swap in the patched npu.ko →
BOOTUP once → one vendor .nnc via ENN. It stopped at step 2.

1. **Firmware staged (runtime only, not persistent):** stock vendor `AIE.bin`
   (2 219 776 bytes; `FW_BASE_NAME` is "AIE" because `CONFIG_DSP_USE_VS4L`) copied to
   `/proc/1/root/vendor/firmware/AIE.bin` (PID 1's ramdisk; it disappears on reboot).
2. **Module swap: FAILED.** `rmmod npu` (stock, refcount 0) completed
   (`npu_device_remove`/`npu_device_exit` success). `insmod` of the patched NPU12 module
   (`builds/npu-native-twelve-module-only-out-20261003`, vermagic matches, debug-stripped
   sha256 `cfae2217…`) loaded, but its probe failed:
   `sysmmu: failed to map 0x100000 @ 0x31000000, ret:-98` (EADDRINUSE), followed by
   `npu_init_iomem_area: fwmbox heap … -12` and `probe of npu_exynos failed with error -22`,
   plus a WARNING stack trace from the iommu map.
   **Root cause:** the stock module's remove path does not unmap its fixed-address
   IOMMU region (fwmbox heap at IOVA 0x31000000). No module can probe again in this
   boot, so a runtime swap cannot work.
3. BOOTUP was not attempted, because no NPU device is bound. The executor
   `tools/hardware/npu/npu-bootup-once.c` (open → `VS4L_VERTEXIOC_BOOTUP` {ctrl 0, value 2}
   → close) is built and staged at `/srv/s22/npu-trial/`, but has not been run.
4. ENN/.nnc was not attempted.

Phone after the failure: no panic, Wi-Fi up, modem ONLINE. The `npu` module stays
loaded with no device. The NPU is unusable until the next reboot, which restores the
stock module (the patched module was only insmod-ed at runtime).

## Options (owner/coordinator decision)
- After a reboot, try BOOTUP on the **stock** module (no swap). Its unwind bugs are
  known from review and unpatched.
- Or load the patched module **at boot instead of the stock one**. That means changing
  the module set in the boot image, which is an image rebuild and flash.
- TTS on NPU in any case needs a TTS model compiled to Samsung's `.nnc`. The
  compiler is proprietary and not available, so CPU Supertonic stays the voice path.
