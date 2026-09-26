# S22 camera inventory and next evidence — 2026-09-27

## Current conclusion

The owner's September 27 continuation authorizes camera bring-up. The gates
below are source, artifact, and operation-safety prerequisites, not a renewed
request for broad camera permission. The worker performed host work only;
the coordinator alone may execute a reviewed device operation.

Camera readiness and capture remain **not assessed**. The only addition in this
wave is [`camera-readiness-once.py`](../../tools/hardware/camera-readiness-once.py),
a bounded, host-testable inventory intended for later use through the existing
pinned transport. It reads fixed `/proc`, `/sys`, and device-tree attributes,
stats fixed `/dev/videoN` and `/dev/mediaN` paths without opening them, and
stats a fixed firmware filename list in four candidate roots. It does not query
kernel logs, issue ioctls, request camera power, or access sensor ID, OTP,
EEPROM, or calibration attributes. Missing files or nodes in one namespace are
not a camera failure and do not establish their absence in another namespace.

The result deliberately says `readiness: not_assessed`. V4L2/media node
presence, module names, DT metadata, and firmware file sizes are inventory—not
proof of probe success, a connected sensor, valid firmware, image capture, or
Android HAL compatibility. Output contains no firmware bytes or hashes and no
raw kernel log/private trace data.

No phone or camera device was accessed by this worker. Preserved prior evidence
records node enumeration with `camera_capture_attempted: false`
(`evidence/s22-driver-readonly-20260926.json`); an earlier receipt likewise
describes video-node presence only (`docs/research/S22_DRIVER_TEST_RECEIPTS_2026-09-23.md`).
Those observations are historical and are not refreshed by the host tests in
this patch.

## Exact r0s source prerequisites

The read-only kernel checkout was pinned at
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`. The following is source
configuration, not proof that the running phone uses this exact merged config
or DT overlay:

* `arch/arm64/configs/r0s.config` says it is merged with
  `s5e9925_defconfig`; it selects `CONFIG_CAMERA_RSV_V01=y`, and modular CIS
  objects for 2LA, 2LD, IMX374, and 3K1. The base defconfig supplies
  `CONFIG_VIDEO_EXYNOS_PABLO_ISP=m`, `CONFIG_VIDEO_EXYNOS_MFC=m`, and
  `CONFIG_CAMERA_CIS_GN3_OBJ=m`. This is the Samsung Exynos Pablo camera stack,
  not a generic upstream ISP substitute.
* Candidate `arch/arm64/boot/dts/samsung/r0s/r0s_eur_openx_w01_r27.dts`
  declares four `samsung,sensor-module` entries with `status = "okay"`:
  `S5KGN3` → `setfile_gn3.bin`, `S5K2LA` → `setfile_2la.bin`, `S5K3K1` →
  `setfile_3k1.bin`, and `IMX374` → `setfile_imx374.bin`. These are static DT
  descriptions; they do not say the module is powered, present, or responding.
  The same candidate defines CSI/platform endpoints separately from those
  sensor-module descriptions. The currently active overlay-to-source mapping
  has not been established here.
* The r0s device manifest at
  `android_device_samsung_r0s` commit
  `142838b2bf6c1c8c69d9bee8cfcb4e846b0769bd` lists those four setfiles, plus
  `setfile_imx563.bin`. But its comment says unpinned blobs are from
  `S901BXXSNGZD7`, not the phone’s recorded `S901BXXSIFYI3`; the IMX563 file
  also has no matching module entry in this r27 source, and the r0s config
  disables the corresponding CIS object. Therefore this manifest is a
  candidate inventory, not provenance or a requirement to stage every listed
  blob. The source/config/manifest mismatch for 2LD likewise remains
  unresolved: config enables its object, while this r27 overlay and manifest
  do not name a matching 2LD sensor/setfile.
* The common S5E9925 device manifest at commit
  `7be96871126c07d6d51d250a4e0048b58e94eb63` lists the camera HAL
  `camera.s5e9925.so` and `is_lib.bin`, `is_rta.bin`, and `is_mcu_fw.bin`.
  `ischain/is-v10_1_0/is-config.h` defines `USE_ONE_BINARY`; camera
  `include/is-binary.h` then selects `is_lib.bin` (rather than the split ISP/VRA
  library names) and fixes the firmware search directory to
  `/system/vendor/firmware/`. The firmware manifest provenance still needs to
  be tied to the exact candidate release before any use.

The camera probe inventories these names only. `/vendor/firmware`,
`/system/vendor/firmware`, and the corresponding `/proc/1/root/...` locations
are separate candidate namespace views: an SSH chroot may not share the PID 1
mount view. The probe reports each root independently and must not interpret a
missing result from one as a driver or firmware failure. A `stat` result cannot
authenticate content, confirm a compatible build, or prove what the driver
actually requested.

## First-resource PHY-LDO failure and query-cap boundary

The proposed host patch [`camera-resource-unwind.patch`](../../tools/hardware/camera-resource-unwind.patch)
targets the exact pinned kernel source above; it has not been applied to a
running kernel. In `drivers/media/platform/exynos/camera/is-resourcemgr.c`,
`is_resource_get()` takes a wake reference and initializes DVFS before enabling
PHY LDOs sequentially. In the pinned source, an LDO enable error jumps to
`p_err`; that label increments both the resource and global counts
(`is-resourcemgr.c:1853-1860, 1969-1975`) without disabling earlier LDOs. For a
sensor leader, `is_sensor_open()` then closes the device manager and CSI on
`is_resource_get()` failure but does not call `is_resource_put()`
(`is-device-sensor_v2.c:1965-1998`). The video-open error unwind likewise
closes the V4L2 context, not the sensor resource (`is-video.c:2728-2733,
2757-2764`). This is a source-confirmed failed-open unwind gap.

The patch adds a small helper which rolls back only earlier successful LDO
votes, in reverse order, when a later enable fails. It preserves the original
`regulator_enable()` error even if rollback fails, while logging the rollback
error and hardware-state uncertainty. A separate first-acquire error label
attempts dynamic-memory deinit when configured, `is_resource_clear()`, and
`pm_relax()`, and reaches `rsc_err` without incrementing resource/core counts.
The normal success path still goes through `p_err` as before. Extracted-C
failure injection checks the helper and the exact proposed cleanup label; the
Python test also verifies the pinned source revision and that the patch applies
without modifying the vendor checkout.

These checks do not prove regulator hardware state. A provider can return an
enable error after physical state has changed; a rollback disable can itself
fail. The patch therefore makes a best-effort vote unwind, not a physical
power-off guarantee. It also intentionally does not address the separate
source issue that `pm_runtime_get_sync()`'s return is ignored in
`is_resource_get()` (`is-resourcemgr.c:1863-1877`); runtime resume can return an
error from the pre-hook or clock-on sequence
(`is-device-sensor_v2.c:4026-4041`). That remains a separate blocker to claiming
sensor-open cleanup.

`VIDIOC_QUERYCAP` itself only fills static capability fields in
`is_vidioc_querycap()` (`is-video.c:2914-2934`), but reaching it requires an
open. The source maps the first sensor leader to video id 1
(`include/v10_1_0/is-video-config.h:20-22`, `is-video-sensor.c:573-581`), then
registers minor `100 + video_id` (`is-video.c:2899-2901`,
`include/is-video.h:113`). Its open calls `is_sensor_open()`
(`is-video.c:2728-2733`). Thus opening `/dev/video101` for
`open -> VIDIOC_QUERYCAP -> close` is a physical power operation, not a passive
inventory query. This worker performed no open/ioctl/device operation.
Do not treat this host patch or its synthetic tests as a running-kernel fix;
any later device trial requires a separately reviewed candidate containing the
patch and a source-backed cleanup/identity gate.

## Inventory limits and tests

The script checks fixed V4L2 and media node number ranges `0..255` (bounded by
the Linux video/media device-number space), reads at most 64 KiB of
`/proc/modules`, at most 4 KiB per device-tree root property, and at most 512
bytes per sysfs text attribute. It reads only selected camera-related module
names; fixed DT root `model`/`compatible`; sysfs video `name`/`dev` and media
`model`/`dev`; and `lstat` metadata for device nodes. Firmware checks use fixed
basenames and `stat` only. Per-field missing, malformed, unreadable, and
truncated states are kept explicit. Kernel logs are marked `not_collected`.

Host tests use temporary fixtures and an extracted C harness only; they do not
require device nodes, private firmware, or phone access. The prior inventory
tests and this patch's additional tests are:

```sh
python3 -I -B tools/hardware/test-camera-readiness-once.py
python3 -O -I -B tools/hardware/test-camera-readiness-once.py
python3 -I -B tools/hardware/test-camera-resource-unwind.py
python3 -I -B -O tools/hardware/test-camera-resource-unwind.py
```

These tests establish only that the inventory implementation respects its
bounds and avoids opening device nodes, and that the proposed LDO helper/cleanup
branch passes host-side injected failures. They do not compile the full kernel,
prove the patch was applied to the phone, or establish camera functionality or
physical regulator state.

## Evidence required before stronger claims

1. Confirm active kernel build/config and map the running DT/DTBO selection to
   the exact r0s source overlay; verify the relevant ISP, CSI, and sensor
   drivers are bound rather than merely listed.
2. Establish the firmware/setfile provenance and full hashes for the exact
   candidate release locally, and confirm runtime paths/namespace. Do not
   publish proprietary payload bytes or use an old manifest as proof of current
   compatibility.
3. Review/build the narrow resource-unwind patch against the exact candidate
   kernel, then separately resolve the ignored runtime-PM return and failure
   cleanup. Host patch application is not deployment evidence.
4. Only after those gates, a separately reviewed, exact-candidate
   `/dev/video101` open/`VIDIOC_QUERYCAP`/close trial may establish static
   capabilities plus that operation's open/close path; it would still not
   prove sensor response, firmware validity, or capture. Do not add input,
   format, buffer, stream, control, OTP, EEPROM, or calibration operations to
   that narrow trial without separate review.

Until those steps, the accurate state is “camera nodes/prerequisite metadata
may be inventoried; sensor response and capture remain unproven.”
