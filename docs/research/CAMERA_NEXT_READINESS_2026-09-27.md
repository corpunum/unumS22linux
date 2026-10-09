# Next camera acceptance step — 2026-09-27

## Decision

After the coordinator's camera-image boot is independently accepted, the
smallest new camera operation is one `/dev/video101` open, one
`VIDIOC_QUERYCAP`, and close. Do not stage camera firmware for this operation:
in the pinned source path, `is_vidioc_querycap()` returns static driver/card
and capability fields, while sensor setfile and ISP-binary loads belong to
later ischain/library paths. This operation tests node registration plus the
sensor open/close and resource-resume path; it does not test firmware
compatibility, sensor response, frame capture, or Android camera HAL behavior.
It includes no userspace streaming ioctl or capture request. However, close
dispatches sensor deinitialization, and the selected sensor's CIS deinit may
internally issue stream-on/off operations; treat this as potentially
sensor-streaming until the active sensor and configuration are resolved.

This is still a powered hardware trial, not a passive inventory. `is_video_open()`
dispatches the sensor leader to `is_sensor_open()`, which opens the device
manager/CAMIF/CSI and calls `is_resource_get()`; the sensor resource path
runtime-resumes the device. `is_sensor_close()` dispatches sensor deinit and
calls `is_resource_put()`. At pinned source commit
`3fca50941422439b2019db2e4a3dc1016b2138a1`, the generic deinit path invokes
the selected CIS deinit operation. Its pinned GN3 implementation is
`sensor_gn3_cis_deinit()`; when `USE_CAMERA_SENSOR_RETENTION` is enabled and
`sensor_gn3_load_retention` is false, GN3 deinit calls CIS stream-on/wait and
stream-off/wait. The pinned rsv v01 vendor config enables that option. This is
a conditional source path, not evidence that the active device uses GN3 or
executes those calls. The active sensor/module/configuration remains
unresolved. Source chain: `is-device-sensor_v2.c` dispatches
`V4L2_CID_SENSOR_DEINIT`, `is-device-module-base.c` calls the CIS `cis_deinit`
operation, and `is-cis-gn3.c` implements the conditional stream sequence;
`vendor/mcd/rsv/is-vendor-config_rsv_v01.h` enables the retention option.
`VIDIOC_QUERYCAP` itself is static, but reaching it requires the prior
power-affecting open. The host source checkout at
`3fca50941422439b2019db2e4a3dc1016b2138a1` includes the reviewed PHY-LDO and
runtime-PM failed-acquire unwind commits; the packaged candidate image is
`b10412715756da3cc8ee221368b49f179cc0c64ab7bd2802976480905e6d8d2f`, and its
`fimc_is` GNU build ID is
`59e54c032c545fff3ba52156f226fb6d69aadf64`. Build/test evidence does not
replace a post-boot identity receipt.

## Required gates before that one operation

1. The coordinator must have an accepted forward reboot-observer receipt for
   the exact camera image, running kernel GNU ID
   `b2dda820b18d410d9bf12f1bd2584567d545991d`, and loaded `fimc_is` GNU ID
   `59e54c032c545fff3ba52156f226fb6d69aadf64`. A reboot ACK or image hash
   alone is insufficient. Account for the global one-shot marker separately;
   reconciliation is bookkeeping, not kernel-liveness acceptance. The completed
   camera trial is currently `not_accepted` and its marker was explicitly
   reconciled without acceptance. This gate remains closed; do not replay the
   completed observer to create a new acceptance window.
2. Rerun the existing passive inventory and independently verify the current
   node mapping: requested video node 101, actual `/dev/video101` character
   device (historical passive evidence: `81:17`), sysfs `dev`/name, driver and
   module links, and the sensor device-tree node. Do not infer the live
   character-device minor from the V4L2 node number. The previous `dtbo_idx=7`
   and r0s Makefile ordering only infer r27; the selected recovery DTBO has not
   been byte-verified against that source.
3. Resolve the active sensor/module and effective configuration, including the
   selected CIS deinit implementation and retention state. In particular,
   determine whether GN3 retention deinit can issue internal stream-on/off for
   this configuration. Until this is known, treat open/close as potentially
   stream-toggling; proceed only with separate explicit authorization for that
   exact powered operation.
4. Confirm stable native/power safety and a clear coordinator-owned trial
   state immediately before the operation. If any identity, node mapping,
   target, or power gate is absent or ambiguous, stop without opening.
5. If the preceding gates and separate authorization are satisfied, perform
   exactly one open → `VIDIOC_QUERYCAP` → close on the freshly verified node;
   record open/ioctl/close outcomes. Do not retry on failure. No userspace
   stream ioctl, format, buffer, control, sensor-ID, OTP/EEPROM, calibration,
   or capture request is part of this acceptance step; the close/deinit caveat
   above still applies.

## Firmware boundary

The passive inventory in
[`camera-readiness-once.py`](../../tools/hardware/camera-readiness-once.py)
stats a fixed set of firmware names in separate filesystem namespace roots;
its `readiness: not_assessed` result is intentional and does not authenticate
payloads. Pinned source uses `/system/vendor/firmware/` and `USE_ONE_BINARY`
selects `is_lib.bin`; the sensor-module setfile is selected from the active
module/DT mapping and loaded later by ischain code. The candidate Android
manifest's firmware comment names `S901BXXSNGZD7`, while the recorded device
build is `S901BXXSIFYI3`, and the active sensor overlay mapping is unresolved.
Therefore no camera binary should be staged until the exact release
provenance, hashes, active sensor/setfile mapping, and runtime namespace are
verified. If a later, separately reviewed operation needs firmware, define a
new exact-purpose gate for those bytes rather than expanding `QUERYCAP`.

## Host check

No defect was found in the existing inventory gate: it is bounded, uses
`lstat` for device nodes, never opens camera nodes or issues ioctls, and
reports inventory rather than readiness. No code change or device operation
was added here. Its hardware-free regressions passed:

```sh
python3 -I -B tools/hardware/test-camera-readiness-once.py
python3 -O -I -B tools/hardware/test-camera-readiness-once.py
```
