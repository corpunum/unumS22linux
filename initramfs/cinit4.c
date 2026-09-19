#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <fcntl.h>
#include <unistd.h>
#include <string.h>

static void wr(const char *path, const char *msg) {
	int fd = open(path, O_WRONLY | O_CREAT | O_APPEND | O_SYNC, 0644);
	if (fd >= 0) {
		write(fd, msg, strlen(msg));
		fsync(fd);
		close(fd);
	}
}

/* Wrapper /init: do ONE trivial thing, then chain-exec the REAL, unmodified
 * AOSP init binary (kept at a fresh path: /real_init). Tests whether ANY
 * code running before the real init - even something totally benign -
 * breaks the boot, independent of what that code actually does. */
int main(void) {
	mkdir("/dev", 0755);
	mount("devtmpfs", "/dev", "devtmpfs", 0, NULL);
	mknod("/dev/kmsg", S_IFCHR | 0666, makedev(1, 11));

	wr("/dev/kmsg", "[cinit4-wrapper] about to exec real init\n");

	execl("/real_init", "/init", (char *)NULL);

	/* only reached if execl itself failed */
	wr("/dev/kmsg", "[cinit4-wrapper] execl(/real_init) FAILED\n");
	for (;;) sleep(5);
	return 0;
}
