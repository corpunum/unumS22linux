# Camera sensor stream test — 2026-10-06 (lead session)

## Result: the rear wide sensor streams on native Linux (no frame saved yet)
The running kernel has the Samsung Pablo self-test parameter
`/sys/module/fimc_is/parameters/test_sensor_run`
(`drivers/media/platform/exynos/camera/testing/pablo-test-sensor-self.c`).
`echo "1 0 2040 1532 30"` (rear position 0 = S5KGN3, DT mode23 2040x1532@30)
followed by `echo 0` ran with **no crash**. The kernel log shows:
- resource open, then the full GN3 power sequence and CSIS stream-on;
- at stop: `[G:SS0] sensor fcount: 83` (**83 frames received over MIPI CSI**),
  then a clean power-down and `is_sensor_close():0`.
This path uses only the sensor resource. The ISP DDK library (`is_load_bin`,
loaded only for the ISCHAIN resource) and its TrustZone verification are not
involved. So the previously reported "TrustZone blocker" applies to the ISP
library path, not to sensor streaming. The 8 `tz_worker_thread` tasks in D
state are idle waits in `tz_worker_handler` (that is where the steady ~14 load
average comes from), not hung requests.

## Issue seen
The OIS MCU asks for `is_mcu_fw.bin`. It is not in the ramdisk's
`/vendor/firmware`, so the start blocks for the 60 s sysfs fallback before
streaming. A copy exists on the rig (stock vendor extract). The fix is boot-time
staging, the same way the audio firmware is staged.

## Not done
No image data was captured. Saving a frame needs a V4L2 client on the sensor
node (`/dev/video101`, S_INPUT/S_FMT/REQBUFS/QBUF with a meta plane carrying
SHOT_MAGIC). That client was not completed in this session. A processed (ISP)
image would additionally need the Pablo DDK library and its TrustZone loading.
