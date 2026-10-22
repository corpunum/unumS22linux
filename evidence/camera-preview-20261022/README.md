# Live camera preview spike — 2026-10-22

Goal: find how fast the S22 cameras can stream to a live viewfinder, and pick the architecture for the
shell's Camera page. All numbers were measured on the phone. Raw numbers: [results.json](results.json).

**Conditions.** The phone was out with the owner, screen off. s22-autosleep had set the `powersave` cpufreq
governor (cores at about 1.0–1.15 GHz), and about 40 % of the CPU was already busy (llama-server and others).
These are worst-case clocks; with the screen on (schedutil) the CPU numbers should drop.
No frame was saved or copied off the phone, and no picture is in this directory.

## 1. Why capture is below 1 fps today

The sensor is not the bottleneck. One continuous stream of the existing client (`profile-stream.py`, which
imports `s22-camera.py` and times every call) shows:

| Step | Rear GN3 | Front IMX374 |
|---|---|---|
| open → blocking `IS_S_STREAM` on | 235 ms | 138 ms |
| open → first frame | 256 ms | 216 ms |
| full teardown | 107 ms | 70 ms |
| steady rate, 2 leader buffers (today) | — | **20–21 fps** (frames dropped) |
| steady rate, 4 leader + 4 VC0 buffers | **30.0 fps** | **30.0 fps** |

- **Per-shot setup costs only about 0.3 s** (start plus stop). The <1 fps comes from what wraps it:
  - `record-safe` restarts the camera every 7 frames;
  - the old develop and the MJPEG encoder are pure Python;
  - touchd meters, then takes a burst, then develops.
- **Two leader (shot) buffers starve the sensor group.** Each frame needs a queued shot. With 2 shot buffers
  about 30 % of frames are lost. With 4 they are not.
- **The Python 6 MB copy costs 15–21 ms per frame**, and 89 % of a core at 30 fps (rear). It cannot develop at all.
- **Sensor modes.** The locked modes are already binned. The rear 2040x1532 is 1/16 of the 50 MP GN3 (4x4 Tetra²).
  The front 1824x1368 is the 2x2 binned IMX374. So "full resolution" in our path is 3.1 MP / 2.5 MP. The DT mode
  tables (`r0s_eur_openx_w01_r27.dts`) also list:
  - GN3: 2040x1532@120 (mode18), 2040x1148@30/240 (mode24/19), 4080x3060@60, 8160x6120@27;
  - IMX374: 3648x2736@30, 1988x1120@30/120, 912x684@120.

  None is needed for a 30 fps preview. Changing the mode is a write-class change, left for review.
- **Readout.** The rear frame arrives 8.5 ms after its CSI frame start. The binned GN3 reads 1532 lines in about 7 ms
  inside a 33 ms frame. The front frame arrives about 34 ms after frame start: the IMX374 uses the whole frame for readout.

## 2. Continuous streaming

Long streams were run with the reviewed ioctl sequence and teardown, without a restart:
- 60 s with the Python client;
- **300 s (8993 frames) with s22-campreview**: 29.97 fps, clean teardown in 70 ms, no kernel fault.
  - 0.6 % of frames carried the V4L2 ERROR flag and were skipped.
  - The board warmed by 5 °C.

The earlier "long-stream wedge" (2026-10-13) did not reproduce. That run also wrote files and ran GStreamer during the stream.

## 3. s22-campreview (the proof)

`tools/hardware/camera/s22-campreview` is a static Rust binary (aarch64 musl, 0.6 MB).
- **Capture.** It ports the reviewed Session byte for byte. `test_campreview.py` pins every struct and ioctl number
  against the Python builders.
- **Develop.** Each frame is developed in place from the VC0 dma-buf (cached, so sync costs about 0 ms):
  - the 2x2 Bayer quad (or a 4x4 block) becomes one XRGB8888 pixel;
  - black level, grey-world white balance, display gain and gamma go through three LUTs;
  - the LUTs are rebuilt every frame from the previous frame's statistics.
- **Output.** A triple-buffered shared-memory file (`/run/s22-campreview.shm`, 4 KiB header + 3 slots).
- **Options:**
  - sensor AE through `S_EXT_CTRLS` while streaming (exposure first, then analog gain);
  - an encoder FIFO (I420 or XRGB);
  - full-resolution raw stills from the running stream (`--still-at-frame`, or SIGUSR1).

| Configuration (rear unless noted) | fps | develop p50 | CPU (one core) | frame start → published p50 / p95 |
|---|---|---|---|---|
| 1020x766, 1 thread on the X2 (cpu7) | 29.6 | 9.1 ms | 28 % | 17.6 / 21.2 ms |
| 1020x766, 2 pinned workers (cpu4,5) | 30.0 | 6.2 ms | 33 % | 14.8 / 19.6 ms |
| 510x383 (4x4 bin), 1 thread on cpu7 | 30.0 | 5.8 ms | 21 % | 13.7 / 14.6 ms |
| front 912x684, 2 workers | 28.8 | 5.0 ms | 30 % | 39.1 / 42.9 ms |
| 1020x766 + sensor AE, 300 s | 29.97 | 8.8 ms | 28 % | 17.0 / 17.9 ms |

- **Reader side.** `shm-reader.py`, polling every 4 ms like a shell would, saw 28.9 fps. Each frame was 2.5 ms old (p50) when read.
- **Glass to screen** = exposure/2 + readout + the numbers above + one display refresh (about 8–16 ms).
  - Rear at 10 ms exposure: about 40–45 ms.
  - Front at 30 ms exposure: about 70–75 ms.
  - Both are well under the 150 ms target.
- **Stalls.** The worst stalls (150–450 ms, a few per run) happen when llama-server shares the same cores.
  Pin the camera thread to the X2, or give it SCHED_FIFO. This was not tested.
- **Threads.** A thread spawned per frame does not spread across cores on this kernel: Exynos EMS wakes a new
  3 ms task on its parent's core. Persistent workers, each pinned to one core, do scale: 4 big cores develop in 2.3 ms.
- **Little cores.** On an A510 the develop takes 42 ms (old loop), so keep it on the big cores.
- **Stills without a restart.**
  - From a request to 4 full-resolution raws in RAM: **107 ms** (3 frame intervals).
  - The raws are written after teardown, in the `.raw` + `.raw.json` format `s22-develop --stack` reads.
  - At powersave clocks `s22-develop --stack` then took 3.3 s; it was 0.4–0.5 s with the screen on (2026-10-22 develop evidence).
- **Sensor AE.** 5 `S_EXT_CTRLS` updates while streaming, with no error and no lost frames. The scene was dark
  (the phone was in a pocket), so it ran to 30 ms / 16x.

## 4. Video

| Path | Result |
|---|---|
| x264 0.164 ultrafast (temporary Alpine binary, removed afterwards), 1020x766 | **17 fps**, cannot keep up. Same with 4 threads. The scene was pure gain-64 noise (50 Mbit/s), the worst case. |
| x264 ultrafast, 510x382, 2 threads | **29.5 fps**, 0 drops, 5.9 Mbit/s |
| Hardware JPEG (SMFC, `/dev/video12`) | 3.8 ms per 1020x766 frame, 1.1 ms CPU, but **the test was invalid and crashed the phone** (below) |
| MFC H.264/HEVC (`/dev/video7 s5p-mfc-enc`) | Not tried. The driver is loaded, but `mfc_fw.bin` is missing on the phone. It is in the stock vendor image (1 MiB, sha256 `2937555870d5…`). It loads through imgloader with the S2MPU/TZ verify, the same path the NPU firmware uses. Staging it is a separate reviewed step. |

### Incident: reboot at 12:20

My hardware-JPEG bench used driver-allocated MMAP buffers. For the compressed queue, `smfc_vb2_queue_setup()`
allocates **only PAGE_SIZE (4 KiB)**. The comment there says the hardware stops when the buffer is full, but
the driver reported 260 KB written. Android hands this queue its own large DMABUF.
- The first two runs (90 and 30 frames) "succeeded", and the saved JPEG was cut at 4096 bytes.
- The third run rebooted the phone. No pstore or last_kmsg was kept.
- The phone came back by itself in about 1 minute, with keepalive, phoned, touchd/fastshell, autosleep, sway and Tailscale up.
- The camera streamed normally right after.

The bench was deleted from the code. A retry needs a DMABUF capture buffer at least as large as the raw frame, and a review first.

## 5. Recommended architecture for the Camera page

1. **One camera daemon** owns the V4L2 stream. It is s22-campreview grown into a service, Rust, no Python per frame.
   - It opens one continuous stream when the page opens and closes it when the page closes.
   - Use 4 leader + 4 VC0 buffers.
   - Start to first frame is about 0.25 s, and stop takes about 0.1 s, so switching between front and back costs about 0.4 s.
2. **Preview.**
   - Use 2x2 bin-to-RGB: 1020x766 rear, 912x684 front.
   - Develop on one big core, pinned to the X2 (or 2 pinned A710 workers). That is 6–9 ms and about 30 % of a core at 30 fps.
   - Publish to the shm triple buffer. The shell maps it and blits the latest slot (or imports it as a dma-buf for zero-copy scanout later).
   - The CPU path is enough, so the GPU/Vulkan path is not needed for preview.
   - Add rotation and mirroring in the blit.
3. **AE/AWB.**
   - Sensor AE every 6 frames via `S_EXT_CTRLS`: exposure up to 30 ms, then analog gain.
   - Display gain and white balance from the same statistics.
   - The stills then need no separate meter frame, which saves about 0.5 s against today's touchd flow.
4. **Shutter.**
   - Take the next 4 frames' raws from the running stream (about 110 ms) and show the preview frozen at once.
   - Run `s22-develop --stack` in the background (0.4–0.5 s with the screen on). Tap to saved JPEG is about 0.6 s, against 2.1 s today.
5. **Video.**
   - Feed the daemon's frames to x264 ultrafast at 510x382 (works now, 30 fps).
   - For 1020x766 or 1080p, the next step is MFC H.264 after a reviewed `mfc_fw.bin` staging, or SMFC MJPEG with a correct DMABUF capture buffer.
   - Keep the preview running while recording; the encoder gets its own core.
6. **Power/robustness.**
   - Drop an inhibit file in `/run/s22-autosleep/inhibit.d/` while streaming. SSH sessions inhibited sleep during these tests; a screen-off recording would have no SSH session.
   - Ask for schedutil or a frequency floor while the page is open.
   - On exit, SIGTERM runs the full teardown. A kill -9 still closes the fds, which powers the sensor down.

Not verified: real scenes (the phone was pocketed, every frame was dark), colour, orientation, physical touch, screen-on clocks, and SCHED_FIFO.

## Files

- `tools/hardware/camera/s22-campreview/`:
  - `stream`: the live preview;
  - `bench`: develop speed on a dma-buf, without the camera;
  - `dump-structs`.
- `tools/hardware/camera/preview/profile-stream.py`: profiles today's Python Session.
- `tools/hardware/camera/preview/shm-reader.py`: the reader side of the shared memory.
- `tools/hardware/camera/preview/test_campreview.py`: pins the Rust ioctl bytes to `s22-camera.py`.

On the phone, `/srv/s22/tmp/campreview/` holds the binary (sha256 `528c22fd…`) and these scripts. Nothing was
installed into `/srv/s22/hardware/bin`. The x264 binary and every frame and test file were deleted.

```sh
cd tools/hardware/camera/s22-campreview && cargo test --offline --release && cargo build --offline --release
python3 ../preview/test_campreview.py
CARGO_TARGET_AARCH64_UNKNOWN_LINUX_MUSL_LINKER=rust-lld cargo build --offline --release --target aarch64-unknown-linux-musl
# phone:
s22-campreview stream --sensor rear --seconds 10 --cpus 7 --ae --shm /run/s22-campreview.shm
```
