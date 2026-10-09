/*
 * npu-bootup-once: one NPU BOOTUP on /dev/vertex10, then close (driver release
 * performs the shutdown). Owner-approved single attempt (2026-10-07). Prints
 * rc/errno of each step. Run it in a throwaway process under a timeout; a
 * kernel-side wait can leave it unkillable, which is the accepted risk.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>
struct vs4l_ctrl { uint32_t ctrl, value, mem_size, mem_addr, mem_addr_h; int32_t reserved; };
#define VS4L_VERTEXIOC_BOOTUP _IOWR('V', 13, struct vs4l_ctrl)
_Static_assert(VS4L_VERTEXIOC_BOOTUP == 0xc018560dUL, "VS4L BOOTUP ioctl ABI");
static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec / 1e9; }
int main(int argc, char **argv)
{
	const char *dev = argc > 1 ? argv[1] : "/dev/vertex10";
	struct vs4l_ctrl c = { .ctrl = 0 /* BOOT_UP | NON_SECURE */, .value = 0x2 /* NPU_HWDEV_ID_NPU */ };
	double t0 = now();
	int fd = open(dev, O_RDWR | O_CLOEXEC);
	printf("open fd=%d errno=%d\n", fd, fd < 0 ? errno : 0); fflush(stdout);
	if (fd < 0) return 1;
	int r = ioctl(fd, VS4L_VERTEXIOC_BOOTUP, &c);
	printf("bootup rc=%d errno=%d (%s) t=%.2fs\n", r, r < 0 ? errno : 0, r < 0 ? strerror(errno) : "ok", now() - t0);
	fflush(stdout);
	r = close(fd);
	printf("close rc=%d t=%.2fs\n", r, now() - t0);
	return 0;
}
