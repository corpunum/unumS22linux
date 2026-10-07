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

## Option (a) after one deliberate recovery reboot: **the NPU executes a vendor model**
Decision: the owner said "Yes you do the best approach go"; the coordinator chose option (a).
No partition writes and no flashing.
1. `s22-reboot recovery` brought the phone back in about 1 min, with the stock `npu`
   module (1 736 704 bytes) loaded and the desktop, sound cards and button daemon up.
   The modem came back in `INIT`, as expected.
2. `AIE.bin` was re-staged at runtime in PID 1's `/vendor/firmware`.
3. **BOOTUP on the stock module:** `npu-bootup-once` (open → `VS4L_VERTEXIOC_BOOTUP`
   {ctrl 0, value 2} → close) returned **rc 0 in 0.05 s**, under a 60 s KILL timeout.
   The kernel logged `exynos_s2mpu_verify_subsystem_fw: DNC FW[0] is verified!` and
   `Allow DNC FW Stage 2 access permission`. It did not hang and nothing went to D state.
4. **ENN inference:** `tools/hardware/npu/enn-run-once.c`, built with Android NDK r27c
   for bionic, calls the public ENN API through dlsym:
   Initialize → OpenModel → AllocateAllBuffers → Execute×N → Release → Close → Deinit.
   It ran in `/srv/s22/enn-rt`, a copy of the 2026-09-21 ENN closure plus
   `vendor/etc/enn/{custom_mode_config.json,enn_mcd_kernel_64.bin}`, with /dev, /proc and /sys bound in.
   Model: stock `OD_V3.5.2_08_31_VGA_PAMIR_ENN_BGRA.nnc` (object detection, PAMIR = E2200),
   with deterministic input (every byte 0x80, a 640×480 BGRA buffer of 1 228 800 bytes) and
   outputs of 307 200 and 153 600 bytes.
   - Every stage returned rc 0. **3000 executions** gave mean **3.11 ms**, p50 3.24 ms,
     p99 3.44 ms, max 4.20 ms (about 320 inferences/s).
   - Output checksums were identical across runs (`492a7bbd…`, `b16c3392…`) and non-zero.
   - Proof that it ran on the NPU and not a CPU fallback: while running, the process held
     `/dev/vertex10` (×2) and dma-heap buffers. NPU devfreq rose from 267 MHz to **1066 MHz**
     (DNC from 267 to 935 MHz). The process sat in D state on the device at about 2 % CPU.
   - No kernel warnings, no panic. Wi-Fi stayed up.
5. Afterwards the modem was brought back up manually with `s22-modem-up up`, which is the
   first verified bring-up right after a fresh boot (ONLINE in 8 s). `s22-modem --serve-rfs`
   served the CP's NV write into the private copy. The phone's OpenUnum was restarted manually.

## TTS on the NPU
Still not possible. ENN only runs `.nnc` graphs, and converting a TTS model needs Samsung's
proprietary compiler (Exynos AI Studio / ENN SDK), which is not available here. The NPU path
itself is now proven, so any `.nnc` produced by that toolchain can run through `enn-run-once`.
The voice stays on CPU Supertonic.
