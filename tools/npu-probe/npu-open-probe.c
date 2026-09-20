/* SPDX-License-Identifier: MIT
 * Host-side safety probe: open/fstat/close only. No VS4L ioctl is issued.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>

int main(int argc, char **argv)
{

	struct stat st;
	int fd;
	if (argc != 2) {
		fprintf(stderr, "usage: %s /dev/vertex10\n", argv[0]);
		return 2;
	}
	fd = open(argv[1], O_RDONLY | O_CLOEXEC);
	if (fd < 0) {
		fprintf(stderr, "open(%s): %s\n", argv[1], strerror(errno));
		return 1;
	}
	if (fstat(fd, &st) < 0) {
		fprintf(stderr, "fstat(%s): %s\n", argv[1], strerror(errno));
		close(fd);
		return 1;
	}
	printf("opened=%s mode=%#o major=%u minor=%u size=%lld\n", argv[1],
	       (unsigned)(st.st_mode & 07777), (unsigned)major(st.st_rdev),
	       (unsigned)minor(st.st_rdev), (long long)st.st_size);
	if (close(fd) < 0) {
		fprintf(stderr, "close(%s): %s\n", argv[1], strerror(errno));
		return 1;
	}
	return 0;
}
