# Exynos NPU host-only probe

This directory contains a deliberately conservative probe. It opens a supplied
device path with `O_RDONLY|O_CLOEXEC`, reports `fstat(2)`, and closes it. It does
not call `ioctl(2)`, `mmap(2)`, `read(2)`, `write(2)`, `poll(2)`, or any VS4L
session operation. Opening a character device can still invoke a driver's open
callback and may allocate a session or wake hardware; therefore even this probe
must only be run on the phone after owner review. It is not an inference test.

The current kernel tree has no checked-in NPU UAPI header or implementation
(the NPU module is supplied by the downstream phone kernel), so no version ioctl
can be safely reconstructed from this checkout. In particular, do not guess
`VS4L_VERTEXIOC_VERSION`: Samsung's VS4L command set and structure layout vary
by vendor kernel generation. `S_GRAPH`, `S_FORMAT`, `BOOTUP`, queue, stream, and
firmware operations are explicitly out of scope; several are state-changing and
the public security research documents their attack surface.

Build on a native Linux host with `cc -Wall -Wextra -O2 -o npu-open-probe
npu-open-probe.c`. Run only as `./npu-open-probe /dev/vertex10` after a review.
