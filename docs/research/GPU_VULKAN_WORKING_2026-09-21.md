# Samsung Xclipse Vulkan headless evidence (2026-09-21)

## Milestone

The recovered Samsung Android Vulkan HAL now has a bounded, repeatable
headless compute result in the isolated Bionic compatibility root. Three
independent `--compute256` trials returned zero, reported `Samsung Xclipse
920`, produced the exact checksum `99712`, and recorded no new GPU messages:

- `vulkan-compute256-first`
- `vulkan-compute256-second`
- `vulkan-compute256-third`

The public receipts are exported under
`evidence/gpu-compat-20260921/`; `summary.json` preserves the earlier failed
`vulkan-load-nodev` trial, the successful `vulkan-load-closure` trial, and the
enumeration trial. The numerical exporter requires the exact checksum, device
name, PASS marker, zero return code, same boot, and clean kernel capture; an
exit code alone is not treated as compute success.

## Exact tested bytes and device report

The staged manifest is
`rootfs/gpu-compat-20260921/manifest-vulkan-headless.json`.

- `system/bin/vulkan-hal-probe`: SHA-256
  `51e79072f2fdbad2089f0808fd4537e700dee51120dc13dcbbd8d13a0cb20d54`.
- HAL: `/vendor/lib64/hw/vulkan.samsung.so`.
- Physical device: `Samsung Xclipse 920`, type `1` (GPU).
- API: `1.3.279`; driver: `100675593`; queue family: `0`.
- Shader input `compute.spv`: SHA-256
  `08d3720f0c0a2545fc044d3e06bce254bb6cae5b60385eadb8cdf77cd1f469ca`.

The probe calls the Android HAL `HMI` entry point directly, creates a Vulkan
instance/device, submits a compute queue, waits on a fence, and reads back the
known result. The test is headless and uses no WSI, display, compositor, or
system graphics service.

## Boundary

This is direct Samsung HAL compute evidence, not a native Linux Vulkan ICD or
a completed llama.cpp bridge. No llama.cpp Vulkan model execution, Qwen
model, WSI/display path, or model-offload acceptance has been tested. The
next bridge work must retain this arithmetic probe as its control and prove
llama.cpp shader execution separately; the three checksum passes do not imply
model support.
