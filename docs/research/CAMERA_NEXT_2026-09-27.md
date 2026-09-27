# S22 camera inventory and next evidence — 2026-09-27

## Current conclusion

The owner's September 27 continuation authorizes camera bring-up. The gates
below are source, artifact, and operation-safety prerequisites, not a renewed
request for broad camera permission. The worker performed host work only;
the coordinator alone may execute a reviewed device operation.

Camera readiness and capture remain **not assessed**. This continuation adds a
source patch for the previously identified failed-open runtime-PM accounting
path and an extracted-C host regression; it does not alter the earlier
inventory behavior. [`camera-readiness-once.py`](../../tools/hardware/camera-readiness-once.py)
is a bounded, host-testable inventory intended for later use through the
existing pinned transport. It reads fixed `/proc`, `/sys`, and device-tree
attributes, stats fixed `/dev/videoN` and `/dev/mediaN` paths without opening
them, and stats a fixed firmware filename list in four candidate roots. It does
not query kernel logs, issue ioctls, request camera power, or access sensor ID,
OTP, EEPROM, or calibration attributes. Missing files or nodes in one namespace
are not a camera failure and do not establish their absence in another
namespace.

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
power-off guarantee.

The follow-up [`camera-runtime-pm-unwind.patch`](../../tools/hardware/camera-runtime-pm-unwind.patch)
is ordered after `camera-resource-unwind.patch`. In the pinned baseline,
`is_resource_get()` ignores the `pm_runtime_get_sync()` return and then sets
the sensor power bit; the later `p_err` label increments resource/core counts.
The pinned PM header specifies that `pm_runtime_get_sync()` keeps its usage
reference even on error, while `pm_runtime_resume_and_get()` drops that
reference on a negative resume result (`include/linux/pm_runtime.h:386-420`).
The new helper uses the latter when CONFIG_PM is enabled, checks and
normalizes the direct callback result otherwise, and sets the sensor power bit
only on success. A failed resume returns via `rsc_err` before the count label.
For a first core acquire it best-effort disables the LDO votes acquired by that
attempt in reverse order, deinitializes dynamic memory, clears resource state,
and releases the wake reference. If other resources are already active, it
leaves shared LDO, memory, and wake state untouched. A cleanup error is logged
and the original resume error is returned; incomplete LDO cleanup is explicitly
hardware-state-unknown.

The patch is only a host candidate, not a complete physical resume-failure
unwind proof. In the r0s v10.1 source path, the runtime-resume pre-hook returns
zero and the explicit generic error checks for a missing sensor device or CSI
subdevice precede ICLK setup (`is-device-sensor_v2.c:4004-4041`,
`is-hw-pwr.c:24-28`). However, the r0s clock-operation wrappers discard the
return from `exynos9925_is_sensor_iclk_cfg/on()` and return zero
(`setup-is-sensor.c:184-198`); the underlying clock/gate operations therefore
remain unverified by the runtime-PM result. No claim is made that they succeeded
or were physically rolled back. The patch handles the PM reference, software
resource state, and first-acquire regulator votes; physical clock/regulator
state still requires separate evidence.

`VIDIOC_QUERYCAP` itself only fills static capability fields in
`is_vidioc_querycap()` (`is-video.c:2914-2934`), but reaching it requires an
open. The source maps the first sensor leader to video id 1
(`include/v10_1_0/is-video-config.h:20-22`, `is-video-sensor.c:573-581`), then
requests video node number `100 + video_id` (`is-video.c:2899-2901`,
`include/is-video.h:113`). Do not read that suffix as the live character-device
minor: a separate passive coordinator inventory observed `/dev/video101` as
character device `81:17`, with driver `exynos-is-sensor` and module `fimc_is`.
Its open calls `is_sensor_open()`
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
python3 -I -B tools/hardware/test-camera-runtime-pm-unwind.py
python3 -I -B -O tools/hardware/test-camera-runtime-pm-unwind.py
```

These tests establish only that the inventory implementation respects its
bounds and avoids opening device nodes, and that the proposed LDO helper/cleanup
branch and runtime-PM helpers pass host-side injected failures. The PM test
uses the GPL-2.0 [function-body fixture](../../tools/hardware/camera-pm-runtime-resume-and-get.inc)
extracted verbatim from the pinned `pm_runtime_resume_and_get()` implementation,
so its CONFIG_PM and direct-callback C failure injection runs even without the
private vendor checkout. When that checkout is available, the test byte-compares
the fixture to the pinned header and checks both ordered patches in a temporary
source copy; those provenance/apply checks explicitly skip if the checkout is
absent.
It injects negative and positive resume results, shared/first-resource state,
reverse LDO cleanup failures, and dynamic-memory cleanup failure. These checks
do not compile the full kernel, prove either patch was applied to the phone, or
establish camera functionality or physical regulator/clock state.

## Evidence required before stronger claims

1. The passive coordinator inventory identified the loaded camera module and
   reported boot properties `androidboot.revision=28` and
   `androidboot.dtbo_idx=7`. The pinned r0s Makefile has eight
   `CONFIG_CAMERA_RSV_V01` overlays ending in r27, so zero-based list-order
   inference points to r27; the selected recovery DTBO has not been
   byte-verified against that source. Verify ISP/CSI/sensor bindings rather
   than treating a source overlay or module listing as proof.
2. Establish the firmware/setfile provenance and full hashes for the exact
   candidate release locally, and confirm runtime paths/namespace. Do not
   publish proprietary payload bytes or use an old manifest as proof of current
   compatibility.
3. Both ordered resource-unwind patches have been applied to the pinned kernel
   source and independently reviewed as host candidates. The module-only build
   and compatibility receipt is
   [`camera-module-build-2026-09-27.json`](camera-module-build-2026-09-27.json).
   The unstripped module and a separately validated debug-only-stripped copy
   are not packaged or deployed. The host tests and build are not deployment
   evidence, and clock-provider side effects remain unverified.
4. Only after those gates, a separately reviewed, exact-candidate
   `/dev/video101` open/`VIDIOC_QUERYCAP`/close trial may establish static
   capabilities plus that operation's open/close path; it would still not
   prove sensor response, firmware validity, or capture. Do not add input,
   format, buffer, stream, control, OTP, EEPROM, or calibration operations to
   that narrow trial without separate review.

Until those steps, the accurate state is “camera nodes/prerequisite metadata
may be inventoried; sensor response and capture remain unproven.”

## Host module build

The target artifact is `fimc-is.ko` (internal name `fimc_is`). It was built as
a single ARM64 module in an isolated, patch-pinned source/output pair using the
unchanged HCI config and matching vmlinux/module symbol inputs. The build did
not rebuild `Image`, modify the original source or O-tree, or access the phone.
The first output had no `__versions` records and is explicitly rejected; the
corrected modpost input produced 378 validated imports, the expected
`module_layout` CRC, and the same 75 exports as the baseline module. This is
module metadata compatibility evidence, not a live-load or camera-function
claim.

A separate copy was processed with the hash-pinned Ubuntu LLVM 18.1.3
`llvm-strip --strip-debug` tool (`f52b9997…c4d23e34`). The original unstripped
artifact remains intact. The stripped file is 7,164,248 bytes, SHA-256
`256926d8…44b35ecf`, GNU build ID `59e54c03…6d69aadf64`; its 378 imported
version records, import set, exports, aliases, and direct dependencies match
the validated unstripped candidate/baseline. This is still a host module only.

The retained HCI image is pinned separately as
`42da267f…d9c49be5` (RECOVERY partition size 100,663,296 bytes). Its compressed
ramdisk SHA-256 is `0dd9dda6…63841e5d`, matching the previously audited
audio-extras ramdisk. Any packager must nevertheless unpack and verify this
exact HCI image as its base. The original `lib/modules/fimc-is.ko` CPIO record
is 8,239,848 bytes, SHA-256 `ba492fcc…1d085d3`, mode `0100644`, uid/gid `0:0`;
`modules.dep`, `modules.alias`, and `modules.softdep` must remain byte-identical
because the candidate's module name, aliases, and dependencies are unchanged.

The host-only packager under review is
[`build-camera-module-recovery.py`](../../tools/hardware/build-camera-module-recovery.py).
It is designed to replace only `lib/modules/fimc-is.ko`, preserve every other
raw CPIO record and all unmodified boot payloads/header fields, and emit a
separate manifest. It has not been run on the real recovery image pending
independent code review. Its synthetic test command is:

```sh
python3 -I -B tools/hardware/test-build-camera-module-recovery.py
python3 -O -I -B tools/hardware/test-build-camera-module-recovery.py
```

The manifest's canonical set digests use these byte definitions: import set,
`modprobe --dump-modversions` sorted under `LC_ALL=C sort -k2`; aliases,
`modinfo -F alias` sorted under `LC_ALL=C sort`; direct dependencies,
`modinfo -F depends` split on commas and sorted under `LC_ALL=C sort`. Each
line is newline-terminated before hashing. No package from this work has been
installed, booted, or tested on hardware.

The existing recovery CPIO entry is `lib/modules/fimc-is.ko`, mode 0100644,
root:root. Its 8,239,848-byte payload has no `.debug_*` sections but retains
`.symtab`, `.strtab`, and `__versions`; it has no module signature metadata.
The new 56,384,552-byte host output retains debug sections. A separate
7,164,248-byte debug-only-stripped copy has the same build ID and validated
imports/exports/aliases/direct dependencies, and has no module signature
metadata. Neither module copy is packaged or deployed yet. None of this
implies that a module can be safely replaced on a live system.
