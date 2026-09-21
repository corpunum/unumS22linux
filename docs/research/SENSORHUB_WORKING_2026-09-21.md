# Sensor hub and real accelerometer/gyro samples

Target: Galaxy S22 SM-S901B/DS, Exynos 2200. On 2026-09-21 the existing
Samsung sensor hub was started without Android services, a reboot or a
partition write. LSM6DSO accelerometer and gyroscope delivered real samples
through Linux IIO in two independent-process trials each.

## The actual missing pieces

The live driver is `drivers/staging/nanohub`, not the newer
`drivers/sensorhub` implementation also present in the same kernel tree.
These have different enable interfaces. The live driver uses a **bitmask**;
the newer tree's `type * 10 + enable` encoding must not be used here.

Before startup, `ssp_sensor/fs_ready=0`, current MCU revision was zero,
sensor-state text was empty, sensor values were zero, and no sensor client
was enabled. The exact DT firmware name is
`sensorhub/shub_pamir_rainbow.bin`; multi-OS is disabled.

The firmware path is `/vendor/firmware` in the guardian/init root. SSH runs
inside a different root: checking its `/vendor` alone gives the wrong answer.
The relevant host-visible path is `/proc/1/root/vendor/firmware`. Wi-Fi and
touch firmware were present there, but this sensor-hub file was absent.

The firmware was recovered from the existing read-only vendor image using
`tools/hardware/recover-f2fs-file.py`, inode `0x46e`, without reading a stock
archive or altering the image:

- Source image SHA-256: `6dfe677119792e95a37b016092c327ad62bc0dc7759851424ad05e6bcb31b206`.
- Firmware: 929,560 bytes; SHA-256 `eb8396f5b3cc904638e68c5b72c20768e3b8852bc0adcbc663e51942bea86679`.
- Declared SRAM size: `0x180000`; the file fits.

`sensorhub-once.py --start` copied only that exact new file into a private
userdata directory and the guardian's RAM firmware directory, verified both
hashes, then wrote `fs_ready` once. The kernel's regular path loaded it,
reported successful S2MPU firmware verification, and reached CHUB run state 2.
Current firmware revision became `24122600`; the sensor inventory populated.
The compiled driver's reference version is `25062600`, so versions are not
claimed equal. Actual authenticated boot and samples are the evidence.

## Measured data path

The sample helper discovers IIO nodes by exact name and validates their
character-device major/minor. Data injection was off. It enables the IIO
buffer, selects one sensor, reads nonblocking frames for at most five seconds,
then restores the sensor-enable mask and buffer flag to their original zero
values. The firmware remains running; the helper does not reset or power off
the hub.

| Sensor | Enable bit | Frame ABI | Trial 1 / trial 2 |
| --- | --- | --- | --- |
| LSM6DSO accelerometer | 0 | 3 little-endian signed 16-bit axes + u64 timestamp; 14 bytes | 25 / 25 samples; 25 / 25 distinct vectors |
| LSM6DSO gyroscope | 1 | 3 little-endian signed 32-bit axes + u64 timestamp; 20 bytes | 25 / 25 samples; 9 / 6 distinct vectors |

All four trials had positive, strictly increasing timestamps, zero partial
frames, restored controls, the same boot, healthy resident4B, and battery
temperature 30.3–30.4 C. Accelerometer raw Z values were approximately 4043–4065
while the phone was stationary. Units, scaling, orientation and calibrated
accuracy were not accepted from that observation.

The exact tested sample-helper SHA-256 was
`41b14bc7ea95089875c390793a34a5945119ddf4874dac0989d6bf6a6c99b9de`.
Its later source comment was corrected to describe the implicit calibration
behavior below; runtime logic was unchanged. Private per-trial snapshots and
full kernel logs remain under `rootfs/hardware-reuse-20260921/`.

## Calibration and remaining limits

First accelerometer enable calls the kernel's `accel_open_calibration()` and
`set_accel_cal()`. The former requests saved calibration through the Android
file-manager IIO channel. With no client, the request timed out; the kernel
then sent zero runtime offsets to the hub. This was observed in the log.
Therefore the original helper comment “no calibration writes” was too broad:
there were no calibration sysfs/file or EFS writes, but there was a normal
runtime calibration command to the hub. Restoring sampling controls does not
undo that internal command. Factory calibration and accuracy remain unproven.

Startup also logged failures setting dynamic-calibration/factory-binary flags;
they did not prevent these raw sample trials and have not been resolved.
No new CHUB reset, kernel panic or device-fault line was observed in the
bounded sampling deltas. The calibration-read timeout is not hidden by that
statement.

This is **raw sensor access**, not completed desktop integration. No physical
movement/orientation reference test, auto-rotation, ambient-light control,
proximity behavior, other sensor acceptance, suspend, cold-start repetition or
sensor autostart is claimed. The recovered firmware persists in userdata,
but guardian RAM staging and hub startup have not been added to boot policy.

## Reproduction and evidence

- Host startup: `python3 tools/hardware/sensorhub-once.py` is read-only by
  default; `--start` refuses an already-started baseline or existing targets.
- Bounded sample: `python3 tools/hardware/run-sensor-sample.py accel UNIQUE`
  or `gyro UNIQUE`; never reuse an evidence directory.
- Five decoder tests: `python3 -m unittest tools/hardware/test_sensorhub_sample_once.py`.
- [Curated receipts](../../evidence/hardware-reuse-20260921/sensors.json).
- Pinned kernel source:
  [sensor ABI](https://github.com/LineageOS/android_kernel_samsung_s5e9925/blob/4e5c5ad7d950e4de0688b5663965f2075654b2ad/drivers/staging/nanohub/sensor_list.h),
  [enable path](https://github.com/LineageOS/android_kernel_samsung_s5e9925/blob/4e5c5ad7d950e4de0688b5663965f2075654b2ad/drivers/staging/nanohub/ssp_sysfs.c),
  [firmware boot](https://github.com/LineageOS/android_kernel_samsung_s5e9925/blob/4e5c5ad7d950e4de0688b5663965f2075654b2ad/drivers/staging/nanohub/chub_bootup.c).
