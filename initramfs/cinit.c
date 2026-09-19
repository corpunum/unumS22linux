#include <sys/mount.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <string.h>

static void wr(const char *path, const char *msg) {
	int fd = open(path, O_WRONLY | O_CREAT | O_APPEND, 0644);
	if (fd >= 0) {
		write(fd, msg, strlen(msg));
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

	wr("/dev/kmsg", "[cinit] PID1 alive, about to mount cache\n");

	int mounted = 0;
	if (mount("/dev/block/sda33", "/cache", "ext4", 0, NULL) == 0) mounted = 1;
	else if (mount("/dev/block/sda33", "/cache", "f2fs", 0, NULL) == 0) mounted = 1;

	if (mounted) {
		wr("/cache/cinit_log.txt", "===== CINIT BOOT =====\n");
		wr("/cache/cinit_log.txt", "PID1 alive after cache mount\n");
	} else {
		wr("/dev/kmsg", "[cinit] cache mount FAILED\n");
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
		if (mounted) wr("/cache/cinit_log.txt", buf);
		wr("/dev/kmsg", "[cinit] heartbeat\n");
		sleep(5);
	}
	return 0;
}
