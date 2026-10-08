# S22 rear camera: raw still capture without the ISP

`s22-camera.py` captures raw Bayer frames from the rear wide sensor (S5KGN3)
through the CSIS write DMA. It does not use the Pablo ISP, the DDK library or
TrustZone. It converts the frames to PNG in pure Python. It is not yet tested
on the device.

## What the earlier work proved, and what it did not

The 2026-10-06 test (`evidence/camera-20261006/README.md`) used the kernel
self-test `/sys/module/fimc_is/parameters/test_sensor_run`
(`testing/pablo-test-sensor-self.c`) with `echo "1 0 2040 1532 30"`, then
`echo 0`. The kernel log shows `sensor fcount: 83`: 83 frames came in over MIPI
CSI. In mode23, VC0 is `VC_NOTHING`, which means it needs a buffer from user
space. The self-test never queues a buffer, so **no pixel data was ever written
to memory.** The client keeps the self-test's settings and adds the V4L2 buffer
path around them.

## Pipeline (read from the kernel source, `fimc_is` build 59e54c03)

| node | sysfs name | role | buffer type |
|---|---|---|---|
| `/dev/video101` | `exynos-is-ss0` | sensor-group leader (M2M direction) | OUTPUT_MPLANE |
| `/dev/video210` | `exynos-is-ss0vc0` | CSIS VC0 write DMA (the Bayer image) | CAPTURE_MPLANE |

The node number is 100 plus `IS_VIDEO_*_NUM` (SS0 = 1, SS0VC0 = 110). Two
corrections to the 2026-10-08 notes (`v4l2-path-20261008.md`):
- `video110`–`113` are the CSTAT nodes, which are 3AA statistics in the ISP chain. They are not CSI capture nodes.
- With `CONFIG_USE_SENSOR_GROUP=y`, the scenario field in `S_INPUT` is at bit 26, not bit 28.

The client reads both sysfs names and refuses to run if either does not match.

The ioctl sequence matches the self-test settings: VISION scenario
(stand-alone, no ischain), position 0, 2040x1532 at 30 fps, ex_mode 0, leader
format `SRP6`, and a non-blocking front start.

1. `open(video101)`, then `S_INPUT 0x04000101` (scenario 1, position 0, vindex 1, leader 1).
2. `S_PARM` (CAPTURE_MPLANE, 1/30), then `S_CTRL IS_S_SENSOR_SIZE` = `2040<<16|1532`, then `S_CTRL SET_EXTENDED_MODE` = 0.
3. `S_FMT` on the leader: OUTPUT_MPLANE, `SRP6`, 2 planes.
4. `open(video210)`, then `S_FMT` CAPTURE_MPLANE `BYR2` (SBGGR16) with `bytesperline` = 4096.
5. `REQBUFS` with `V4L2_MEMORY_DMABUF` on both nodes. The buffers come from
   `/dev/dma_heap/system`. MMAP cannot work here: Pablo maps the meta plane with
   `dma_buf_get(m.fd)`.
6. `QBUF` on VC0, then `STREAMON` on VC0. VC0 must start before the leader,
   because `is_subdev_start()` refuses once the leader has started.
7. `QBUF` on the leader. Its meta plane holds a `camera2_shot_ext`: magic
   `0x56789234` at offset 37936, and `node_group.capture[0]` = {vid 110,
   request 1, 2040x1532, `buf.length` 0}. Then `STREAMON` on the leader, then
   `S_CTRL IS_S_STREAM` = `0x10000001`.
8. Loop: `select`, then `DQBUF` VC0. Requeue each done leader buffer with a fresh shot.
9. In `finally`: `IS_S_STREAM` off, `STREAMOFF` leader, `STREAMOFF` VC0,
   `REQBUFS(0)` on both, close VC0, close leader, release the dma-bufs.

The VC0 data is CSIS `U10BIT_UNPACK_MSB_ZERO`: 10-bit values in little-endian
u16, with a stride of `align(2*w, 32)`. The GN3 colour order is GBRG. Without
the ISP there is no lens shading correction, no defect correction and no AE
loop. The client applies black level 64, gray-world white balance, a 99th
percentile exposure stretch and gamma 2.2, at 1/4 resolution by default
(510x383). The struct sizes and offsets were checked against a probe compiled
with the real kernel build flags. `test_s22_camera.py` pins them.

## Before the first run

- **OIS firmware:** without `is_mcu_fw.bin`, the sensor start waits about 60 s
  in the firmware fallback. The client looks for it in
  `/system/vendor/firmware`, `/vendor/firmware` and `/lib/firmware`, and
  refuses if it is missing. Staging it is a separate reviewed step. To run
  anyway, add `--allow-fw-stall`; the first-frame timeout then becomes 90 s.
- Nothing else should be using the camera: `test_sensor_run` must show
  `act 0`, which the client checks.

## First device test (lead runs this)

```sh
python3 -I -B tools/hardware/camera/s22-camera.py list
python3 -I -B tools/hardware/camera/s22-camera.py capture -v \
    --out /tmp/cam0.png --raw /tmp/cam0.raw --frames 1 --skip 4 --deadline 120
dmesg | tail -n 80
```

On success, the last stdout line is one JSON object:

```json
{"ok": true, "path": "/tmp/cam0.png", "width": 510, "height": 383, "format": "png",
 "node": "/dev/video210", "frames": 5, "ms": 3000, "stride": 4096, "pixfmt": "BYR2",
 "raw": "/tmp/cam0.raw", "teardown_errors": [], "develop": {...}}
```

The run also writes `/tmp/cam0.raw` (6,275,072 bytes = 4096 x 1532) and
`/tmp/cam0.raw.json`. To re-develop the raw frame later, on the phone or the rig:
`s22-camera.py develop /tmp/cam0.raw --out x.png --scale 2 [--bayer GBRG --black 64]`.
On failure, `"ok": false` and `"error"` names the ioctl that failed. The
`-v` ioctl log goes to stderr.

Record a short sequence (frames are developed after the stream stops):
`s22-camera.py record --seconds 3 --fps-limit 5 --out /tmp/seq [--mjpeg]`.
`--mjpeg` uses `gst-launch-1.0` from PATH. If that is missing, it runs it
inside the Arch chroot, but only when the output directory is under
`/mnt/omarchy-trial`.

## Independent review (2026-10-08): GO-WITH-CHANGES, applied

- Blocking stream start (`IS_S_STREAM` = 1, noblock 0). The non-blocking start raced teardown: CSI DMA
  could arm into freed buffers, and a SysMMU fault on the camera block panics the kernel.
- VC0 buffers carry 1 MiB of guard pages, because no vertical clamp on the CSIS DMA is visible.
- On real hardware the geometry is locked to 2040x1532 at 30 fps until a capture is proven.
- `/vendor/firmware` was dropped from the firmware search: the kernel never looks there.
- Struct offsets were cross-checked against DWARF from `fimc-is.ko`, and all match.

## Risks and rollback

- **First real DMA write by CSIS VC0 into memory.** The buffer is sized from
  the kernel's own stride formula and that stride is passed as `bytesperline`,
  so `vb2` rejects a buffer that is too small. The leader image plane (14 MB,
  `SRP6`) is a dummy that no DMA writes to.
- Kernel `BUG()` traps found in the source and avoided:
  - `capture[n].buf.length` must be at most 17. It stays 0, so no user plane pointers are read.
  - `frame->stream` must be non-NULL. It is set from the VC0 meta plane.
- If the image is black or noise, try these one at a time:
  - `--exposure-us 20000 --iso 400` (manual sensor exposure through `shot.ctl`, AE off).
  - `--bayer` with a different order.
  - `--pixfmt raw10p` (packed; the decoder tries MIPI and LSB packing and picks the better fit).
- Hangs: the frame timeout counts from the last VC0 frame (default 5 s; 20 s
  for the first frame). A SIGALRM hard deadline (default 120 s) aborts the
  run, and teardown still runs with its own 30 s window. A stuck kernel ioctl
  cannot be interrupted from user space.
- Rollback: the client's `finally` block runs the stop sequence above. If the
  process is killed instead, closing the fds still runs `is_sensor_close()`,
  which powers the sensor down. As a last resort, reboot.
- The client does not load modules, write sysfs or stage firmware.
  `list --probe NODE` opens a node, which powers the sensor resource.
