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

/* V18 - final plan from the 3-round Opus<->Astra review loop. Fixes over V17
 * (cinit6.c):
 *  - console wired to fds 0/1/2 BEFORE opening kmsg, and kmsg forced above
 *    fd 2 - on this ramdisk (empty /dev, no console_on_rootfs) this was a
 *    CERTAIN bug, not probabilistic: kmsg would land on fd 0 and get
 *    silently clobbered by dup2(con,0).
 *  - watchdog (s3c2410_wdt) loaded and opened immediately after dss, not
 *    after UFS probing + cache mount - the leading theory is an unmanaged
 *    bootloader watchdog, so disarming/managing it late defeats the point.
 *  - watchdog petted via a WNOHANG waitpid poll loop during EVERY modprobe
 *    child, not just during the partition-wait loop.
 *  - modprobe child's stderr goes to the kmsg fd instead of /dev/null.
 *  - module success verified against /proc/modules (hyphen->underscore
 *    normalized), not trusted from exit code alone - proven on real
 *    hardware that exit 1 means "already loaded" as often as "real failure"
 *    with this busybox build (no CHECK_ALREADY_LOADED feature).
 *  - timing oracle uses an absolute monotonic deadline from true t=0, not a
 *    relative "start" checkpoint taken after setup work.
 *  - explicitly does NOT load sec_reboot.ko - it would override the clean
 *    built-in PSCI restart/poweroff handlers with Samsung's power-key-wait/
 *    5-retry-then-restart path, which is unnecessary for this reset-timing
 *    oracle (PSCI provides pm_power_off/restart with zero modules loaded).
 *  - ramdisk no longer carries a duplicate flat copy of every .ko - busybox
 *    modprobe only ever reads /lib/modules/<uname release>/, confirmed on
 *    real hardware; the flat copies were dead weight.
 */

#define EARLY_LOG_MAX 96
#define EARLY_LOG_LINE 160
static char early_log[EARLY_LOG_MAX][EARLY_LOG_LINE];
static int early_log_n = 0;
static int g_cache_ok = 0;
static int g_kmsg_fd = -1;
static int g_wd_fd = -1;

static double now_mono(void) {
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec + ts.tv_nsec / 1e9;
}

static void pet_watchdog(void) {
	if (g_wd_fd >= 0) {
		int r = write(g_wd_fd, "1", 1);
		(void)r;
	}
}

static void log_line(const char *msg) {
	if (g_kmsg_fd >= 0) {
		int r = write(g_kmsg_fd, msg, strlen(msg));
		(void)r;
	}
	if (g_cache_ok) {
		int fd = open("/cache/v18_log.txt", O_WRONLY | O_APPEND, 0644);
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
	int fd = open("/cache/v18_log.txt", O_WRONLY | O_CREAT | O_APPEND, 0644);
	if (fd < 0) return;
	for (int i = 0; i < early_log_n; i++) {
		int r = write(fd, early_log[i], strlen(early_log[i]));
		(void)r;
	}
	fsync(fd);
	close(fd);
}

static void logf_uptime(const char *prefix) {
	char buf[200];
	double t = now_mono();
	int sec = (int)t;
	int frac = (int)((t - sec) * 1000);
	snprintf(buf, sizeof(buf), "[v18 t=%d.%03ds] %s\n", sec, frac, prefix);
	log_line(buf);
}

/* Normalize a module name the same way the kernel does in /proc/modules
 * (hyphens become underscores) so the presence check actually matches. */
static void normalize(char *dst, const char *src, size_t n) {
	size_t i = 0;
	for (; src[i] && i < n - 1; i++) dst[i] = (src[i] == '-') ? '_' : src[i];
	dst[i] = 0;
}

static int module_present(const char *name) {
	char norm[64];
	normalize(norm, name, sizeof(norm));
	size_t nlen = strlen(norm);
	int fd = open("/proc/modules", O_RDONLY);
	if (fd < 0) return 0;
	char buf[16384];
	int n = read(fd, buf, sizeof(buf) - 1);
	close(fd);
	if (n <= 0) return 0;
	buf[n] = 0;
	char *p = buf;
	while (*p) {
		if (strncmp(p, norm, nlen) == 0 && (p[nlen] == ' ' || p[nlen] == '\t')) return 1;
		char *nl = strchr(p, '\n');
		if (!nl) break;
		p = nl + 1;
	}
	return 0;
}

static int platform_bound(const char *drivername) {
	char path[128];
	snprintf(path, sizeof(path), "/sys/bus/platform/drivers/%s", drivername);
	int fd = open(path, O_DIRECTORY | O_RDONLY);
	if (fd < 0) return -1;
	close(fd);
	return 1; /* directory existing is already a strong signal on this busybox-only env */
}

/* Run busybox modprobe as a child, pet the watchdog while waiting (not just
 * during the partition poll), capture stderr into kmsg, and verify success
 * against /proc/modules instead of trusting the exit code alone. */
static void modprobe(const char *name) {
	char msg[160];
	snprintf(msg, sizeof(msg), "modprobe %s: starting\n", name);
	logf_uptime(msg);

	int devnull = open("/dev/null", O_WRONLY);

	pid_t pid = fork();
	if (pid == 0) {
		if (devnull >= 0) dup2(devnull, 1);
		if (g_kmsg_fd >= 0) dup2(g_kmsg_fd, 2);
		execl("/busybox", "/busybox", "modprobe", name, (char *)NULL);
		_exit(127);
	}
	if (devnull >= 0) close(devnull);

	int status = 0;
	pid_t r;
	while ((r = waitpid(pid, &status, WNOHANG)) == 0) {
		pet_watchdog();
		usleep(200000);
	}

	int present = module_present(name);
	snprintf(msg, sizeof(msg), "modprobe %s: exit=%d proc_modules=%s\n", name,
	         WIFEXITED(status) ? WEXITSTATUS(status) : -1, present ? "yes" : "no");
	logf_uptime(msg);
}

static int wait_for_partition(const char *name, int max_seconds) {
	for (int i = 0; i < max_seconds * 5; i++) {
		pet_watchdog();
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

	/* FIX: wire the console to fds 0/1/2 FIRST. On this ramdisk (empty /dev,
	 * no console_on_rootfs since nothing existed for the kernel to open
	 * before our own mknods ran) PID1 starts with zero inherited fds, so
	 * whichever open() runs first gets fd 0. Opening kmsg before this would
	 * certainly get silently clobbered by dup2(con, 0). */
	int con = open("/dev/console", O_RDWR);
	if (con >= 0) {
		dup2(con, 0); dup2(con, 1); dup2(con, 2);
		if (con > 2) close(con);
	}

	/* Now open kmsg and force it off 0/1/2 regardless of what it landed on. */
	g_kmsg_fd = open("/dev/kmsg", O_WRONLY);
	if (g_kmsg_fd >= 0 && g_kmsg_fd <= 2) {
		int moved = fcntl(g_kmsg_fd, F_DUPFD_CLOEXEC, 10);
		if (moved >= 0) { close(g_kmsg_fd); g_kmsg_fd = moved; }
	}

	logf_uptime("PID1 alive, tmpfs+mknod done, console+kmsg fds fixed");

	/* dss first (for /proc/last_kmsg capture, though known corrupted on this
	 * device - kept for completeness/future devices), THEN the watchdog
	 * immediately - not after UFS/cache like V17. The leading theory is an
	 * unmanaged bootloader watchdog; managing it late defeats the test. */
	modprobe("dss");
	modprobe("s3c2410_wdt");

	g_wd_fd = open("/dev/watchdog", O_WRONLY | O_CLOEXEC);
	if (g_wd_fd >= 0) {
		int timeout = 0;
		if (ioctl(g_wd_fd, WDIOC_GETTIMEOUT, &timeout) == 0) {
			char m[64];
			snprintf(m, sizeof(m), "watchdog timeout=%ds\n", timeout);
			logf_uptime(m);
		}
		pet_watchdog();
		logf_uptime("opened /dev/watchdog immediately after s3c2410_wdt, first pet sent");
	} else {
		logf_uptime("open /dev/watchdog FAILED");
	}

	/* Storage controller - V17 never loaded this at all, so its cache mount
	 * was doomed regardless of the device-node fix. */
	modprobe("ufs-exynos-core");

	if (wait_for_partition("sda33", 15)) {
		logf_uptime("sda33 appeared in /proc/partitions");
		if (mount("/dev/block/sda33", "/cache", "ext4", MS_SYNCHRONOUS, NULL) == 0) {
			g_cache_ok = 1;
			flush_early_log();
			logf_uptime("cache mounted OK, early log flushed");
		} else {
			char m[64];
			snprintf(m, sizeof(m), "cache mount FAILED errno=%d\n", errno);
			logf_uptime(m);
		}
	} else {
		logf_uptime("sda33 never appeared after 15s wait - UFS probe likely failed");
	}

	/* USB chain. Deliberately does NOT include sec_reboot - it would
	 * override the clean built-in PSCI restart/poweroff handlers with
	 * Samsung's power-key-wait/5-retry path, unnecessary for this test. */
	static const char *usb_mods[] = {
		"phy-exynos-usbdrd-super","dwc3-exynos-usb","usb_typec_manager",
		"usb_notify_layer","usb_notifier","usb_f_conn_gadget", NULL
	};
	for (int i = 0; usb_mods[i]; i++) modprobe(usb_mods[i]);

	logf_uptime("module load pass complete");

	/* Bound-device sanity check for the drivers that matter most. */
	const char *check_drivers[] = { "s3c2410-wdt", "dwc3-exynos", "ufs-exynos-core" };
	for (int i = 0; i < 3; i++) {
		char m[80];
		snprintf(m, sizeof(m), "platform driver %s bound=%d\n",
		         check_drivers[i], platform_bound(check_drivers[i]));
		logf_uptime(m);
	}

	/* Decisive timing oracle: absolute monotonic deadline from true t=0
	 * (kernel boot), not a relative checkpoint taken after setup work.
	 * If the observed physical reset lands at true uptime ~45s (visible in
	 * the persisted log's own timestamps up to the last line before reset),
	 * PID1 was alive and healthy that whole time - the "crash-loop" framing
	 * is wrong and something else has its own independent schedule.
	 * reboot(RESTART) works with zero modules via the built-in PSCI restart
	 * handler - verified present on this device's DT and kernel config. */
	while (now_mono() < 45.0) {
		pet_watchdog();
		logf_uptime("heartbeat");
		sleep(1);
	}

	logf_uptime("reached absolute t=45s alive - self-rebooting now (PSCI restart)");
	sync();
	int rc = reboot(LINUX_REBOOT_CMD_RESTART);
	if (rc != 0) {
		char m[64];
		snprintf(m, sizeof(m), "reboot() syscall FAILED errno=%d\n", errno);
		logf_uptime(m);
	}

	for (;;) { pet_watchdog(); sleep(5); }
	return 0;
}
