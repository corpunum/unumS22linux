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

Record a short sequence (frames are developed after the stream stops). Each record now defaults to 60 captured frames per camera stream, fully tearing down and reopening the camera between chunks; override with `--chunk-frames N` only after validating a larger safe limit. This avoids the long-stream `kthread_stop` wedge observed during record, at the cost of a gap at each chunk boundary:
`s22-camera.py record --seconds 3 --fps-limit 5 --chunk-frames 60 --out /tmp/seq [--mjpeg]`.
`--mjpeg` uses `gst-launch-1.0` from PATH. If that is missing, it runs it
inside the Arch chroot, but only when the output directory is under
`/mnt/omarchy-trial`.

### record-safe.py: chunked recorder with deferred MJPEG encode

`record-safe.py` (sibling of `s22-camera.py`) is the chunked recorder that
`tools/s22d` `camera.record` calls. Instead of one long stream with
`--chunk-frames`, it runs a chain of independent capture-style sessions: each
session opens/stream/takes at most `--max-frames-per-session` frames (default
7, the capture path's proven-clean length) and runs the capture path's full
teardown before the next session starts. Raw frames are written only after
that session's teardown; the MJPEG AVI encoder runs only after the last
session's teardown, so no encoder ever runs while a stream is open.

```
record-safe.py --sensor rear|front --seconds N --out DIR
               [--max-frames-per-session K] [--fps N] [--exposure-us N]
               [--gain X] [--deadline S] [--scale N]
               [--encoder auto|gst|python] [--keep-raw]
```

The last stdout line is one JSON object:
`{"ok": true, "path": "DIR/video.avi", "frames": 25, "sessions": 4,
"seconds": 5, "effective_fps": 4.2}`. It imports the capture path from
`s22-camera.py` (run_stream/Session, not reimplemented). Encoding prefers
`gst-launch-1.0` via the reviewed `make_mjpeg` path (`--encoder auto`);
without it, a stdlib-only baseline-JPEG encoder + MJPEG AVI muxer built into
the script produces `DIR/video.avi` (verified with ffprobe/ffmpeg). On session
failure it prints `{"ok": false, "error": ...}` and does not encode.

## Independent review (2026-10-08): GO-WITH-CHANGES, applied

- Blocking stream start (`IS_S_STREAM` = 1, noblock 0). The non-blocking start raced teardown: CSI DMA
  could arm into freed buffers, and a SysMMU fault on the camera block panics the kernel.
- VC0 buffers carry 1 MiB of guard pages, because no vertical clamp on the CSIS DMA is visible.
- On real hardware the geometry is locked to 2040x1532 at 30 fps until a capture is proven.
- `/vendor/firmware` was dropped from the firmware search: the kernel never looks there.
- Struct offsets were cross-checked against DWARF from `fimc-is.ko`, and all match.

## Manual exposure and gain (review 2026-10-09)

The first captures (`evidence/lead2-20261008/camera`) were black: the raw values sat at
the black level of 64. Without the DDK, nothing programs the GN3 integration time.
`is_sensor_setting_mode_change()` logs `invalid exp_gain_count(0)` and returns before any
exposure write, because only the DDK sets `exp_gain_cnt` and `mode_chg`. The `shot.ctl`
exposure from `--exposure-us`/`--iso` is also never read: `copy_sensor_ctl()` is a DDK
callback. The full code-path trace is in the rig-local brief `s22-camera-exposure-review-20261009.md`.

The one path from user space to the CIS that works on this kernel is
`VIDIOC_S_EXT_CTRLS` on `/dev/video101`. It runs `is_ssx_video_s_ext_ctrls()`, then
`is_sensor_s_ext_ctrls()`, then `sensor_module_s_ext_ctrls()`, and its `default:` case calls
`sensor_module_s_ctrl()`:
- `V4L2_CID_SENSOR_SET_AE_TARGET` (µs) goes to `sensor_gn3_cis_set_exposure_time()`, which writes 0x0202 and 0x0704.
- `V4L2_CID_SENSOR_SET_ANALOG_GAIN` (permille) goes to `sensor_gn3_cis_set_analog_gain()`, which writes 0x0204.

Plain `VIDIOC_S_CTRL` with `SET_AE_TARGET`, `SHUTTER` or `GAIN` returns success but does
nothing: `is_ssx_video_s_ctrl()` catches those IDs and calls `CALL_MOPS` on
`module->ops == NULL`.

`--cis-exposure-us N --cis-again X` sends one `S_EXT_CTRLS` after the blocking
`IS_S_STREAM` on, and only if `IS_G_STREAM` reads 1. The client clamps the values to
100–30000 µs and 1–16x, and the CIS driver clamps them again.

The gain goes out as three writes: `(g-1, g, digital 1x)`. This works around a
fall-through in `sensor_module_s_ctrl()`: when the requested analog gain equals the cached
value, the driver sets the digital gain instead. That cache survives close and open with
`CONFIG_CAMERA_VENDER_MCD`. If the call fails, the error is recorded in `result["cis"]`,
nothing is retried, and the capture continues. The driver stops at the first failing
control, so an earlier control in the list may already have been written. The result
also reports the analog gain read back from register 0x0204.

```sh
python3 -I -B tools/hardware/camera/s22-camera.py capture -v --out /tmp/e1.png \
    --raw /tmp/e1.raw --frames 1 --skip 6 --deadline 120 \
    --cis-exposure-us 10000 --cis-again 1
```

## Read-only sensor state dump (review v2, 2026-10-09)

Exposure and gain now reach the sensor (`cis.applied`, and register 0x0204 reads back 0x80), but
the frames still read 62–66. Two opt-in, read-only probes look at what the sensor is
actually doing. Both take a snapshot after the stream starts (`after_start`) and another
after the last kept frame (`before_stop`), and record it in `result["dump"]`:

- `--cis-dump` sends `VIDIOC_G_CTRL` for:
  - `IS_G_STREAM`, `IS_G_DTPSTATUS` and `IS_G_MIPI_ERR` (the CSIS `error_id_last`): kernel state bits only, no I2C.
  - `GET_ANALOG_GAIN` and `GET_DIGITAL_GAIN`: GN3 I2C reads of 0x0204 and 0x020E.
- `--cis-i2c-bus N` reads a fixed list of GN3 registers through `/dev/i2c-N`.
  - Each read is one `I2C_RDWR` containing a 2-byte address write and a 1- or 2-byte read to address 0x10. This is the same framing as `is_sensor_read16()`. No register is written.
  - The list covers 0x0100 mode, 0x0005 frame count, 0x0202 CIT, 0x0204 and 0x020E gains, 0x0340 and 0x0342 timing, 0x0344–0x034E window, 0x0600 and 0x0620 test pattern, 0x0900 binning, 0x0112 format, and the retention flags.
  - Every address is a page-0x4000 CCI register below 0x6000. The client never reads 0x6000 and above, because those are the page and indirect-access controls.
  - The dump stops if 0x0002 does not read the GN3 revision 0xC000.
  - The client refuses to run unless `/sys/bus/i2c/devices/N-0010/of_node/compatible` is `samsung,exynos-is-cis-gn3`.
  - It does not load `i2c-dev` itself. Loading that module is a separate step.

## Analog-rail and setfile-echo probes (review v3, 2026-10-09)

The v2 dump showed that the sensor's digital side matches the driver's configuration on every
register read: it is streaming, the frame counter advances, CIT 0x0830, gains 0x80/0x100,
timing, window, RAW10, binning, and the test pattern off. Yet the pixels contribute nothing.
Two more opt-in, read-only probes:

- `--rails` reads `/sys/class/regulator/*/{name,state,microvolts,num_users}` for the GN3
  power-table rails: S2MPB02 BUCK1/2, BB and LDO1/6/7/10/11/13/14, including VDDA_2.2V_CAM =
  LDO14. It snapshots before open, after start, before stop and after close. The S2MPB02
  driver reads `state` and `microvolts` from the PMIC itself and does not cache them.
- `--cis-i2c-extended`, used together with `--cis-i2c-bus N`, reads back the 47 page-0x4000
  registers below 0x6000 that the driver's Global and mode-18 setfiles write, plus SMIA 0x0006
  (pixel order) and 0x0008 (data pedestal). It lists every mismatch. A mismatch is a hint
  only, because the firmware may rewrite some of these registers.

## Front camera preset (review v5, 2026-10-09)

`--sensor front` selects the IMX374. Its DT node is `is_sensor_imx374@10`: `position = 1`, which is
SP_FRONT, and module `id = 1`, which maps to `core->sensor[1]`. That gives:
- leader `/dev/video102` (`exynos-is-ss1`; S_INPUT vindex 2, so S_INPUT = `0x04010201`);
- VC0 `/dev/video214` (shot `capture[0].vid` 114). Its sysfs name is the kernel-bug name, which
  the client accepts only on video214 for this preset.

The geometry is locked to DT mode9, which is setfile 8: 1824x1368@30 RAW10, stride 3648, CFA
GRBG. Sensor-write and raw-I2C flags are refused with `--sensor front`; they were reviewed for
the GN3 only. `--sensor rear` is the default and sends the same ioctls with the same bytes as
before.

## Risks and rollback

- **First real DMA write by CSIS VC0 into memory.** The buffer is sized from
  the kernel's own stride formula and that stride is passed as `bytesperline`,
  so `vb2` rejects a buffer that is too small. The leader image plane (14 MB,
  `SRP6`) is a dummy that no DMA writes to.
- Kernel `BUG()` traps found in the source and avoided:
  - `capture[n].buf.length` must be at most 17. It stays 0, so no user plane pointers are read.
  - `frame->stream` must be non-NULL. It is set from the VC0 meta plane.
- If the image is black or noise, try these one at a time:
  - `--cis-exposure-us 10000 --cis-again 2` (see "Manual exposure" above). `--exposure-us`/`--iso` only fill
    `shot.ctl`, which nothing reads without the DDK. The 2026-10-08 run proved this.
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

## Review v6 (2026-10-09): the sensors image light; the scenes were dark

The "black" frames were dim scenes, not a dead pixel path. Both sensors integrate
light in the VISION scenario without the DDK:

- **Front IMX374.** At 30 ms / 8x (8x is its analog maximum; asking for 16x is clamped)
  the frame shows soft optical structure. At 1 ms / 8x the same scene is pure noise.
  Raising the display backlight from 128 to 510 lifted the scene from +0.85 to
  +5.5 codes above black, so most of what it saw was the screen's own light
  reflected off something close.
- **Rear GN3.** The earlier W-0BE4 frame (30 ms / 16x) shows a sharp diagonal edge and
  a lit gradient once it is block-averaged and stretched.
- **The environment.** The rear ambient light sensor (`/sys/class/input/input3`, AMS
  TSL2510) read 1–2 counts at gain 4096x, which is near-total darkness. With the
  rear torch on, it saturated. The rear lenses are against a surface.

Two tooling gaps hid the signal. Reviewers looked at p50 (which a 1–2 code
scene does not move), and the develop step floored the auto white point at 16 codes.

- `--cis-exposure-us` / `--cis-again` are now allowed with `--sensor front`. It is the
  same `S_EXT_CTRLS` → `sensor_module_s_ctrl` path, and
  `sensor_imx374_cis_set_exposure_time/_analog_gain/_digital_gain` clamp and
  group-hold the same way as the GN3 functions. The i2c-dev probes and the 0x0BE4
  write stay GN3-only.
- `develop --min-white X` lowers the auto white-point floor. The default stays 16.
- `stack DIR|RAW... --out S.raw [--drop 2]` averages a `record --keep-raw`
  directory into one raw that stores mean×16, recorded in the sidecar's `stack_scale`.
  `develop` reads that and treats the raw as 14-bit.

```sh
s22-camera.py record --sensor front --allow-fw-stall --out /tmp/seq --seconds 2 \
    --fps-limit 30 --keep-raw --cis-exposure-us 30000 --cis-again 8
s22-camera.py stack /tmp/seq --out /tmp/seq.raw
s22-camera.py develop /tmp/seq.raw --out /tmp/seq.png --bayer GRBG --min-white 0.5
```

The DDK path (`is_lib.bin`/`is_rta.bin`) is kernel-executed and loads only when an
ischain opens. It is not needed for raw capture and has **not** been run. It is MEDIUM
risk and needs owner approval.

## Software ISP: `s22-develop` (2026-10-22)

`s22-develop/` is a static Rust binary (aarch64 musl, 0.6 MB, installed as
`/srv/s22/hardware/bin/s22-develop`). It turns a raw dump plus its `.raw.json` sidecar into a photo.
It replaces the old `pipeline/` prototype (rawpy/LibRaw on the rig), which needs numpy. The phone has no numpy.

| Stage | What it does |
|---|---|
| Black / white | Black 64 and white 1023 (10-bit), times `stack_scale` for `stack` raws. |
| Defect pixels | A pixel more than max(0.04, 0.6 × neighbour max) away from its 4 same-colour neighbours is replaced by their median. |
| Lens shading | Generic radial gain 1 + k·r², k = 0.35 (`--lsc`). It is not the stock LSC table, which is not decoded. |
| Offset / flare | Per colour, the 1st percentile of 16x16 block means goes to black, capped at 35 % of that colour's mean (`--no-auto-black` turns it off). |
| White balance | Grey-world over 2x2 quads that are neither clipped nor in the noise floor; gains are clamped to 0.4–5 (`--wb grayworld\|none\|R,B`). |
| Demosaic | Malvar-He-Cutler at full resolution; `--half` bins 2x2 instead. |
| Colour matrix | The stock matrix (`--ccm stock\|none\|9 floats`), see below. |
| Chroma denoise | A linear-light chroma blur before any clipping, then a gamma-domain one. The radius grows with the digital gain (3/5/8 px). |
| Exposure / tone | The bright 0.5 % goes to 0.95, with the mean capped at 0.28 linear (`--ev` shifts it). Soft highlight roll-off, sRGB gamma and a 20 % smoothstep S-curve. |
| Output | JPEG (`--out`, q 90), PNG (`--png`), and a preview PNG scaled to fit (`--preview`, `--preview-max 1016x1400`). `--before` writes the raw mosaic as grey. `--rotate` rotates the output. |

- **Stacking:** `--stack` averages `RAW.0`, `RAW.1`, …. They come from the new `s22-camera capture --raw-burst`, which writes every kept frame
  after teardown. Without alignment, so the phone has to be still for about 4 frames (130 ms at 30 fps).
- **AE:** `s22-develop meter RAW --exposure-us E --gain G [--max-gain N]` reads the centre-green mean of a meter frame.
  It prints the next exposure/gain pair as JSON: target mean 0.16, exposure first up to 30 ms, then analog gain.

**Colour matrix source.** A scan of the stock Pablo setfiles (`setfile_imx374.bin`, `setfile_gn3.bin`) looked for
int16 Q8 3x3 matrices whose rows sum to 256 and have a positive diagonal. It found four per-illuminant matrices and one generic saturation matrix,
at the same relative offsets in both files.
- The one in use is `[332 -47 -29; -82 394 -56; -26 -105 387]/256`, at IMX374 @0x17050 and GN3 @0x170e0. It is the least saturating of the four.
- The others: @0x16e68 `[380 -113 -11; -30 297 -11; 30 -141 367]`, @0x1732c, @0x17514. The generic one, @0x17608, is 1.61/-0.31.

Which illuminant each matrix belongs to is not decoded, and the colour accuracy was not measured (no chart).

**Shell path.** s22-touchd's `camera_capture` (optional `sensor`: `rear`/`front`) runs:
1. a meter frame (10 ms, 2x);
2. `s22-develop meter`;
3. a 4-frame `--raw-burst` at the metered values;
4. `s22-develop develop --stack`.

Raws go to `/srv/s22/tmp/camera-work`, not to the 64 MB run tmpfs. The preview goes to `RUN/camera-latest.png`, which is
what both shells show. The full JPEG goes to the OpenUnum gallery as `cam-<ts>-<sensor>.jpg`. If `s22-develop` is missing
or fails, touchd shows the client's old quick PNG instead, so the old behaviour is the fallback.
The front camera takes about 2.1 s from tap to picture.

```sh
s22-camera capture --sensor front --raw /srv/s22/tmp/x.raw --raw-burst --frames 4 --skip 3 \
    --out /srv/s22/tmp/x-quick.png --allow-fw-stall --cis-exposure-us 30000 --cis-again 8
s22-develop develop /srv/s22/tmp/x.raw --stack --out x.jpg --preview x-prev.png --before x-raw.png
cd s22-develop && cargo test --offline --release      # 9 tests (demosaic per pattern, WB, defects, AE, I/O)
```

Evidence: [`evidence/camera-develop-20261022/`](../../../evidence/camera-develop-20261022/README.md).

## Live preview spike: `s22-campreview` (2026-10-22)

A continuous stream works. Both sensors run at 30 fps for minutes, given **4 leader (shot) buffers**; with 2,
about 30 % of frames are lost. `s22-campreview` (Rust) develops each frame in place from the VC0 dma-buf:
- the rear 2040x1532 becomes a 1020x766 XRGB8888 preview by 2x2 bin, with LUT white balance, gain and gamma;
- the develop takes 6–9 ms on a big core, about 30 % of one core;
- frames go to a shared-memory triple buffer, published 15–18 ms after the CSI frame start (rear);
- sensor AE runs through `S_EXT_CTRLS` while streaming;
- raw stills are taken from the running stream.

Do **not** use the SMFC hardware JPEG with MMAP buffers. The compressed queue gets only 4 KiB, and the encoder
overran it and rebooted the phone. Numbers, video results and the recommended Camera-page architecture:
[`evidence/camera-preview-20261022/`](../../../evidence/camera-preview-20261022/README.md).
