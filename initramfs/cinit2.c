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

int main(void) {
	mkdir("/dev", 0755);
	mkdir("/proc", 0755);
	mkdir("/sys", 0755);
	mkdir("/cache", 0755);

	mount("devtmpfs", "/dev", "devtmpfs", 0, NULL);
	mount("proc", "/proc", "proc", 0, NULL);
	mount("sysfs", "/sys", "sysfs", 0, NULL);

	/* real AOSP ramdisks ship this node; devtmpfs auto-populates /dev but
	 * only AFTER it's mounted, so if the kernel opened console before that
	 * mount, this ensures it exists as soon as we possibly can */
	mknod("/dev/console", S_IFCHR | 0622, makedev(5, 1));
	mknod("/dev/kmsg", S_IFCHR | 0666, makedev(1, 11));

	wr("/dev/kmsg", "[cinit2] PID1 alive, about to mount cache\n");
	wr("/dev/console", "[cinit2] PID1 alive, about to mount cache\n");

	int mounted = 0;
	if (mount("/dev/block/sda33", "/cache", "ext4", MS_SYNCHRONOUS, NULL) == 0) mounted = 1;
	else if (mount("/dev/block/sda33", "/cache", "f2fs", MS_SYNCHRONOUS, NULL) == 0) mounted = 1;

	if (mounted) {
		wr("/cache/cinit2_log.txt", "===== CINIT2 BOOT (fsync+console+panic-oracle) =====\n");
		wr("/cache/cinit2_log.txt", "PID1 alive after cache mount\n");
	} else {
		wr("/dev/kmsg", "[cinit2] cache mount FAILED\n");
		wr("/dev/console", "[cinit2] cache mount FAILED\n");
	}

	int n = 0;
	char buf[64];
	for (;;) {
		n++;
		int len = 0;
		buf[len++] = 'h'; buf[len++] = 'b'; buf[len++] = ' ';
		int v = n, digits = 0, tmp = v;
		char digitbuf[16];
		if (tmp == 0) { digitbuf[digits++] = '0'; }
		while (tmp > 0) { digitbuf[digits++] = '0' + (tmp % 10); tmp /= 10; }
		while (digits > 0) buf[len++] = digitbuf[--digits];
		buf[len++] = '\n';
		buf[len] = 0;
		if (mounted) wr("/cache/cinit2_log.txt", buf);
		wr("/dev/kmsg", "[cinit2] heartbeat\n");
		sleep(2);
	}
	return 0;
}
