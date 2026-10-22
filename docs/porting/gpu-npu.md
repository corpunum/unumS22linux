# GPU and NPU

Part of the [porting guide](../PORTING.md). Both the Xclipse 920 GPU and the NPU run with
**Samsung's own userspace drivers**, inside small isolated Bionic roots on native Linux. No Android
framework, graphics services or property service run
([tools/gpu-compat/README.md](../../tools/gpu-compat/README.md)).

## GPU: the open driver didn't work, the vendor one did

The Xclipse 920 is AMD RDNA2-based ([DEVICE.md](../../DEVICE.md)), so the first attempt was Mesa's
open RADV driver with community patches. Transfers and fences worked; arithmetic shaders still gave
wrong results ([GPU_SUBMISSION_2026-09-21.md](../GPU_SUBMISSION_2026-09-21.md),
[GPU_SHADER_DIAGNOSTICS_2026-09-21.md](../GPU_SHADER_DIAGNOSTICS_2026-09-21.md)). Samsung's driver
from the phone's own vendor partition ran the same 256-word shader correctly
([GPU_VULKAN_WORKING_2026-09-21.md](../research/GPU_VULKAN_WORKING_2026-09-21.md)).

How the vendor driver runs ([tools/gpu-compat/README.md](../../tools/gpu-compat/README.md)):

1. **Build a private root:** the recovery's Bionic linker and libraries, plus the exact closure of
   vendor libraries the Vulkan HAL (`/vendor/lib64/hw/vulkan.samsung.so`) or OpenCL needs, read from
   the read-only vendor backup. Libraries it links to but never really calls (display interop) get
   fail-fast stubs that abort if called.
2. **Expose only the GPU device nodes** in a process-private `/dev` (the userdata mount is `nodev`).
   `/proc` and `/sys` only read-only and only when needed.
3. **Drop privileges:** UID/GID 1000, no capabilities, `NoNewPrivs`. (The README is clear that this is
   not a full security boundary around a kernel GPU driver.)
4. **Talk to the HAL directly.** Our probe calls the Android HAL entry point (`HMI`) itself. For
   llama.cpp, a tiny `libvulkan.so` bridge forwards to the HAL, with no loader, no layers and no
   fake success ([VULKAN_BRIDGE.md](../../tools/gpu-compat/VULKAN_BRIDGE.md)).

Results: Vulkan and OpenCL compute each passed three fresh-process runs with exact checksums
([GPU_VULKAN_WORKING_2026-09-21.md](../research/GPU_VULKAN_WORKING_2026-09-21.md)); a 0.8B model
produced CPU-identical text with all layers on the GPU
([HISTORY.md](../HISTORY.md)); a 2B model is the phone's GPU fallback model
([STATUS.md](../../STATUS.md#resident-agent-phone-stack-gpu-fallback--2026-10-08-lead2)). The same
driver can render into a dma-buf that the display driver scans out with zero copy
([EXPERIMENTS.md](../../EXPERIMENTS.md), "Fast touch UI spike"). The desktop itself still draws on
the CPU.

Caveats we measured: prompt processing at the default batch size was about 10× slower than CPU,
and the GPU was thermally capped during long runs
([evidence/gpu-t0-t1-20261008](../../evidence/gpu-t0-t1-20261008/README.md)). One early OpenCL run
caused a GPU timeout/reset despite correct output ([HISTORY.md](../HISTORY.md)). A GPU ioctl can hang
the hardware even inside a chroot, so keep remote access and a recovery path
([tools/gpu-compat/README.md](../../tools/gpu-compat/README.md#running-the-already-staged-control-tests)).

## NPU: stock module, stock firmware, stock runtime

What we needed, all from the phone itself ([evidence/npu-20261007](../../evidence/npu-20261007/README.md)):

- the stock kernel module (`npu`), already loaded at boot;
- the NPU firmware `AIE.bin` from the vendor image, copied at runtime into PID 1's
  `/vendor/firmware` (the name comes from a kernel config option);
- one `BOOTUP` ioctl on `/dev/vertex10` (our tiny [`npu-bootup-once.c`](../../tools/hardware/npu/npu-bootup-once.c));
  the kernel then logged that the security unit verified the firmware;
- Samsung's ENN user library in its own Bionic root (`/srv/s22/enn-rt`), called through its public
  API by our [`enn-run-once.c`](../../tools/hardware/npu/enn-run-once.c), built with NDK r27c.

A stock object-detection model ran 3000 times at 3.1 ms, with byte-identical output across days.
Proof it was the NPU and not a CPU fallback: the process held `/dev/vertex10`, and the NPU's clock
rose from 267 to 1066 MHz ([evidence/npu-20261007](../../evidence/npu-20261007/README.md),
[evidence/npu-20261021](../../evidence/npu-20261021/README.md)). 16 of 17 stock models run.

What failed or is dangerous:

- **Swapping the NPU module at runtime does not work:** the stock module's remove path leaves an
  IOMMU mapping behind, so no module can probe again until reboot
  ([evidence/npu-20261007](../../evidence/npu-20261007/README.md)). The working result above uses
  the stock module unchanged, after one reboot.
- **A model with a DSP section rebooted the phone** (page fault, then watchdog). The sweep tool now
  skips such models ([evidence/npu-20261021](../../evidence/npu-20261021/README.md)).

### Our own models on the NPU

The vendor partition also ships an NNAPI support library (`neuralnetworks_npu_sl_driver_prebuilt.so`)
that contains Samsung's NPU compiler. Loaded with `dlopen` (no HAL service, no binder), it compiles
NNAPI or TFLite graphs into ordinary `.nnc` files that ENN runs
([evidence/npu-custom-20261022](../../evidence/npu-custom-20261022/README.md)).

- Multi-op graphs are only accepted if they look like known benchmark models; we patch three
  function entries in **private copies** of two compiler libraries to lift that check. Vendor files
  stay unchanged.
- In a kernel-size sweep, 15 was correct, 17 gave **silently wrong** results (16 untested) and 32
  failed to compile. Always compare
  NPU output with a CPU reference; the compiler can be wrong without an error.
- The library's own execute path crashes, so it is used only to compile.

## For your phone

- Look in `/vendor/lib64/hw` for the GPU HAL (`vulkan.*.so`) and in `/vendor/lib64` for OpenCL and
  any NPU runtime (on Samsung: `libenn*`, firmware in `/vendor/firmware`). `readelf -d` gives the
  NEEDED closure you have to copy.
- Start with a probe that does one known computation and checks the exact answer, before any model.
- If your GPU has a working open driver (for example Adreno with Mesa Turnip), prefer it; a working
  desktop GPU is still not proof of correct LLM inference ([OTHER_PHONES.md](../OTHER_PHONES.md)).
