# fastui: fast touch UI spike (Phase 1)

A time-boxed spike on 2026-10-21. The question: can the S22 draw a touch UI much faster than today's desktop, and can Samsung's own GPU driver (vendor Vulkan in the isolated Bionic runtime) draw frames that the Linux display shows?

The results are in [`evidence/fastui-20261021/`](../../evidence/fastui-20261021/README.md).

| File | Runs where | What it is |
|---|---|---|
| `src/` → `fastui` | native root, as root, during a trial | Static aarch64 musl Rust binary, about 413 KB, with `libc` as its only dependency.<br>**Display:** owns `card1` (`exynos-drmdpu`) through raw legacy KMS ioctls: dumb buffers, PRIME import/export, `SETCRTC`, page flip with events.<br>**Touch:** reads `sec_touchscreen` (`event7`) without grabbing it, with timestamps on `CLOCK_MONOTONIC`.<br>**Screen:** a full-screen 2×4 tile grid. The touched tile lights up.<br>**Output:** prints JSON stats for frame time, touch-to-flip time and CPU use. |
| `vk/fastui-vk.c` + `vk/grid.comp` → `fastui-vk` | isolated Bionic sandbox (`s22-gpu-exec-isolated … --proc --sys --bridge-v4`) | **Renderer:** draws each frame with Samsung's Vulkan driver (Xclipse 920), using a compute shader that writes a linear XRGB8888 buffer.<br>**Link to `fastui`:** a unix socket carrying dma-buf fds over `SCM_RIGHTS`.<br>**Sandbox:** runs as uid 1000 with no capabilities. |
| `fastui-trial.sh` | native root | Bounded, self-restoring hand-over of the panel from sway (or Hyprland) to the test, through a seatd session switch. See below. |

## Modes

```sh
fastui info                                   # CRTC/connector/mode, dumb pitch, PRIME export check
fastui sw  --bench N [--full]                 # CPU renderer, N back-to-back frames (damage or full redraw)
fastui sw  --seconds S                        # CPU renderer, interactive (touch highlights tiles)
fastui gpu --gpu-mode export|import|readback [--bench N | --seconds S]
#   export   Vulkan allocates dma-buf memory -> fastui imports it into card1 (zero copy)
#   import   fastui's dumb buffers -> dma-buf -> Vulkan renders straight into scanout memory (zero copy)
#   readback Vulkan renders to host-visible memory -> memcpy to a shared memfd -> memcpy to a dumb buffer
fastui dump > frame.ppm                       # host-side offscreen render of the scene
```

Stop early with `touch /run/fastui-stop`.

## Build (rig)

```sh
export PATH=$HOME/.cargo/bin:$PATH
cd tools/fastui
flock ~/.cache/rig-heavy.lock env CARGO_TARGET_AARCH64_UNKNOWN_LINUX_MUSL_LINKER=rust-lld \
  cargo build --offline --release --target aarch64-unknown-linux-musl
cd vk
glslc -O --target-env=vulkan1.1 grid.comp -o grid.spv && xxd -i -n grid_spv grid.spv > grid.spv.h
NDK=../../../rootfs/gpu-compat-20260921/toolchain/android-ndk-r27c/toolchains/llvm/prebuilt/linux-x86_64
$NDK/bin/aarch64-linux-android31-clang -O2 -Wall -o fastui-vk fastui-vk.c -ldl
```

## Run (phone, native root)

```sh
scp target/aarch64-unknown-linux-musl/release/fastui fastui-trial.sh root@PHONE:/root/fastui/
# CPU path
setsid /root/fastui/fastui-trial.sh 60 /root/fastui/fastui sw --seconds 40 </dev/null >/root/fastui/sw.log 2>&1 &
# GPU path: the client must sit under the sandbox root's /system/bin. Remove it afterwards.
scp vk/fastui-vk root@PHONE:/srv/s22/gpu-compat-20260921/llama-vulkan/system/bin/fastui-vk
FASTUI_GPU_CLIENT="/usr/bin/python3 /srv/s22/hardware/bin/s22-gpu-exec-isolated /srv/s22/gpu-compat-20260921/llama-vulkan --proc --sys --bridge-v4 /system/bin/fastui-vk /tmp/fastui.sock" \
  setsid /root/fastui/fastui-trial.sh 60 /root/fastui/fastui gpu --gpu-mode export --bench 240 </dev/null >/root/fastui/gpu.log 2>&1 &
```

Without `--proc --sys`, `vkCreateInstance` returns -1 inside the sandbox.

## How the hand-over works

`fastui-trial.sh` does not stop sway, seatd, s22-touchd or anything else.

1. `p2-seat-hold.py` (from Phase 2) registers as seat session 2.
2. `wtype -k XF86Switch_VT_2` makes sway switch to session 2. seatd then drops sway's DRM master and revokes its input fds.
3. The test opens `card1` directly.
4. On exit, the deadline, or `/run/fastui-stop`, the script stops the test by exact PID and releases the holder. seatd re-activates sway.
5. The script then restarts the touch shell. It sends SIGTERM to the exact Quickshell PID whose parent is s22-touchd, and touchd respawns it.

Step 5 is needed because sway drops DSI-1 during the switch. Quickshell then falls back to a placeholder screen and never re-maps its layer surfaces, which leaves the panel black. This was seen and fixed during the spike.
