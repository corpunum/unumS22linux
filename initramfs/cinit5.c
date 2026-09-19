#define _GNU_SOURCE
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/syscall.h>
#include <sys/reboot.h>
#include <sys/wait.h>
#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <errno.h>
#include <linux/reboot.h>

/* aarch64 has no init_module/finit_module libc wrapper in older glibcs used
 * by our static toolchain - call the syscall directly. */
static int finit_module(int fd, const char *param, int flags) {
	return syscall(SYS_finit_module, fd, param, flags);
}

static void wr(const char *path, const char *msg) {
	int fd = open(path, O_WRONLY | O_CREAT | O_APPEND | O_SYNC, 0644);
	if (fd >= 0) {
		write(fd, msg, strlen(msg));
		fsync(fd);
		close(fd);
	}
}

static int g_cache_ok = 0;
#define LOG(msg) do { wr("/dev/kmsg", "[v15] " msg "\n"); if (g_cache_ok) wr("/cache/v15_log.txt", "[v15] " msg "\n"); } while (0)

static void log_mod(const char *name, int ok, int err) {
	char buf[128];
	int n = 0;
	const char *p = "[v15] insmod ";
	while (*p) buf[n++] = *p++;
	p = name;
	while (*p) buf[n++] = *p++;
	p = ok ? " OK\n" : " FAILED errno=";
	while (*p) buf[n++] = *p++;
	if (!ok) {
		int v = err, d = 0; char db[8];
		if (v == 0) db[d++] = '0';
		while (v > 0) { db[d++] = '0' + v % 10; v /= 10; }
		while (d > 0) buf[n++] = db[--d];
		buf[n++] = '\n';
	}
	buf[n] = 0;
	wr("/dev/kmsg", buf);
	if (g_cache_ok) wr("/cache/v15_log.txt", buf);
}

static void load_mod(const char *path, const char *name) {
	int fd = open(path, O_RDONLY);
	if (fd < 0) { log_mod(name, 0, errno); return; }
	int ret = finit_module(fd, "", 0);
	close(fd);
	log_mod(name, ret == 0, ret == 0 ? 0 : -ret);
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

	wr("/dev/kmsg", "[v15] PID1 alive, tmpfs+mknod done (no devtmpfs on this kernel)\n");

	/* Load exynos-pmu-if + dss FIRST, deliberately out of official order,
	 * so /proc/last_kmsg has the best chance of capturing everything from
	 * here on if this boot dies later. */
	load_mod("/lib/modules/exynos-pmu-if.ko", "exynos-pmu-if");
	load_mod("/lib/modules/dss.ko", "dss");

	/* Now cache should actually be mountable - the block device node
	 * genuinely exists this time (earlier builds never mknod'd it and
	 * silently relied on devtmpfs, which this kernel doesn't have). */
	if (mount("/dev/block/sda33", "/cache", "ext4", MS_SYNCHRONOUS, NULL) == 0) {
		g_cache_ok = 1;
	}
	LOG("cache mount result logged above via g_cache_ok gate");
	wr("/cache/v15_log.txt", "===== V15 BOOT =====\n");

	/* Remaining official module load order, dependency-respecting since
	 * it's literally the real device's own known-good sequence. */
	static const char *mods[] = {
		"exynos-chipid_v2","exynos-reboot","sec_debug_base_early","clk_exynos",
		"exynos_mct_v2","s3c2410_wdt","sec_debug_mode","ems","sec_mpam",
		"sec_mpam_sysfs","zsmalloc","lzo","lzo-rle","ssg","blk-sec-stats",
		"irq-gic-v3-vh","mhi","phy-exynos-mipi-dsim","phy-exynos-mipi",
		"phy-exynos-usbdrd-super","pinctrl-samsung-s2mps26","pinctrl-samsung-s2mpm07",
		"pinctrl-samsung-core","pwm-samsung","samsung-dma","cmupmucal","cmu_ewf",
		"exynos_acpm","exynos-adv-tracer","exynos-adv-tracer-s2d","exynos-pmu-if",
		"exynos-pd_el3","exynos-s2mpu","exynos_pm_qos","s2mps25-regulator",
		"s2mps26-regulator","s2mpb02-regulator","s2mpm07_regulator","samsung_iommu",
		"samsung-iommu-group","dwc3-exynos-usb","usb_f_conn_gadget","usb_notify_layer",
		"usb_notifier","usb_typec_manager", NULL
	};
	for (int i = 0; mods[i]; i++) {
		char path[128];
		int n = 0;
		const char *pre = "/lib/modules/";
		while (*pre) path[n++] = *pre++;
		const char *nm = mods[i];
		while (*nm) path[n++] = *nm++;
		path[n++] = '.'; path[n++] = 'k'; path[n++] = 'o'; path[n] = 0;
		load_mod(path, mods[i]);
	}

	LOG("module load pass complete");

	/* Open the real watchdog device and pet it directly, plus start the
	 * real watchdogd binary as a second, independent mechanism. */
	int wdfd = open("/dev/watchdog", O_WRONLY);
	if (wdfd >= 0) LOG("opened /dev/watchdog OK"); else LOG("open /dev/watchdog FAILED");

	pid_t wd = fork();
	if (wd == 0) {
		execl("/system/bin/watchdogd", "watchdogd", "10", "20", (char *)NULL);
		_exit(127);
	}
	LOG("forked real watchdogd");

	/* Decisive timing oracle: self-trigger a clean reboot at a fixed T=45s
	 * regardless of anything else. If the observed reset timing matches
	 * ~45s, PID1 was alive and well that whole time - the "crash-loop"
	 * framing is wrong and something else (unmanaged HW watchdog) is
	 * resetting the SoC on its own schedule, not because of us. */
	for (int t = 0; t < 45; t++) {
		if (wdfd >= 0) write(wdfd, "1", 1);
		char buf[64];
		int n = 0;
		const char *p = "[v15] heartbeat t=";
		while (*p) buf[n++] = *p++;
		int d = 0; char db[8]; int v = t;
		if (v == 0) db[d++] = '0';
		while (v > 0) { db[d++] = '0' + v % 10; v /= 10; }
		while (d > 0) buf[n++] = db[--d];
		buf[n++] = '\n'; buf[n] = 0;
		wr("/dev/kmsg", buf);
		if (g_cache_ok) wr("/cache/v15_log.txt", buf);
		sleep(1);
	}

	LOG("reached T=45s alive - self-rebooting now as the timing oracle");
	sync();
	reboot(LINUX_REBOOT_CMD_RESTART);

	/* should never reach here */
	for (;;) sleep(5);
	return 0;
}
