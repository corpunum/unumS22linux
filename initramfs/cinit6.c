#define _GNU_SOURCE
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/wait.h>
#include <sys/reboot.h>
#include <sys/ioctl.h>
#include <linux/watchdog.h>
#include <linux/reboot.h>
#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <errno.h>
#include <time.h>
#include <stdio.h>

/* V17 - incorporates every fix from the Codex/Opus reviews of V15:
 *  - real dependency-ordered module loading via busybox modprobe (not
 *    hand-rolled finit_module calls that ignored transitive deps)
 *  - includes ufs-exynos-core, the storage controller V15 never loaded at
 *    all, so cache could never have actually been backed by real hardware
 *  - single watchdog owner (direct open+ioctl+write only, no double-open
 *    conflict with a forked watchdogd)
 *  - kmsg opened without O_CREAT (fails loudly instead of silently writing
 *    to a fake regular file if mknod somehow didn't happen)
 *  - console fd wired to stdin/stdout/stderr
 *  - monotonic clock for the timing oracle, not loop-iteration counting
 *  - waits for the cache partition to actually appear in /proc/partitions
 *    (UFS probe takes real time) before attempting to mount it, bounded
 *  - early log lines buffered in memory, flushed to cache once it's up
 */

#define EARLY_LOG_MAX 64
#define EARLY_LOG_LINE 128
static char early_log[EARLY_LOG_MAX][EARLY_LOG_LINE];
static int early_log_n = 0;
static int g_cache_ok = 0;
static int g_kmsg_fd = -1;

static double now_mono(void) {
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec + ts.tv_nsec / 1e9;
}

static void log_line(const char *msg) {
	if (g_kmsg_fd >= 0) {
		write(g_kmsg_fd, msg, strlen(msg));
	}
	if (g_cache_ok) {
		int fd = open("/cache/v17_log.txt", O_WRONLY | O_APPEND, 0644);
		if (fd >= 0) {
			write(fd, msg, strlen(msg));
			fsync(fd);
			close(fd);
		}
	} else if (early_log_n < EARLY_LOG_MAX) {
		strncpy(early_log[early_log_n], msg, EARLY_LOG_LINE - 1);
		early_log[early_log_n][EARLY_LOG_LINE - 1] = 0;
		early_log_n++;
	}
}

static void flush_early_log(void) {
	if (!g_cache_ok) return;
	int fd = open("/cache/v17_log.txt", O_WRONLY | O_CREAT | O_APPEND, 0644);
	if (fd < 0) return;
	for (int i = 0; i < early_log_n; i++) {
		write(fd, early_log[i], strlen(early_log[i]));
	}
	fsync(fd);
	close(fd);
}

static void logf_uptime(const char *prefix) {
	char buf[160];
	double t = now_mono();
	int sec = (int)t;
	int frac = (int)((t - sec) * 1000);
	snprintf(buf, sizeof(buf), "[v17 t=%d.%03ds] %s\n", sec, frac, prefix);
	log_line(buf);
}

/* Run busybox modprobe as a real child process so dependency resolution
 * uses the actual, tested logic in modprobe-small.c against modules.dep -
 * not a hand-rolled load order that (as verified) leaves most transitive
 * dependencies unresolved. */
static void modprobe(const char *name) {
	char msg[128];
	snprintf(msg, sizeof(msg), "modprobe %s: starting\n", name);
	logf_uptime(msg);

	pid_t pid = fork();
	if (pid == 0) {
		int devnull = open("/dev/null", O_WRONLY);
		if (devnull >= 0) { dup2(devnull, 1); dup2(devnull, 2); }
		execl("/busybox", "/busybox", "modprobe", name, (char *)NULL);
		_exit(127);
	}
	int status = 0;
	waitpid(pid, &status, 0);
	snprintf(msg, sizeof(msg), "modprobe %s: exit=%d\n", name,
	         WIFEXITED(status) ? WEXITSTATUS(status) : -1);
	logf_uptime(msg);
}

/* Poll /proc/partitions for a partition name to appear - UFS probe takes
 * real wall-clock time after the controller module loads. */
static int wait_for_partition(const char *name, int max_seconds) {
	for (int i = 0; i < max_seconds * 5; i++) {
		int fd = open("/proc/partitions", O_RDONLY);
		if (fd >= 0) {
			char buf[8192];
			int n = read(fd, buf, sizeof(buf) - 1);
			close(fd);
			if (n > 0) {
				buf[n] = 0;
				if (strstr(buf, name)) return 1;
			}
		}
		usleep(200000);
	}
	return 0;
}

int main(void) {
	mkdir("/dev", 0755);
	mount("tmpfs", "/dev", "tmpfs", 0, NULL);
	mkdir("/dev/block", 0755);
	mkdir("/proc", 0755);
	mkdir("/sys", 0755);
	mkdir("/cache", 0755);

	mknod("/dev/kmsg", S_IFCHR | 0666, makedev(1, 11));
	mknod("/dev/null", S_IFCHR | 0666, makedev(1, 3));
	mknod("/dev/console", S_IFCHR | 0622, makedev(5, 1));
	mknod("/dev/watchdog", S_IFCHR | 0644, makedev(10, 130));
	mknod("/dev/block/sda33", S_IFBLK | 0660, makedev(259, 17));

	mount("proc", "/proc", "proc", 0, NULL);
	mount("sysfs", "/sys", "sysfs", 0, NULL);

	/* kmsg WITHOUT O_CREAT - if mknod somehow failed, fail loudly (fd stays
	 * -1, log_line just skips kernel-log writes) instead of silently
	 * writing to a fake regular file named "kmsg" like V10/V15 could. */
	g_kmsg_fd = open("/dev/kmsg", O_WRONLY);

	/* Wire up the console for actual stdin/stdout/stderr instead of just
	 * creating the node and leaving fds 0-2 unattached. */
	int con = open("/dev/console", O_RDWR);
	if (con >= 0) {
		dup2(con, 0); dup2(con, 1); dup2(con, 2);
		if (con > 2) close(con);
	}

	logf_uptime("PID1 alive, tmpfs+mknod done, console wired");

	/* Load exynos-pmu-if + dss first (dss depends on it) so /proc/last_kmsg
	 * has the best chance of capturing everything from here on, same intent
	 * as V15 but now via real dependency resolution instead of assuming the
	 * two-module chain is complete. */
	modprobe("dss");

	/* Load the storage controller BEFORE attempting to mount anything - V15
	 * never loaded this at all, so its cache mount could never have worked
	 * regardless of the block device node being correct. */
	modprobe("ufs-exynos-core");

	if (wait_for_partition("sda33", 15)) {
		logf_uptime("sda33 appeared in /proc/partitions");
		if (mount("/dev/block/sda33", "/cache", "ext4", MS_SYNCHRONOUS, NULL) == 0) {
			g_cache_ok = 1;
			flush_early_log();
			logf_uptime("cache mounted OK, early log flushed");
		} else {
			logf_uptime("cache mount FAILED (see kmsg errno)");
		}
	} else {
		logf_uptime("sda33 never appeared after 15s wait - UFS probe likely failed");
	}

	/* Watchdog + USB chain, single owner for the watchdog device. */
	modprobe("s3c2410_wdt");
	modprobe("phy-exynos-usbdrd-super");
	modprobe("dwc3-exynos-usb");
	modprobe("usb_typec_manager");
	modprobe("usb_notify_layer");
	modprobe("usb_notifier");
	modprobe("usb_f_conn_gadget");

	logf_uptime("module load pass complete");

	int wdfd = open("/dev/watchdog", O_WRONLY | O_CLOEXEC);
	if (wdfd >= 0) {
		int timeout = 0;
		if (ioctl(wdfd, WDIOC_GETTIMEOUT, &timeout) == 0) {
			char m[64];
			snprintf(m, sizeof(m), "watchdog timeout=%ds\n", timeout);
			logf_uptime(m);
		}
		logf_uptime("opened /dev/watchdog (sole owner, direct petting)");
	} else {
		logf_uptime("open /dev/watchdog FAILED");
	}

	/* Decisive timing oracle: self-trigger a clean reboot at a real
	 * monotonic T=45s, not 45 loop iterations. If the observed physical
	 * reset timing matches ~45s (plus the module-loading time already
	 * logged above), PID1 was alive and healthy that whole time. */
	double start = now_mono();
	while (now_mono() - start < 45.0) {
		if (wdfd >= 0) {
			int r = write(wdfd, "1", 1);
			(void)r;
		}
		logf_uptime("heartbeat");
		sleep(1);
	}

	logf_uptime("reached T=45s alive - self-rebooting now as the timing oracle");
	sync();
	int rc = reboot(LINUX_REBOOT_CMD_RESTART);
	if (rc != 0) logf_uptime("reboot() syscall FAILED");

	for (;;) sleep(5);
	return 0;
}
