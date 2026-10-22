# Camera

Part of the [porting guide](../PORTING.md). Rear and front cameras take photos on the S22 through
our own capture client and our own software image processing, with no Android camera stack, no ISP
library and no TrustZone involvement. Video is under 1 frame/s
([README](../../README.md#what-works-and-what-doesnt-yet)).

## Avoiding the hard part: the ISP

Samsung's image signal processor path loads a proprietary library that TrustZone verifies. We
didn't go there. The first finding was that the **sensor itself does not need it**: the kernel's own
Pablo self-test (`/sys/module/fimc_is/parameters/test_sensor_run`) powered the rear sensor and
received 83 frames over MIPI CSI, then powered it down cleanly. The TrustZone dependency applies
only to the ISP library ([evidence/camera-20261006](../../evidence/camera-20261006/README.md)).

The self-test never gives the sensor a buffer, so no pixels reached memory. The next step was our
own V4L2 client that writes raw Bayer frames into memory through the CSI write DMA
([CAMERA.md](../../tools/hardware/camera/CAMERA.md)).

## Writing the capture client from the kernel source

Everything about the client came from reading the camera driver source and checking it on the phone
([CAMERA.md](../../tools/hardware/camera/CAMERA.md#pipeline-read-from-the-kernel-source-fimc_is-build-59e54c03)):

- which `/dev/video*` nodes are the sensor group leader and the CSI capture DMA (and which aren't:
  some "obvious" nodes were ISP statistics);
- the exact ioctl order, formats and the metadata "shot" structure with its magic number;
- that buffers must be DMABUF from `/dev/dma_heap/system`, because MMAP cannot work with this driver;
- that the capture DMA must start before the leader.

Struct sizes and offsets were checked against a probe compiled with the real kernel build flags and
the DWARF debug info of the module, and are pinned by host tests
([CAMERA.md](../../tools/hardware/camera/CAMERA.md), `test_s22_camera.py`).

An independent review before the first run changed several things for safety: a blocking stream
start (the non-blocking one could let the DMA write into freed buffers, and a memory-management
fault in the camera block panics the kernel), guard pages around buffers, and geometry locked to one
known mode until a capture was proven
([CAMERA.md](../../tools/hardware/camera/CAMERA.md#independent-review-2026-10-08-go-with-changes-applied)).

One more piece from the vendor partition: the OIS controller asks for `is_mcu_fw.bin`; without it,
each start waits 60 s in the firmware fallback ([evidence/camera-20261006](../../evidence/camera-20261006/README.md)).

## A software ISP

Raw frames are dark, mosaicked and colour-cast. `s22-develop` is a static Rust program that does
what the ISP would: black level, defect pixels, lens shading, white balance, demosaic, a colour
matrix, denoise, exposure and tone, plus averaging a 4-frame burst. It develops a full-resolution
frame in about 0.35 s on the phone
([CAMERA.md](../../tools/hardware/camera/CAMERA.md#software-isp-s22-develop-2026-10-22),
[evidence/camera-develop-20261022](../../evidence/camera-develop-20261022/README.md)).

The colour matrix came from the stock sensor **setfiles** in the vendor partition: a scan for
3×3 fixed-point matrices whose rows sum to 1.0 found four per-illuminant matrices and one generic
saturation matrix, at the same offsets in both sensors' files. Which illuminant each belongs to is not decoded, and colour accuracy was not
measured.

## For your phone

- Look for a kernel self-test or debug sysfs for the camera driver; it can prove the sensor works
  before you write any client.
- Find out whether your sensor path depends on the ISP firmware/library or can deliver raw frames
  on its own.
- Read the driver source for buffer types and the order of operations; verify structure layouts
  against the real module, not against headers from a different kernel.
- Expect to write a small software ISP; start from black level, white balance and demosaic.
