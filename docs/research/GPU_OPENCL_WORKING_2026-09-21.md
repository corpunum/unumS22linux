# Samsung Xclipse OpenCL working evidence (2026-09-21)

## Milestone

The isolated Samsung SGPU OpenCL path is working for verified arithmetic
compute. This proves the OpenCL compatibility-root/probe path only; it does
not prove Hyprland, NPU use, or model offload. The parent agent's private
llama.cpp Xclipse-family admission is an in-progress diagnostic build only;
this is not a generic llama.cpp port or model-offload acceptance.

The three clean arithmetic trials were independent processes on the same boot:

- `headless-compute256-after-recovery`: `--compute 256`, checksum verified.
- `headless-compute1024-clean`: `--compute 1024`, checksum verified.
- `headless-compute256-clean-third`: `--compute 256`, checksum verified.

All three returned zero, reported `Samsung Xclipse 920`, had empty
`gpu_messages`, and emitted `COMPUTE ... checksum=verified PASS`. Receipts
are under `rootfs/gpu-compat-20260921/trials/<name>/receipt.json`.
[Curated public receipts](../../evidence/gpu-compat-20260921/summary.json)
include the failed bring-up stages as well as the clean runs.

The earlier same-run `headless-compute256-first` also returned a numerical
pass, but its kernel delta recorded an SGPU job timeout and successful GPU
reset. That numerical pass is not stable evidence. No root cause is asserted
for that timeout/reset.

## Runtime boundary

Trials were launched from the host with a unique name, for example:

```sh
python3 tools/gpu-compat/run-trial.py --proc --sys opencl-headless \
  new-compute-001 /system/bin/opencl-probe --compute 256
python3 tools/gpu-compat/run-trial.py --proc --sys opencl-headless \
  new-capabilities-001 /system/bin/opencl-capabilities --capabilities
```

Use a different unused trial name each time; existing receipts are preserved.
`exec-isolated.py` creates a private mount namespace, makes mount propagation private,
uses read-only `/proc` and `/sys` views when requested, drops capabilities,
sets UID/GID 1000 and `NoNewPrivs=1`, and uses the compatibility-root linker
paths. The selected device nodes are SGPU `/dev/dri/renderD128` and
`/dev/dri/card0`. Because userdata is `nodev`, the working setup requires the
private `/dev` tmpfs with only the selected device nodes. No exynos display
nodes were added and original device permissions were not changed.

No Android graphics services or Android property area were needed. The
headless interop shims are fail-fast sentinels for nonessential calls; they do
not emulate GPU compute and were not used to claim graphics support.

## Exact bytes

The staged runtime manifest is
`rootfs/gpu-compat-20260921/manifest-opencl-headless.json`.

- AArch64 Bionic `linker64`: SHA-256
  `df0f69682d6a425e9a63c0894e87b6d1184537cc23773bff25fd3ca33e62f731`.
- `opencl-probe`: SHA-256
  `2136ab6249ca119b7c7defd0a22f7288c699d43377359118517075db16ac401d`.
- `opencl-capabilities`: SHA-256
  `954d0f660e767b8cd96c0d63da004bcfe88c19ffe59ef54b7229b5a444a893fe`.
- Trial helper (`exec-isolated.py`): SHA-256
  `21fcf21e62e5ab722dea16b19acf0aa0f127f6582c7acc0b5166889f5679b27d`.
- Vendor `libSGPUOpenCL.so`: SHA-256
  `0da7a4f54dad4102801fdae8e91a04127ca66de73b9913e28897a5a2758ceb81`.

Headless shim manifest: `rootfs/gpu-compat-20260921/shims/manifest.json`.
The host abort test passed. Exact shim hashes are:

- mapper `android.hardware.graphics.mapper@4.0-impl-sgr.so`:
  `8174c3ebf9c551973b2d378e7eb295fb7da0580ab489cef493ecb0117a5fa9ec`.
- `libnativewindow.so`: `f2c7b03eb634dcc3648b2b81e3a584282d7d2f78dc289d13cba954b6b7167b16`.
- `libsync.so`: `a0d1366b1a04e5144dd462f6efe9f480c32aef200a0486a99798ca796c570196`.

The capability probe reported global/shared memory 4 GiB, maximum allocation
1 GiB, 64-bit addressing, FP16 support (`cl_khr_fp16`), and subgroup support
(`cl_khr_subgroups`); driver version was `3.541, f0078d4a11`.

The proprietary vendor blobs are not redistributed. Recorded hashes identify
bytes from the owner's read-only backup.

## Follow-up boundary

Keep future trial names unique and invoke them through host `run-trial.py`.
Do not treat the initial reset event as explained or resolved without new
evidence. Graphics compositor/Hyprland, NPU, and model-offload acceptance
remain unproven.
