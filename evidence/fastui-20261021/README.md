# Fast touch UI spike: KMS-direct CPU renderer and Samsung Vulkan zero-copy scanout (2026-10-21)

Phase 1 spike. Tools are in [`tools/fastui/`](../../tools/fastui/README.md).

Everything below ran on the phone: Galaxy S22, kernel 5.10.260, uptime about 10–30 min, battery full, BIG cluster at 34–39 °C. Taps were **injected** into the real `sec_touchscreen` evdev node with `p2-tap-latency.py`. No physical finger was used.

## 1. How the screen is driven today

| Item | Finding |
|---|---|
| DRM devices | `card0` is `amdgpu`-API `sgpu`: the Xclipse 920 GPU, which has no display. `card1` is `exynos-drmdpu`: DECON + DPP, the display. |
| Panel | DSI-1 (connector 191) on CRTC 184. Mode `1080x2340@120hs`. A **command-mode** panel (`mode=cmd`, `bts_fps=120`). |
| Planes | 16 hardware planes (DPP #0–#15) with zpos, alpha and blend. Only plane-0 is used today. The driver also reports partial-update (`no partial region request`). |
| Scanout | Today's desktop scans out an XR24 linear framebuffer: pitch 4320, `modifier=0`, `imported=yes` (wlroots dumb buffer via PRIME). |
| Production desktop | **sway 1.12 + wlroots 0.20 with the pixman renderer**, not Hyprland. `/etc/s22-desktop` = `sway-pixman`. The Quickshell touch shell runs as a layer-shell client under s22-touchd.<br>The "Hyprland on llvmpipe, ~680 ms/frame" figure from 10-09 is stale: Phase 2 (10-10) measured it, and the profile has switched since. STATUS.md did not say so. |
| Dumb buffers | `fastui info`: `CREATE_DUMB` 1080x2340x32 gives pitch 4320 and 10,108,928 bytes. PRIME export works. |

Files: [`drm-state-before.txt`](drm-state-before.txt) (debugfs `dri/1/state`).

## 2. Baseline measured today (sway-pixman + Quickshell)

| Metric | Value |
|---|---|
| Idle | 0.12 panel fps. sway 0.0 % CPU, 46 MB. Quickshell 0.6 % CPU, 91 MB. |
| Tap on the Settings tile, first panel flip | p50 13.8 ms, max 21.6 ms (6 taps). |
| Tap on the Settings tile, **page shown** (second flip) | 94–292 ms, **p50 120 ms**. |

So the panel path is already fast. What is left is Quickshell's QML software scene graph. Phase 2 also measured Files scrolling at 17.7 fps with p95 245 ms. Files: [`baseline-tap-settings-tile.jsonl`](baseline-tap-settings-tile.jsonl), [`baseline-home-sway.png`](baseline-home-sway.png).

## 3. fastui, CPU path

`fastui sw` uses a hand-rolled damage-clipped renderer: rounded tiles, a 5x7 bitmap font, and a cached shadow buffer. Each frame copies only the damaged rectangles into one of two dumb buffers and page-flips it. The figures below are what `fastui` itself reported.

| Run | Render | Copy | Frame (start → flip-complete event) | CPU (one core) |
|---|---|---|---|---|
| Bench, damage only (2 tiles + status bar per frame, 360 frames, 3.6 MB copied per frame) | p50 1.71 ms | p50 1.65 ms | p50 8.33, mean 8.98, p95 16.4 ms → **~111 fps** | 43.5 % while flipping non-stop |
| Bench, full redraw every frame (10.1 MB per frame) | p50 3.86 ms | p50 1.68 ms | mean 11.87 ms → ~84 fps | 58 % |
| Interactive, injected taps (32 frames) | p50 1.49 ms | p50 0.87 ms | p50 11.7 ms | 0.85 % over the 19 s session |

| Other measurement | Value |
|---|---|
| **Touch event → flip-complete** (evdev timestamp to the DRM flip event, interactive) | p50 **12.5 ms**, p95 16.5 ms, max 21.4 ms |
| Touch injection → plane-0 fb changes (external, debugfs, same method as the baseline) | p50 6.3–6.9 ms, p95 ≤ 10.7 ms (31 taps).<br>One run's first tap raced the start-up and is excluded. |
| Idle | 0 panel flips, 0.4 % CPU, **RSS 9.9 MB** |
| First frame after start | 33–65 ms |

Files: [`trial-swbench2.log`](trial-swbench2.log), [`trial-swbench-full2.log`](trial-swbench-full2.log), [`trial-sw-touch2.log`](trial-sw-touch2.log), [`sw-tap-latency*.jsonl`](sw-tap-latency.jsonl), and the real scanout capture [`fastui-sw-pressed.png`](fastui-sw-pressed.png). The first `trial-swbench(-full).log` runs double-copied the previous frame's damage; the `*2.log` runs fixed that.

## 4. fastui, GPU path (Samsung Vulkan in the isolated Bionic runtime)

**It works, zero copy, in both directions.**

- **Export:** Samsung Vulkan allocates `VK_EXT_external_memory_dma_buf` memory. `vkGetMemoryFdKHR` gives a dma-buf, which goes to the native display process over `SCM_RIGHTS`. That process imports it into card1 with `PRIME_FD_TO_HANDLE` + `ADDFB2` and page-flips it.
- **Import:** card1 dumb buffers are exported as dma-bufs and imported into Vulkan (importable memory-type bits `0xeaea`). The GPU renders straight into scanout memory.

The scanout captures show the GPU-drawn grid with the touched tile highlighted: [`fastui-gpu-export-pressed.png`](fastui-gpu-export-pressed.png), [`fastui-gpu-import-pressed.png`](fastui-gpu-import-pressed.png). The GPU frame has no text; the shader has no glyphs.

| Mode | GPU submit → fence | Frame | Touch → flip (interactive, internal) | Display-process CPU |
|---|---|---|---|---|
| export (zero copy) | p50 6.01 ms | p50 8.62, mean 11.1 ms | p50 17.2, p95 22.1 ms | 7.5 % bench, 0.5 % interactive |
| import (zero copy) | p50 6.08 ms | mean 11.6 ms | p50 17.5, p95 22.2 ms | 8.0 % bench, 0.4 % interactive |
| readback (option a: 2 × 10 MB memcpy via memfd) | p50 6.26 ms | mean 19.7 ms (~50 fps) | not run | 22 % (copies, both processes) |
| export, 1-workgroup dispatch | **p50 1.02 ms**: the fixed submit + fence cost | mean 8.3 ms | – | – |

| Other measurement | Value |
|---|---|
| Touch injection → plane-0 fb change (external, debugfs) | export p50 11.6 ms, import p50 9.6 ms |
| Start-up | Vulkan device ready in 194 ms. First GPU frame on the panel 1.13–1.35 s after start, including the sandbox and the vendor driver load. |

Findings and gotchas:

- **The driver:** `Samsung Xclipse 920`, Vulkan 1.3, 141 device extensions. Present: `VK_KHR_external_memory_fd`, `VK_EXT_external_memory_dma_buf`, `VK_EXT_queue_family_foreign` and `VK_EXT_image_drm_format_modifier`, the last by `strings`. The chosen export memory type is 1 (DEVICE_LOCAL).
- **Sandbox mounts:** inside the sandbox, `vkCreateInstance` returns -1 unless the runner adds `--proc --sys`.
- **No dma-buf mmap in the sandbox:** mmap of the card1 dma-buf from the sandbox fails with EACCES, so the readback mode uses a shared memfd instead.
- **Where the GPU time goes:** about 5 of the 6 ms is the naive per-pixel shader (8 tiles × 3 rounded-rect tests over 2.5 M pixels). G3D clocks were not inspected or pinned. A real GPU UI would rasterize with damage scissors, so it would be faster than this. That is an estimate, not a measurement.
- **GPU vs CPU:** for this UI, the GPU path is **slower end to end** than the CPU path: touch → flip p50 17 ms against 12.5 ms. Its costs are the IPC hop, submit plus fence, and the 1.1 s start.

Files: `trial-gpu-*.log`, `gpu-*-tap-latency.jsonl`.

**Option (c), EGL on GBM: not tested.** The vendor image (`rootfs/vendor-pristine-20260920.img`) contains `libEGL_samsung.so`, `libGLES_mali.so`, `android.hardware.graphics.allocator@4.0-service-sgr` and `composer@2.4-service`, which is Android EGL. It has no `libgbm`. Using it would mean `AHardwareBuffer`/gralloc without the allocator service. The Vulkan dma-buf path makes that unnecessary.

## 5. Measured vs assumed

| Measured | **Not** measured, or assumed |
|---|---|
| Everything in the tables: from fastui's own clocks and from debugfs plane-0 fb changes. Captures are real scanout buffers read back with `drm-capture.py`. | **Real finger latency.** The touch controller's scan and firmware time comes before the evdev timestamp, and injected events skip it. Owner physical test pending. |
| | **Photon time.** The flip-complete event is taken as the end of the command-mode frame transfer. Panel pixel response is not measured. |
| | **CPU of the Bionic client** in GPU mode is not measured (only the display process). |
| | **Power draw.** Only SoC temperatures were sampled. |
| | **Text.** Text rendering at real quality (anti-aliased fonts) was not part of the CPU renderer. Expect render cost to grow by a small multiple for text-heavy pages. That is an estimate. |

## 6. Phone state after the spike

- sway still owns plane-0, with the same sway, seatd, s22-touchd and squeekboard PIDs as before.
- The touch shell was restarted once by exact PID (Quickshell 2336 → 19813 → 20414). touchd respawned it. The home screen shows: [`after-restore-sway.png`](after-restore-sway.png).
- The sandbox copy of `fastui-vk` was removed from `llama-vulkan/system/bin`. The binaries stay in `/root/fastui/` on the phone.
- Audio and the voice scripts were not touched. No packages were installed and nothing was flashed. There was no reboot.

**Incident:** the session switch makes sway drop DSI-1. Quickshell then logs "There are no outputs" and never re-maps its layer surfaces, so the panel stays black even though sway owns it. A DPMS cycle did not fix this; restarting the shell did. `fastui-trial.sh` now does that restart automatically, and this was verified (`trial-restore-check.log`).

## 7. Recommended architecture for the real shell

- **The shell:** one Rust process, organised as renderer + presenters.
  - **Renderer:** CPU, damage-tracked, retained widget tree, in the style of tiny-skia, with a glyph cache (fontdue or cosmic-text).
  - **Presenter 1, default:** KMS-direct, as measured here: two dumb buffers, page flip, evdev touch. It holds a seatd session, so it never fights sway.
  - **Presenter 2:** the same frames as a `wl_shm` layer-shell surface under sway-pixman. It is used while a foreign Wayland app (foot, a browser) is on screen.
  - Switching presenters is the seat switch proven here. The Quickshell re-map bug goes away once Quickshell is gone.
- **Apps:** native pages inside the shell.
  - chat: OpenUnum `:18880` SSE. Port the client from `tools/unum-shell`.
  - phone: s22d and s22-phoned.
  - files and settings: s22d.
  - switcher.
  - agent cards: keep s22-touchd's `ctl.sock`/confirm protocol, so the `s22-ui` plugin and OpenUnum tools keep working unchanged.
  - camera stills: s22-camera.
  - terminal: `alacritty_terminal` + a pty, drawn by the shell, or hand off to foot under sway.
- **GPU:** use it only where it pays. That means camera or video preview and long scrolling animations: a sandboxed Samsung Vulkan producer hands dma-bufs to the presenter, ideally on a **separate DPP plane**, so there is no compositing at all. Plane overlay was not tested. A full GPU UI is not worth it here: 17 ms against 12.5 ms touch → flip measured, a 1.1 s start, and a second sandboxed process.
- **Targets**, from this spike's numbers. Each is an estimate until the real shell is built:

  | Metric | Target |
  |---|---|
  | Tap → highlight | ≤ 16 ms |
  | Tap → new page | ≤ 25 ms, against 120 ms p50 today |
  | Idle CPU | about 0 % |
  | RSS | ≤ 30 MB |
