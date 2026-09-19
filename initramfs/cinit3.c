#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/wait.h>
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

	mknod("/dev/console", S_IFCHR | 0622, makedev(5, 1));
	mknod("/dev/kmsg", S_IFCHR | 0666, makedev(1, 11));

	wr("/dev/kmsg", "[cinit3] PID1 alive, starting watchdogd first\n");

	/* Start the REAL AOSP watchdogd binary exactly as recovery's own init.rc
	 * does: "service watchdogd /system/bin/watchdogd 10 20" - 10s petting
	 * interval, 20s margin, 30s total hardware timeout. Confirmed present as
	 * a dynamically-linked ELF at /system/bin/watchdogd in this same ramdisk. */
	pid_t wd = fork();
	if (wd == 0) {
		execl("/system/bin/watchdogd", "watchdogd", "10", "20", (char *)NULL);
		wr("/dev/kmsg", "[cinit3] execl watchdogd FAILED\n");
		_exit(127);
	}
	wr("/dev/kmsg", "[cinit3] forked watchdogd\n");

	int mounted = 0;
	if (mount("/dev/block/sda33", "/cache", "ext4", MS_SYNCHRONOUS, NULL) == 0) mounted = 1;
	else if (mount("/dev/block/sda33", "/cache", "f2fs", MS_SYNCHRONOUS, NULL) == 0) mounted = 1;

	if (mounted) {
		wr("/cache/cinit3_log.txt", "===== CINIT3 BOOT (watchdogd started first) =====\n");
	} else {
		wr("/dev/kmsg", "[cinit3] cache mount FAILED\n");
	}

	/* reap the watchdogd child status if it already died, non-blocking, so we
	 * can log whether it's actually alive */
	int status = 0;
	pid_t r = waitpid(wd, &status, WNOHANG);
	if (r == 0) {
		wr("/cache/cinit3_log.txt", "watchdogd still running after cache mount\n");
	} else {
		wr("/cache/cinit3_log.txt", "watchdogd exited already (bad sign)\n");
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
		if (mounted) wr("/cache/cinit3_log.txt", buf);
		wr("/dev/kmsg", "[cinit3] heartbeat\n");
		sleep(2);
	}
	return 0;
}
