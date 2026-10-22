# Our own models on the NPU — 2026-10-22

Goal: run models we choose (not Samsung's stock `.nnc` files) on the Exynos 2200 NPU from native Linux.
No flashing, no module change, no partition write. Everything ran inside the isolated Bionic runtime
`/srv/s22/enn-rt` (the same chroot as the 10-21 stock-model work).

## Result

**Verified on the device:**
1. **Samsung's on-device NPU compiler works from Linux.** The vendor image ships an NNAPI *support library*,
   `neuralnetworks_npu_sl_driver_prebuilt.so`. It is the whole NNAPI runtime plus Samsung's driver in one
   `.so`, so no HAL service and no binder are needed. Our process `dlopen`s it and sees one device,
   `npu_sl` (type ACCELERATOR, feature level 5). We build an NNAPI graph and compile it. The SL then runs
   GraphGen (`libgraphgen_*`) and the NPU compiler (`libnpu_compiler`, `libsait_npu_compiler`, crane/dove).
   It **writes the result as a normal `.nnc` file** (`.//ann.nnc`, an `NPU_BINARY` section, no `DSP_BINARY`).
   That file runs through ENN with the existing `enn-run`, like the stock models.
2. **Our own int8 conv, bit-checked:** `nnapi-conv-test` builds a 3×3 conv (8→16 channels, 32×32, fused
   ReLU). The weights and input are deterministic. A C integer reference is computed on the CPU.
   - Compile time: 168 ms (63 ms warm).
   - NPU: **0.32 ms p50**.
   - Output: 16 384 values. QUANT8_ASYMM: 16 027 exact, 357 off by 1, **0 worse**. QUANT8_ASYMM_SIGNED:
     16 026 exact, 358 off by 1. The ±1 is requantisation rounding.
   - The output checksum is the same in every rerun (`9b995581645257e9`).
3. **A trained keyword spotter runs on the NPU.** We took ARM ML-zoo models (public, Apache-2.0) and compiled
   them on the phone with the official TFLite `benchmark_model` (Android nightly) using
   `--nnapi_support_library_path=<the vendor SL>`:
   - **KWS MicroNet-L** (whole graph, 18 ops, one partition). 20 random inputs, 240 logits vs the TFLite
     CPU reference kernels: 159 exact, 81 off by 1, none worse. Same top-1 in every case. **NPU 0.58 ms p50**.
   - **KWS DS-CNN-L** (the conv/depthwise body, ops 1–12, on the NPU; FC + softmax, 276×12, on the CPU).
     5 520 values: 4 942 exact, 578 off by 1, none worse. **NPU 0.34 ms p50**.
     End to end vs the full TFLite model: top-1 agrees 20/20.
   - **Real words:** Kokoro TTS said yes/no/up/down/left/right/on/off/stop/go in 6 voices, 60 clips.
     We computed MFCC features on the rig (`kws-features.py`, the TF speech-commands front end in numpy).
     **CPU reference 47/60 correct, NPU 47/60 correct, NPU and CPU agree on 60/60.**
     The misses are the model's, mostly "up" → `_unknown_`.
     [kws-dscnn-tts-npu-vs-cpu.txt](kws-dscnn-tts-npu-vs-cpu.txt)
4. **No crash.** No compiled `.nnc` had a `DSP_BINARY` section. GraphGen's config says `[X, NPU] to [X, DSP] not allowed`
   for every layer type. The bad cases (see 6) were 4.3 s NPU timeouts that returned zeros. The NPU
   recovered every time: the conv checksum was the same afterwards. There was no watchdog reset.

**Found but not usable yet:**

5. **Samsung's SL only compiles whitelisted multi-op graphs.** `GraphGenCore::isSupportedAIBANNModel` checks the graph
   ("AI Benchmark ANN model"). `ann_s::AnnModelSupported` keeps a multimap from op count to architecture
   checker: ResNetV5, InceptionV3, MobileNetV2/V3, VGG, DPED, PyNET, CRNN, LSTM, SRCNN. Any other multi-op
   graph gets "This model is not surpported!" (sic), and the TFLite delegate falls back to the CPU.
   A second gate, `NpuSkip::isNeedSkip`, refuses graphs whose first layer is Reshape, Softmax, Pooling,
   Eltwise, Concat, ReLU and similar. Single-op graphs pass, which is why our conv worked unmodified.
   **Bypass:** `patch-graphgen-gates.py` patches 3 function entries (`checkModel` → true,
   `isSupported` → true, `isNeedSkip` → false). The patch goes into *private copies* of
   `libgraphgen_ann_import_s.so` and `libgraphgen_intermediate_process.so`, staged in `/data/nn/patched`
   (`LD_LIBRARY_PATH`, vendor files unchanged). Every KWS result above used these patched libs.
6. **NPU kernel-size limit.** Kernel-size sweep for a 1×148 input, 100→64 channels:
   - K = 7: correct;
   - K = 15: correct (0.75 ms);
   - **K = 17: silently wrong** (max diff 255);
   - K = 32: compile fails.

   The wav2letter ASR models (tiny 3.9 MB, full 23.8 MB) use 1-D kernels 48 and 32 wide.
   - TFLite's NNAPI delegate does not delegate int8 LEAKY_RELU. `tflite-leaky2prelu.py` rewrites it as
     PRELU, and then both whole graphs compile into one `.nnc` (4.2 MB / 24 MB).
   - **But execution takes 4.3 s and returns all zeros.** So the ASR encoder is **not** on the NPU yet.
   - Likely fix (untested): split the wide kernels by space-to-depth blocking. This is exact integer math,
     with K′ ≤ 15.
   - **Lesson:** every compiled model must be checked against the CPU reference. The compiler can produce
     wrong code without any error.
7. **The SL's own execute path crashes.** The NPU executes (`ENN_FRAMEWORK: ExecuteModel`, "Execution Done"),
   then a NULL dereference in the output copy-back kills the process. So we only use the SL to *compile*,
   and we run the dumped `.nnc` with `enn-run`. Not debugged further.
8. `DS-CNN-L` as a whole graph crashes the compiler (SIGSEGV in the IR builder at FULLY_CONNECTED after
   AVERAGE_POOL). That is why the model is cut (`tflite-cut.py`): the NPU runs the body, the CPU runs the 12-way FC + softmax.

## Latency: NPU vs CPU (same Bionic runtime, TFLite nightly XNNPACK, schedutil, display on)

Median per inference in ms. The NPU numbers come from `enn-run` (3000 runs) and include the ENN call
overhead. The NPU floor is about 0.3 ms per call: the single conv is already 0.32 ms.

| Model | NPU | X2 (cpu7) 1 thread | A710 (cpu4) 1 thread | 3× A710 | 4 threads (cpu4-7) |
|---|---|---|---|---|---|
| DS-CNN-L KWS (body on NPU) | **0.34** | 0.39 | 0.65 | 0.24 | 0.28 |
| MicroNet-L KWS | **0.58** | 0.90 | 1.43 | 0.53 | 0.69 |
| tiny_wav2letter (ASR) | not working (see 6) | 3.68 | 5.44 | 2.53 | 2.97 |
| wav2letter (ASR) | not working | 30.1 | — | 16.9 | 14.2 |

So for these tiny KWS nets, the NPU is about as fast as the CPU: 1.1–1.5× faster than one X2 core, a little
slower than 3 A710 cores. **The win is offload.** A wake-word check every 20 ms costs about 0.3–0.6 ms
of NPU time and almost no CPU, so the CPU stays free (and can idle) for the voice loop.
The NPU should only clearly beat the CPU on bigger models (an ASR encoder), once the kernel-size limit is solved.
(With the screen off, `s22-autosleep` sets the CPU governor to `powersave`. For these runs it was temporarily
set to schedutil, then left on schedutil because the display had come back on, which matches autosleep's unlocked policy.)

## How to use it (root on the phone)

```sh
cp /srv/s22/npu-trial/AIE.bin /proc/1/root/vendor/firmware/AIE.bin     # once per boot (as before)
cd /srv/s22/npu-custom
# compile a TFLite model on the phone (patched gates by default; NN_PATCHED=0 = stock libs):
./tflite-npu-compile /data/models/custom/model.tflite /srv/s22/enn-rt/data/models/custom/model.nnc
# run it (inputs = raw tensor bytes in NHWC order, like the TFLite tensor):
ENN_OUT=/data/local/tmp/out ENN_BIN=/bin/enn-run /srv/s22/npu-trial/enn-chroot-run \
    /data/models/custom/model.nnc 100 /data/local/tmp/in.bin
# raw NNAPI test (own graph, prints compare vs CPU):  ./nnapi-chroot-run /bin/nnapi-conv-test npu 100
```
- The compiled `.nnc` keeps the NNAPI/TFLite **NHWC byte layout**, even though ENN reports the dims as NCHW.
  Sometimes the graph tail is a CPU sub-network and the `.nnc` gets an extra I/O pair. MicroNet has
  in0 = 12 B (feed zeros) and in1 = features, and out1 is the result.
- Every compile is deterministic: the same `.nnc` md5 each time.
- The Samsung compile runs on the phone in 0.1–0.6 s (24 s for the 24 MB wav2letter).

## What was staged (all private, none of it in git)
- `/srv/s22/enn-rt/vendor/lib64`: the SL plus its closure from `vendor-pristine-20260920.img`. That is 23 new
  vendor libs: the SL, GraphGen/export ×11, the NPU compiler stack ×6 (npu, sait, crane, dove, ir, common),
  NNAPI HIDL 1.0–1.3 ×4 and `libenn_profiler`. Plus our `libnativewindow.so` stub (`nativewindow-stub.c`: the SL links
  `AHardwareBuffer_*` and never uses it).
- `/srv/s22/enn-rt/system/lib64`: libcrypto, libprocessgroup, libcgrouprc, ld-android (from the stock FYI3
  recovery ramdisk) and `android.hidl.safe_union@1.0`.
- `/data/nn/lib`: stub `libEGL.so` / `libGLESv2.so` for `benchmark_model` (`stub-*.c`).
  `/data/nn/patched`: the two patched GraphGen libs.
- `/dev/ashmem<boot_id>` → `ashmem` symlink. Bionic libcutils opens the node with the boot-id suffix,
  which Android's ueventd normally creates. `nnapi-chroot-run` makes it on each call.
- Android liblog goes to stderr through `__android_log_set_logger` (`NN_LOG=<prio>`). The chroot has no logd.
- Rig copy of every binary, patched lib, model and `.nnc`: `~/s22-private-backups/npu-custom-20261022/`.
- Reproduce the closure: `stage-nnapi-sl.sh VENDOR_MNT RECOVERY_SYSTEM_LIB64 OUT`.

## Files
- [conv-sl-run.log](conv-sl-run.log): first SL compile + NPU execute + the copy-back segfault.
- [compile-summary.log](compile-summary.log): the delegate partitioning and NNC sizes per model.
- [kws-dscnn-tts-npu-vs-cpu.txt](kws-dscnn-tts-npu-vs-cpu.txt): 60 TTS words, the NPU and CPU predictions.
- Tools: `tools/hardware/npu/nnapi/`. These are `nnapi-conv-test.c`, `nnapi-chroot-run`, `tflite-npu-compile`,
  `patch-graphgen-gates.py`, `tflite-cut.py`, `tflite-leaky2prelu.py`, `tflite-ref.py`, `kws-features.py`,
  `stage-nnapi-sl.sh` and the stubs.

## Not verified / next
- **ASR encoder on the NPU:** split the 48- and 32-wide 1-D kernels with exact space-to-depth blocking, then
  verify against the CPU. Or train or choose an encoder with kernels ≤ 15.
- **A wake-word service:** a resident ENN process that keeps the model open (`open_model` is about 30 ms)
  and is fed MFCC frames from the mic. The DS-CNN-L accuracy on real (non-TTS) speech is unmeasured.
- **The SL execute segfault.** If fixed, `benchmark_model --use_nnapi` and any NNAPI app would run
  directly, without the `.nnc` detour.
- `libeden_ud_npu.so` (the older EDEN "user-defined" path) and the HIDL NNAPI service were not needed and were not tested.
- **Power:** NPU energy per inference vs CPU was not measured.
