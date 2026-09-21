# Xclipse compute compatibility experiments

Measured on Galaxy S22 **SM-S901B/DS, r0s, Exynos 2200**, 2026-09-21.

Samsung's recovered OpenCL and Vulkan drivers execute verified arithmetic on
the actual Xclipse GPU under native Linux, inside a small Bionic compatibility
root. No Android init, framework, graphics services or property server runs.
This is proprietary-driver reuse, not a native Mesa/RADV fix or a generic
installable Linux GPU driver.

Evidence: [OpenCL](../../docs/research/GPU_OPENCL_WORKING_2026-09-21.md),
[Vulkan](../../docs/research/GPU_VULKAN_WORKING_2026-09-21.md),
[curated receipts](../../evidence/gpu-compat-20260921/summary.json).
Each path passed three consecutive fresh-process numerical tests with no new
GPU faults. An earlier OpenCL test caused a timeout/reset despite correct
output; its cause and long-duration/cold-start reliability remain unknown.
Neither accelerated Hyprland nor model offload follows from these shader tests
alone. Separate [llama.cpp tests](../../docs/research/GPU_LLAMA_VULKAN_2026-09-21.md)
now verify Qwen0.8B GPU inference and CPU-matching text; the resident4B service
and desktop remain CPU/software.

## Running the already-staged control tests

These commands require the owner's existing verified private runtime,
SSH setup, working guardian boot and healthy CPU 4B service. They are not
instructions for a stock phone. A GPU ioctl can hang hardware even inside a
chroot; preserve remote access and a separately reviewed recovery path.

```sh
python3 tools/gpu-compat/run-trial.py --proc --sys opencl-headless \
  unique-opencl-trial /system/bin/opencl-probe --compute 256
python3 tools/gpu-compat/run-trial.py --proc --sys vulkan-headless \
  unique-vulkan-trial /system/bin/vulkan-hal-probe --compute256 --shader /compute.spv
```

Use a previously unused trial name. The wrapper checks staged hashes, saves
the exact privilege-drop helper, enforces a deadline and captures before/after
phone health and kernel output. Acceptance requires every expected output,
real completion, unchanged boot, healthy services and no new GPU faults—not
merely a zero exit code. It does not reboot or auto-retry a failed test.

The helper exposes only selected SGPU nodes in a process-private `/dev` tmpfs,
because persistent userdata is intentionally mounted `nodev`. Optional
`/proc`/`/sys` views are read-only; it drops to UID/GID 1000, clears capabilities
and sets NoNewPrivs. This is not a complete security boundary around a kernel
GPU driver.

## Reconstruction and boundaries

`stage-base.py` copies saved recovery Bionic libraries into a **new** private
host runtime. `recover-vendor-closure.py` reads selected files from the owner's
read-only compressed F2FS backup; `stage-vendor.py` adds the recorded GPU
closure. The headless-shim builders produce fail-fast sentinels for unused
graphics interop imports. A called sentinel aborts: none fakes successful
GPU work, allocation, synchronization or display support. Vulkan also uses
SONAME-only carriers for dependencies with no imported functions in the
selected closure. Proprietary firmware/libraries are intentionally excluded
from GitHub; source paths and manifests are specific to the project checkout.

Stage a new phone variant only after reviewing its closure. Existing variants
are not overwritten. `expose-devices.py` verifies the SGPU driver binding and
creates selected nodes only inside the test root; original device permissions
and system graphics configuration are unchanged. Never archive `stock/`.

`build-llama-vulkan.sh` and the [Vulkan bridge](VULKAN_BRIDGE.md) form the separate
Qwen0.8B integration experiment, not a production4B replacement. The OpenCL Xclipse
admission patch is **diagnostic only**: additional upstream Adreno/Intel-only
kernel selectors remain unported. Do not install it as a functional backend.

Host tests do not execute Samsung binaries or ARM binaries through QEMU:

```sh
python3 tools/gpu-compat/test-opencl-probe.py
python3 tools/gpu-compat/test-vulkan-hal-bridge.py
python3 -m py_compile tools/gpu-compat/*.py
```

The optional `review-progress.py` invokes the separately maintained Pi review
loop and is not required for GPU testing. Local-model advice is not device
execution evidence or authorization.
