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
#include <stdlib.h>
#include <signal.h>

/* V24 (cinit13.c) - incorporates Astra-only loop round 4 (5 findings on V23,
 * the final of 4 planned Astra-only rounds - loop did NOT converge, round 4
 * still found new issues including 2 incompletely-fixed races from round 3).
 * Summary of what changed since V23 (cinit12.c):
 *  - modprobe()'s child resetting signal dispositions one-by-one (round 3's
 *    fix) narrows but cannot atomically close the window before every reset
 *    has executed - a real fault landing in that gap still inherited PID1's
 *    handler and produced a fabricated "PID1 dying" record (proven live).
 *    Fixed properly: record the true PID1 pid once at startup and have the
 *    handler check actual process identity via getpid() (async-signal-safe)
 *    before ever claiming to be PID1 - correct regardless of any timing,
 *    now or from any future fork() this file might add.
 *  - the options-file write failure path could leave a malformed partial
 *    option (e.g. "tmr_atboot=" with no value) on disk, which modprobe-small
 *    still reads and forwards to the kernel's parameter parser - proven live
 *    that this can abort the module load entirely rather than falling back
 *    to the compiled default as the log message claimed. Now actively
 *    unlinks the file on write failure so modprobe-small finds none at all.
 *  - g_cache_ok was set to 1 AFTER close(fd) returned in the banner-write
 *    block, leaving a gap between "banner durably written" and "logging
 *    enabled" that a real SIGABRT could land in (proven live). Moved the
 *    assignment into the same scope as the write+fsync success check,
 *    before close().
 *  - the /proc/cmdline read loop had the same read-error-vs-EOF conflation
 *    the counter-reading code was already fixed for in V23 - a real read()
 *    error just fell through and presented whatever partial content had
 *    accumulated as if it were the complete cmdline, with no indication the
 *    tail was lost to an I/O error (proven live via injected EIO). Now
 *    tracks true EOF explicitly and logs an explicit "fragment, not
 *    complete" warning when a real read error occurs.
 *  - the fatal-signal handler's hand-assembled version tag was still
 *    literally 'v','2','2' from V20 (never updated by any of the sed-based
 *    version bumps in V21-V23, since it's built character-by-character
 *    rather than as a matchable string literal) - every fatal record from
 *    V21 through V23 mislabeled itself, making crash evidence
 *    cross-build-inconsistent. Fixed to 'v','2','4' and reviewed for other
 *    hand-assembled tags that a future text-substitution bump could miss.
 *
 * V23 (cinit12.c) - incorporates Astra-only loop round 3 (6 findings on V22).
 * See EXPERIMENTS.md for the full round-by-round history; summary of what
 * changed since V22 (cinit11.c):
 *  - boot counter: a read() error after some valid digits were already
 *    buffered was previously indistinguishable from a normal EOF, so a
 *    genuine I/O error mid-read could silently truncate and trust a
 *    corrupted value (proven live: "123456" + a 2-byte read + EIO produced
 *    a trusted "12"). Now tracks true EOF explicitly and rejects anything
 *    that didn't end via a clean EOF.
 *  - boot counter: the 7-digit cap validated the STORED value but not the
 *    INCREMENTED candidate, so 9999999 (accepted, 7 digits) incremented to
 *    10000000 (8 digits) - a value the very next boot would then reject,
 *    permanently wedging the counter one boot after silently writing it.
 *    Now rejects the increment itself if the candidate would exceed 7
 *    digits, rather than writing a value the counter can't read back.
 *  - g_cache_ok is now enabled as soon as the boot banner itself is
 *    confirmed durable, not only after the following early-log flush also
 *    succeeds - a fatal signal delivered during the flush (proven live via
 *    a real SIGABRT landing in exactly that window) previously found
 *    cache logging still disabled and had nowhere persistent to write.
 *  - two new diagnostic lines (the early-syscall summary and the
 *    watchdog-arm note) exceeded EARLY_LOG_LINE's 160-byte cap once
 *    wrapped in logf_uptime()'s timestamp prefix, silently truncating and
 *    losing their trailing newline (merging into the next record) whenever
 *    they landed in early_log. Split into shorter lines that fit.
 *  - the /etc/modules/s3c2410_wdt options-file write's return value was
 *    discarded, so a short write or real failure (reproduced live via
 *    ENOSPC) still unconditionally logged "wrote... tmr_atboot=1" -
 *    falsely asserting the watchdog experiment's precondition was met.
 *    Now requires a confirmed complete write before claiming success.
 *  - modprobe()'s forked child inherited PID1's fatal-signal handlers
 *    until exec() replaced them, so a crash in that narrow pre-exec window
 *    (proven live via an injected SIGSEGV) produced a fabricated "PID1
 *    dying" log line from a process that was never PID1, while the real
 *    PID1 kept running fine. Child now resets to SIG_DFL before exec.
 *
 * V22 (cinit11.c) - incorporates Astra-only loop round 2 (5 findings on V21)
 * plus leftover Opus round-9 findings not already covered by round-1 fixes
 * (F-6 watchdog-arm note, F-7 early-syscall error logging, F-9 sig_atomic_t,
 * F-10 signal-handler install ordering). See EXPERIMENTS.md for the full
 * round-by-round history; summary of what changed since V21 (cinit10.c):
 *  - boot counter: distinguishes "legitimately first boot" (ENOENT) from
 *    "unavailable/untrustworthy" (any other failure, short read, invalid
 *    content, or a value that would need >7 digits) - previously any
 *    failure silently reset the sequence to 1, and an INT_MAX counter would
 *    overflow to a negative number on increment.
 *  - boot banner: now written directly to the log file BEFORE
 *    flush_early_log() runs, achieving true first-in-file placement -
 *    queuing it into early_log first (V21's approach) does NOT make it
 *    appear first, since flush walks the queue forward from index 0.
 *  - fatal-signal handler: uses write_full() instead of a single raw
 *    write(), so a short write or EINTR no longer silently drops part or
 *    all of the one line this handler exists to guarantee.
 *  - pre-reboot unmount: cache logging is disabled BEFORE attempting
 *    umount(), not after, closing a window where a signal landing between
 *    a successful umount() and the flag-clear could still write into the
 *    now-detached ramdisk tmpfs. g_cache_mounted is only cleared on a
 *    CONFIRMED successful unmount, not on failure (a failed umount leaves
 *    the filesystem genuinely still mounted).
 *  - g_cache_ok / g_kmsg_fd are volatile sig_atomic_t (shared with the
 *    signal handler; plain int is technically UB there).
 *  - signal handlers installed after g_kmsg_fd opens, not before.
 *  - early tmpfs/mknod/proc/sysfs syscalls now capture and log errno
 *    instead of being silently unchecked.
 *  - explicit log line noting this is the first build in the series to
 *    arm the watchdog at probe (tmr_atboot=1), and that it runs on the
 *    raw DT timeout rather than stock watchdogd's 30s SETTIMEOUT window.
 *
 * V20 - incorporates rounds 7 (Opus) + 8 (Astra cross-check) findings on top
 * of V19 (cinit8.c). Two of the project's long-held "known facts" were
 * overturned this round, both confirmed live against the real phone:
 *  - tmr_atboot default is 0 at compile time, but the BOOTLOADER passes
 *    s3c2410_wdt.tmr_atboot=1 on /proc/cmdline. Stock AOSP init's ueventd/
 *    module loading picks this up; our busybox modprobe (CONFIG_MODPROBE_SMALL,
 *    no CONFIG_FEATURE_CMDLINE_MODULE_OPTIONS) does NOT read /proc/cmdline at
 *    all, so under every prior custom-init build the watchdog driver silently
 *    probed with the opposite policy from stock. Fixed below via modprobe-
 *    small's actual options mechanism: an /etc/modules/<name> file, NOT a
 *    modprobe command-line argument (verified in modprobe-small.c - extra
 *    argv is discarded, only that file is read).
 *  - /dev/console returns ENODEV on this device - confirmed live, DSS (the
 *    only /proc/consoles entry) has no .device callback. Every prior build's
 *    "console wired" log line was printed unconditionally and was FALSE. Now
 *    logs the real fd numbers and errno instead of an assumed success.
 *  - the "watchdog auto-ping" theory behind opening /dev/watchdog was itself
 *    wrong (this driver never sets WDOG_HW_RUNNING, so there was never a
 *    kernel-side pre-open feeder to disable) - but userspace petting is still
 *    the only thing keeping the SoC alive once opened, and several blocking
 *    operations (ext4 mount, log fsync, sync()) never pet, which is real.
 *  - the "t=45s deadline" was not actually a deadline: modprobe's waitpid
 *    loop could block indefinitely on a hung child, and mount()/sync() are
 *    unbounded blocking calls with no absolute wall-clock cap around them.
 *  - CONFIG_S3C2410_SHUTDOWN_REBOOT=y rearms a SEPARATE 30s watchdog timeout
 *    on any reboot()-triggered device_shutdown(), independent of whether this
 *    program ever opened /dev/watchdog itself - a stall after that point can
 *    produce a watchdog reset that looks like "PSCI restart" in the log but
 *    isn't.
 *  - sec_debug.ko (the module that writes PANIC_INFORM/UPLOAD_CAUSE on a real
 *    kernel panic) is not in this build's module closure, so /proc/cmdline's
 *    reset_reason field reflects the PREVIOUS boot's cause, not necessarily
 *    a cause this program's own crash would produce cleanly - it's useful
 *    context, not a proven panic oracle for this exact program.
 * Carries forward every V15-V19 fix (console/kmsg fd ordering attempt, single
 * watchdog owner, real busybox modprobe dependency resolution, absolute
 * monotonic clock use, no sec_reboot.ko, deduplicated module directory,
 * read-to-EOF /proc/modules parsing, verified platform_bound driver/device
 * names, modprobe's reaped-status gating).
 */

#define EARLY_LOG_MAX 96
#define EARLY_LOG_LINE 160
static char early_log[EARLY_LOG_MAX][EARLY_LOG_LINE];
static int early_log_n = 0;
/* Astra round-2 (Opus F-9) fix: g_cache_ok and g_kmsg_fd are read/written
 * from fatal_sig_handler() as well as normal control flow. Plain `int` is
 * technically undefined behavior for cross-signal-handler access - the
 * compiler is free to keep a stale cached value in a register at -O2. Both
 * are now volatile sig_atomic_t, the only integer type POSIX guarantees is
 * safe to share between a signal handler and the code it can interrupt. */
static volatile sig_atomic_t g_cache_ok = 0;
/* Astra round-1 P4 fix: g_cache_ok conflated "logging currently healthy" with
 * "cache is mounted" - a log-write failure cleared g_cache_ok but left the
 * mount in place with nobody planning to unmount it, and a successful clean
 * unmount left g_cache_ok=1 so a later fatal-handler write would silently
 * land in the ramdisk tmpfs instead of on the (now unmounted) ext4 device.
 * g_cache_mounted tracks mount ownership independently; g_cache_ok tracks
 * only "is it currently safe to write a log line to /cache". */
static int g_cache_mounted = 0;
static volatile sig_atomic_t g_kmsg_fd = -1;
static int g_wd_fd = -1;
/* Astra round-4 finding 1 fix: resetting modprobe()'s forked child's signal
 * dispositions to SIG_DFL one-by-one (the round-3 fix) narrows but cannot
 * close the window where a real fault hits the child between fork() and the
 * last reset() call - it still inherits the parent's handler until every
 * reset has actually executed. Proven live: a real SIGSEGV/SIGABRT delivered
 * in that exact window still produced a fabricated "PID1 dying" record from
 * a process that was never PID1. No sequence of reset() calls can close this
 * atomically from userspace. Instead, record the true PID1 pid once at
 * startup and have the handler itself check identity before claiming to be
 * PID1 - this is correct regardless of ANY timing, in the child or anywhere
 * else a future fork() might be added. getpid() is an async-signal-safe
 * syscall, safe to call from inside the handler. */
static pid_t g_pid1_pid = 0;

/* Astra round-1 P2 fix: write() can do a short write even with no error
 * (r >= 0, r < len) - both log_line() and flush_early_log() previously only
 * checked for r < 0, so a short write was silently treated as a fully
 * persisted record. Loop until all bytes are written, retry EINTR, and treat
 * zero forward progress as a failure rather than looping forever. */
static int write_full(int fd, const char *buf, size_t len) {
	size_t off = 0;
	while (off < len) {
		ssize_t w = write(fd, buf + off, len - off);
		if (w < 0) {
			if (errno == EINTR) continue;
			return -1;
		}
		if (w == 0) return -1; /* no forward progress - treat as failure */
		off += (size_t)w;
	}
	return 0;
}

static double now_mono(void) {
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec + ts.tv_nsec / 1e9;
}

static double g_last_pet_ok = -1.0;

static void pet_watchdog(void) {
	if (g_wd_fd >= 0) {
		int r = write(g_wd_fd, "1", 1);
		if (r == 1) g_last_pet_ok = now_mono();
		/* failure deliberately not logged here to avoid recursion into
		 * log_line() from a hot path called every 200ms; last-known-good
		 * pet time is logged explicitly at the point that matters (just
		 * before the final reboot). */
	}
}

/* D4 fix: log_line's cache open now has O_CREAT too (previously relied on
 * flush_early_log() having created the file first, but g_cache_ok was set to
 * 1 BEFORE that ran - if flush_early_log()'s own create ever failed, every
 * later log_line() write became a silent no-op AND the early_log fallback
 * was already bypassed since g_cache_ok was 1: total undetectable evidence
 * loss). Also: if a cache write fails here, clear g_cache_ok so the
 * in-memory fallback resumes capturing subsequent lines instead of silently
 * dropping them too. */
static void log_line(const char *msg) {
	if (g_kmsg_fd >= 0) {
		int r = write(g_kmsg_fd, msg, strlen(msg));
		(void)r;
	}
	if (g_cache_ok) {
		int fd = open("/cache/v24_log.txt", O_WRONLY | O_CREAT | O_APPEND, 0644);
		if (fd >= 0) {
			int w = write_full(fd, msg, strlen(msg));
			int fs = fsync(fd);
			close(fd);
			if (w < 0 || fs < 0) g_cache_ok = 0;
		} else {
			g_cache_ok = 0;
		}
	}
	if (!g_cache_ok && early_log_n < EARLY_LOG_MAX) {
		strncpy(early_log[early_log_n], msg, EARLY_LOG_LINE - 1);
		early_log[early_log_n][EARLY_LOG_LINE - 1] = 0;
		early_log_n++;
	}
}

/* Returns 1 only on a confirmed successful create+write - caller gates
 * g_cache_ok on this return value rather than assuming success. */
static int flush_early_log(void) {
	int fd = open("/cache/v24_log.txt", O_WRONLY | O_CREAT | O_APPEND, 0644);
	if (fd < 0) return 0;
	int ok = 1;
	for (int i = 0; i < early_log_n; i++) {
		if (write_full(fd, early_log[i], strlen(early_log[i])) < 0) ok = 0;
	}
	if (fsync(fd) < 0) ok = 0;
	close(fd);
	return ok;
}

static void logf_uptime(const char *prefix) {
	char buf[220];
	double t = now_mono();
	int sec = (int)t;
	int frac = (int)((t - sec) * 1000);
	snprintf(buf, sizeof(buf), "[v24 t=%d.%03ds] %s\n", sec, frac, prefix);
	log_line(buf);
}

/* Astra round-1 P1 fix: dumping /proc/cmdline "verbatim" was defeated by
 * three separate buffer sizes downstream - a 2048-byte read buffer, then
 * logf_uptime()'s 220-byte formatting buffer, then (if cache wasn't yet
 * mounted) EARLY_LOG_LINE's 160-byte cap. On the real phone (cmdline is
 * 3329 bytes), the reset_reason and watchdog-option fields this diagnostic
 * exists to capture were both silently cut off, and the truncation ate the
 * trailing newline too, merging the next log record into the cmdline dump.
 * Fixed by logging the cmdline in raw EARLY_LOG_LINE-sized chunks through
 * log_line() directly (bypassing logf_uptime's small formatting buffer),
 * each chunk numbered and newline-terminated, with an explicit line if the
 * outer 4096-byte cmdline read buffer itself gets exhausted. */
static void log_cmdline_chunked(const char *cbuf, size_t total, int truncated_at_read) {
	size_t chunk_max = EARLY_LOG_LINE - 40; /* room for the numbering prefix */
	size_t off = 0;
	int idx = 0;
	while (off < total) {
		size_t n = total - off;
		if (n > chunk_max) n = chunk_max;
		char line[EARLY_LOG_LINE];
		int hdrlen = snprintf(line, sizeof(line), "cmdline[%d]: ", idx);
		size_t room = sizeof(line) - (size_t)hdrlen - 2; /* newline + NUL */
		if (n > room) n = room;
		memcpy(line + hdrlen, cbuf + off, n);
		line[hdrlen + n] = '\n';
		line[hdrlen + n + 1] = 0;
		log_line(line);
		off += n;
		idx++;
	}
	if (truncated_at_read) {
		logf_uptime("prev-boot /proc/cmdline: TRUNCATED at read buffer limit, some tail content lost");
	}
}

/* Normalize a module name the same way the kernel does in /proc/modules
 * (hyphens become underscores) so the presence check actually matches. */
static void normalize(char *dst, const char *src, size_t n) {
	size_t i = 0;
	for (; src[i] && i < n - 1; i++) dst[i] = (src[i] == '-') ? '_' : src[i];
	dst[i] = 0;
}

/* /proc/modules is a seq_file - a single read() only returns one internal
 * page's worth of data (empirically ~4KB), NOT the whole file. Proven on
 * real hardware: a single-read version of this function returned "absent"
 * for all 9 requested modules even when every one was actually loaded.
 * Must loop until read() returns 0 (EOF), like any other file. */
static int module_present(const char *name) {
	char norm[64];
	normalize(norm, name, sizeof(norm));
	size_t nlen = strlen(norm);
	int fd = open("/proc/modules", O_RDONLY);
	if (fd < 0) return 0;

	static char buf[32768];
	size_t total = 0;
	for (;;) {
		if (total >= sizeof(buf) - 1) break;
		ssize_t n = read(fd, buf + total, sizeof(buf) - 1 - total);
		if (n < 0) {
			if (errno == EINTR) continue;
			break;
		}
		if (n == 0) break; /* real EOF */
		total += (size_t)n;
	}
	close(fd);
	if (total == 0) return 0;
	buf[total] = 0;

	char *p = buf;
	while (*p) {
		if (strncmp(p, norm, nlen) == 0 && (p[nlen] == ' ' || p[nlen] == '\t')) return 1;
		char *nl = strchr(p, '\n');
		if (!nl) break;
		p = nl + 1;
	}
	return 0;
}

/* D8 fix: 3-state result instead of collapsing "driver never registered" and
 * "driver registered but this device didn't bind" into the same bound=0.
 * Returns: 0 = no driver dir at all (module never loaded / driver never
 * registered), 1 = driver dir exists but this device instance isn't bound,
 * 2 = device instance symlink present (bound). Driver names verified live
 * against the real phone - they do NOT match the .ko module names (e.g.
 * module dwc3-exynos-usb registers driver "exynos-dwc3", not "dwc3-exynos").
 * exynos-ufs has suppress_bind_attrs, so the device symlink is the only
 * available proof of binding for it. */
static int platform_bound(const char *drv, const char *dev) {
	char drvpath[160], devpath[160];
	snprintf(drvpath, sizeof(drvpath), "/sys/bus/platform/drivers/%s", drv);
	if (access(drvpath, F_OK) != 0) return 0;
	snprintf(devpath, sizeof(devpath), "/sys/bus/platform/drivers/%s/%s", drv, dev);
	return access(devpath, F_OK) == 0 ? 2 : 1;
}

/* N1 fix: bounded wait. Previously the WNOHANG poll loop had no cap, so a
 * hung modprobe child (or a child stuck on an uninterruptible kernel probe)
 * could block the entire boot sequence forever - the "t=45s deadline" was
 * never actually enforced against this. A hard cap per module means a stuck
 * child at worst costs MODPROBE_MAX_WAIT_S of wall time, not infinity; the
 * child itself is deliberately leaked (as a zombie or orphan) rather than
 * killed, since killing it while it may be inside a kernel probe() callback
 * risks a worse-defined state than just moving on.
 *
 * Run busybox modprobe as a child, pet the watchdog while waiting (not just
 * during the partition poll), capture stderr into kmsg, and verify success
 * against /proc/modules instead of trusting the exit code alone. */
#define MODPROBE_MAX_WAIT_S 20
static void modprobe(const char *name) {
	char msg[160];
	snprintf(msg, sizeof(msg), "modprobe %s: starting", name);
	logf_uptime(msg);

	int devnull = open("/dev/null", O_WRONLY);

	pid_t pid = fork();
	if (pid < 0) {
		int saved_errno = errno;
		if (devnull >= 0) close(devnull);
		snprintf(msg, sizeof(msg), "modprobe %s: fork() FAILED errno=%d", name, saved_errno);
		logf_uptime(msg);
		return;
	}
	if (pid == 0) {
		/* Astra round-3 finding 5 fix: fork() copies the parent's signal
		 * dispositions, so this child briefly runs with PID1's fatal_sig_
		 * handler still installed for SIGSEGV/BUS/ILL/ABRT/FPE until exec()
		 * replaces it. Proven live: a SIGSEGV injected into this exact
		 * pre-exec window in the actual modprobe() child produced a real
		 * "FATAL sig=11 PID1 dying" log line from a process that was never
		 * PID1 and left the real PID1 running fine afterward - a fabricated
		 * fatal record for an event that wasn't fatal to the boot at all.
		 * Reset to default disposition before exec so a crash here is
		 * reported as an ordinary abnormal child exit (visible in the
		 * modprobe result's exit= field below) instead of a false PID1
		 * death. */
		signal(SIGSEGV, SIG_DFL);
		signal(SIGBUS, SIG_DFL);
		signal(SIGILL, SIG_DFL);
		signal(SIGABRT, SIG_DFL);
		signal(SIGFPE, SIG_DFL);
		if (devnull >= 0) dup2(devnull, 1);
		if (g_kmsg_fd >= 0) dup2(g_kmsg_fd, 2);
		execl("/busybox", "/busybox", "modprobe", name, (char *)NULL);
		_exit(127);
	}
	if (devnull >= 0) close(devnull);

	int status = -1;
	int reaped = 0;
	int timed_out = 0;
	int saved_errno = 0;
	pid_t r;
	double deadline = now_mono() + MODPROBE_MAX_WAIT_S;
	while (!reaped) {
		r = waitpid(pid, &status, WNOHANG);
		if (r == pid) { reaped = 1; break; }
		if (r < 0) {
			if (errno == EINTR) continue;
			saved_errno = errno;
			break; /* genuinely can't reap - report as unknown, not fake success */
		}
		if (now_mono() >= deadline) { timed_out = 1; break; }
		pet_watchdog();
		usleep(200000);
	}

	int present = module_present(name);
	if (reaped) {
		snprintf(msg, sizeof(msg), "modprobe %s: exit=%d proc_modules=%s", name,
		         WIFEXITED(status) ? WEXITSTATUS(status) : -1, present ? "yes" : "no");
	} else if (timed_out) {
		snprintf(msg, sizeof(msg), "modprobe %s: TIMED OUT after %ds, abandoning wait (child leaked) proc_modules=%s",
		         name, MODPROBE_MAX_WAIT_S, present ? "yes" : "no");
	} else {
		snprintf(msg, sizeof(msg), "modprobe %s: waitpid FAILED errno=%d proc_modules=%s",
		         name, saved_errno, present ? "yes" : "no");
	}
	logf_uptime(msg);
}

/* D6 fix: /proc/partitions is a seq_file exactly like /proc/modules - round 6
 * fixed the single-read() bug in module_present() but left this second call
 * site with the identical bug. Loop to real EOF, and match on BOTH boundaries
 * of the name field (leading and trailing), not just trailing. V20 only
 * checked the trailing boundary, so searching for "da33" would falsely match
 * inside "sda33" - confirmed live via Astra round 1: live partition checks
 * against the real phone returned sda33=1 AND da33=1 AND a33=1, proving the
 * "line-anchored" claim was only half-anchored. /proc/partitions fields are
 * whitespace-separated ("major minor #blocks name"), so require the char
 * before a match to be buffer-start or whitespace too. */
static int wait_for_partition(const char *name, int max_seconds) {
	size_t nlen = strlen(name);
	for (int i = 0; i < max_seconds * 5; i++) {
		pet_watchdog();
		int fd = open("/proc/partitions", O_RDONLY);
		if (fd >= 0) {
			static char buf[16384];
			size_t total = 0;
			for (;;) {
				if (total >= sizeof(buf) - 1) break;
				ssize_t n = read(fd, buf + total, sizeof(buf) - 1 - total);
				if (n < 0) { if (errno == EINTR) continue; break; }
				if (n == 0) break;
				total += (size_t)n;
			}
			close(fd);
			if (total > 0) {
				buf[total] = 0;
				char *p = buf;
				while ((p = strstr(p, name)) != NULL) {
					char after = p[nlen];
					char before = (p == buf) ? ' ' : p[-1];
					int before_ok = (before == ' ' || before == '\t' || before == '\n');
					int after_ok = (after == '\n' || after == 0 || after == ' ' || after == '\t');
					if (before_ok && after_ok) return 1;
					p += nlen;
				}
			}
		}
		usleep(200000);
	}
	return 0;
}

/* Minimal async-signal-safe unsigned-int-to-string, for use inside the
 * signal handler below (no snprintf, no floating point - see the round-1
 * Astra finding this replaces: the previous version called snprintf() with
 * "%.3f" floating-point formatting from inside a signal handler, which is
 * NOT on the POSIX async-signal-safe list regardless of how deterministic
 * it looks in practice). Returns length written, writes into buf backwards
 * then shifts forward. */
static int u2a(unsigned long v, char *buf) {
	char tmp[24];
	int i = 0;
	if (v == 0) tmp[i++] = '0';
	while (v > 0) { tmp[i++] = '0' + (v % 10); v /= 10; }
	int len = i;
	for (int j = 0; j < len; j++) buf[j] = tmp[len - 1 - j];
	return len;
}

/* D7 fix, hardened per Astra round-1 P5: fatal-signal handlers. Cannot catch
 * a real kernel panic, a watchdog bite, or a PMIC reset, and the watchdog's
 * panic notifier does not uniformly reset in 5s (only when
 * num_online_cpus()>1 at notify time). But when PID1 itself dies of a
 * synchronous fault (segfault, illegal instruction, etc.) this is the
 * difference between "the log just stops" and "the log says why" for that
 * specific failure class - worth having even though it doesn't cover every
 * failure mode.
 *
 * Two bugs fixed from the original D7 version: (1) it called snprintf() with
 * "%.3f" from inside a signal handler - not async-signal-safe, replaced with
 * integer seconds via u2a() above; (2) resetting the disposition to SIG_DFL
 * and calling raise() does not GUARANTEE PID1 actually dies - the kernel
 * discards default-action signals for SIGNAL_UNKILLABLE tasks (global init
 * has special signal-delivery semantics, kernel/signal.c). If termination is
 * the intent, an explicit _exit() after best-effort logging is the only way
 * to be sure - the parent reboot()-based timing oracle still runs from the
 * exec'd child path either way, so this is safe to call unconditionally. */
static void fatal_sig_handler(int sig) {
	/* Astra round-4 finding 1 fix: no sequence of signal(sig, SIG_DFL) reset
	 * calls in a forked child can atomically close every window before all
	 * of them have executed - a real fault landing in that gap still hits
	 * this handler while running as the child, not PID1, and previously
	 * always claimed "PID1 dying" regardless. Checking real identity here is
	 * correct independent of any timing anywhere a fork() ever happens, now
	 * or in a future edit - getpid() is an async-signal-safe syscall. A
	 * non-PID1 process hitting this handler just dies via the normal exit
	 * path without writing a false PID1 death record; modprobe()'s own
	 * exit= reporting already captures that a child died abnormally. */
	if (getpid() != g_pid1_pid) {
		_exit(128 + sig);
	}

	char buf[64];
	int n = 0;
	buf[n++] = '[';
	buf[n++] = 'v'; buf[n++] = '2'; buf[n++] = '4';
	buf[n++] = ' '; buf[n++] = 't'; buf[n++] = '=';
	n += u2a((unsigned long)now_mono(), buf + n);
	buf[n++] = 's'; buf[n++] = ']'; buf[n++] = ' ';
	const char *tag = "FATAL sig=";
	for (const char *c = tag; *c; c++) buf[n++] = *c;
	n += u2a((unsigned long)sig, buf + n);
	buf[n++] = ' ';
	const char *tag2 = "PID1 dying\n";
	for (const char *c = tag2; *c; c++) buf[n++] = *c;

	/* Astra round-2 P3 fix: both writes here previously used a single raw
	 * write() call with no retry - a short write (proven live via a
	 * disposable-child RLIMIT_FSIZE test: a 3-byte cap produced a 3-byte
	 * persisted record, "[v2") or an EINTR would silently lose part or all
	 * of the one line this handler exists to guarantee. write_full() itself
	 * only calls write() and checks errno, both async-signal-safe, so it is
	 * safe to reuse verbatim from inside a signal handler. */
	if (g_kmsg_fd >= 0) { int w = write_full(g_kmsg_fd, buf, n); (void)w; }
	if (g_cache_ok) {
		int fd = open("/cache/v24_log.txt", O_WRONLY | O_CREAT | O_APPEND, 0644);
		if (fd >= 0) { int w = write_full(fd, buf, n); (void)w; fsync(fd); close(fd); }
	}
	_exit(128 + sig);
}

int main(void) {
	g_pid1_pid = getpid(); /* must be set before signal() installs any handler */

	/* Opus round-9 F-7 fix: these early syscalls (tmpfs mount, mknods, proc/
	 * sysfs mounts) previously had zero error checking or logging - exactly
	 * the operations most likely to differ between this custom init and
	 * AOSP init, and a failure here would surface only as maximally
	 * misleading downstream symptoms ("open /proc/cmdline FAILED",
	 * "proc_modules=no" on every module). Captured into a small errno array
	 * and logged as one summary line right after kmsg opens (kmsg isn't
	 * open yet at this point in boot). */
	int e_dev = mkdir("/dev", 0755) == 0 ? 0 : errno;
	int e_devtmpfs = mount("tmpfs", "/dev", "tmpfs", 0, NULL) == 0 ? 0 : errno;
	int e_devblock = mkdir("/dev/block", 0755) == 0 ? 0 : errno;
	int e_proc = mkdir("/proc", 0755) == 0 ? 0 : errno;
	int e_sys = mkdir("/sys", 0755) == 0 ? 0 : errno;
	mkdir("/cache", 0755);
	mkdir("/etc", 0755);
	mkdir("/etc/modules", 0755);

	int e_kmsg_n = mknod("/dev/kmsg", S_IFCHR | 0666, makedev(1, 11)) == 0 ? 0 : errno;
	int e_null_n = mknod("/dev/null", S_IFCHR | 0666, makedev(1, 3)) == 0 ? 0 : errno;
	int e_con_n = mknod("/dev/console", S_IFCHR | 0622, makedev(5, 1)) == 0 ? 0 : errno;
	int e_wd_n = mknod("/dev/watchdog", S_IFCHR | 0644, makedev(10, 130)) == 0 ? 0 : errno;
	int e_sda33_n = mknod("/dev/block/sda33", S_IFBLK | 0660, makedev(259, 17)) == 0 ? 0 : errno;

	int e_procmnt = mount("proc", "/proc", "proc", 0, NULL) == 0 ? 0 : errno;
	int e_sysmnt = mount("sysfs", "/sys", "sysfs", 0, NULL) == 0 ? 0 : errno;

	/* Console open attempted for completeness, but confirmed live on this
	 * device to return ENODEV - /proc/consoles' only entry (dss-1) has no
	 * .device callback, so this always fails here. Prior builds logged
	 * "console wired" unconditionally, which was FALSE. Now logs the real
	 * fd/errno so that fact is visible in evidence instead of assumed. */
	int con_errno = 0;
	int con = open("/dev/console", O_RDWR);
	if (con < 0) con_errno = errno;
	if (con >= 0) {
		dup2(con, 0); dup2(con, 1); dup2(con, 2);
		if (con > 2) close(con);
	}

	/* Open kmsg and force it off fds 0-2 regardless of what it landed on -
	 * still correct and still needed, since console open is expected to
	 * fail on this device (fds 0-2 stay free either way). */
	g_kmsg_fd = open("/dev/kmsg", O_WRONLY);
	int kmsg_errno = (g_kmsg_fd < 0) ? errno : 0;
	if (g_kmsg_fd >= 0 && g_kmsg_fd <= 2) {
		int moved = fcntl(g_kmsg_fd, F_DUPFD_CLOEXEC, 10);
		if (moved >= 0) { close(g_kmsg_fd); g_kmsg_fd = moved; }
	}

	/* Opus round-9 F-10 fix: signal handlers previously installed before
	 * g_kmsg_fd was opened - a fault in that window would log nowhere at
	 * all (g_kmsg_fd==-1, g_cache_ok==0, no early_log fallback in the
	 * handler). Installed here instead, right after kmsg is confirmed. */
	signal(SIGSEGV, fatal_sig_handler);
	signal(SIGBUS, fatal_sig_handler);
	signal(SIGILL, fatal_sig_handler);
	signal(SIGABRT, fatal_sig_handler);
	signal(SIGFPE, fatal_sig_handler);

	{
		char m[160];
		snprintf(m, sizeof(m), "PID1 alive, tmpfs+mknod done. console_fd=%d(errno=%d) kmsg_fd=%d(errno=%d)",
		         con, con_errno, g_kmsg_fd, kmsg_errno);
		logf_uptime(m);
	}
	/* Astra round-3 finding 3 fix: this line (and the watchdog-arm note
	 * below) previously exceeded EARLY_LOG_LINE (160 bytes) once wrapped in
	 * logf_uptime()'s "[v23 t=...] " prefix, so it was silently truncated
	 * and lost its trailing newline whenever it landed in early_log (before
	 * cache mounts) - proven live: the truncated tail merged into the next
	 * record. Split into two short lines, each comfortably under the cap,
	 * instead of one long one. */
	{
		char m[100];
		snprintf(m, sizeof(m), "early syscalls A: dev=%d devtmpfs=%d devblock=%d proc=%d sys=%d (0=ok else errno)",
		         e_dev, e_devtmpfs, e_devblock, e_proc, e_sys);
		logf_uptime(m);
	}
	{
		char m[130];
		snprintf(m, sizeof(m), "early syscalls B: kmsg_n=%d null_n=%d con_n=%d wd_n=%d sda33_n=%d procmnt=%d sysmnt=%d (0=ok else errno)",
		         e_kmsg_n, e_null_n, e_con_n, e_wd_n, e_sda33_n, e_procmnt, e_sysmnt);
		logf_uptime(m);
	}

	/* D1 fix (highest-value finding of rounds 7-8), hardened per Astra round-1
	 * P1: dump /proc/cmdline verbatim, immediately, before any module
	 * loading. This is bootloader-authored evidence of the PREVIOUS boot's
	 * reset cause (sec_debug_reset_reason.reset_reason= et al, decodable
	 * even after /proc/last_kmsg has bit-rotted).
	 *
	 * V20's version was NOT actually verbatim: on the real phone (cmdline is
	 * 3329 bytes), a chain of THREE undersized buffers - a 2048-byte read
	 * buffer, then logf_uptime()'s 220-byte formatting buffer, then (if
	 * cache wasn't mounted yet) EARLY_LOG_LINE's 160-byte early-log cap -
	 * silently cut off the reset_reason and watchdog-option fields this
	 * diagnostic exists to capture, and ate the trailing newline, merging
	 * the next log record into the truncated dump. Fixed by reading into a
	 * bigger 4096-byte buffer, then emitting it via log_cmdline_chunked()
	 * in numbered, newline-terminated, EARLY_LOG_LINE-sized pieces that
	 * bypass logf_uptime's small buffer entirely. */
	{
		/* Astra round-4 finding 4 fix: this loop had the exact same
		 * error-vs-EOF conflation the counter-reading code was already
		 * fixed for - a real read() error (not EINTR) just `break`s, same
		 * as normal EOF, so whatever partial content had accumulated so
		 * far was silently presented as if it were the complete cmdline,
		 * with no indication the tail (often including the very
		 * reset_reason/watchdog fields this diagnostic exists to capture)
		 * was lost to an I/O error rather than genuinely ending there.
		 * Proven live via injected EIO after a partial read. Now tracks
		 * true EOF explicitly and logs an explicit warning distinguishing
		 * "read error, content below is a fragment" from a real complete
		 * capture. */
		int fd = open("/proc/cmdline", O_RDONLY);
		if (fd >= 0) {
			static char cbuf[4096];
			size_t total = 0;
			int truncated = 0;
			int hit_eof = 0;
			int read_error = 0;
			for (;;) {
				if (total >= sizeof(cbuf) - 1) { truncated = 1; break; }
				ssize_t n = read(fd, cbuf + total, sizeof(cbuf) - 1 - total);
				if (n < 0) {
					if (errno == EINTR) continue;
					read_error = 1;
					break;
				}
				if (n == 0) { hit_eof = 1; break; }
				total += (size_t)n;
			}
			close(fd);
			cbuf[total] = 0;
			if (read_error) {
				char m[110];
				snprintf(m, sizeof(m), "/proc/cmdline read FAILED errno=%d after %zu bytes - fragment follows, NOT complete", errno, total);
				logf_uptime(m);
			}
			if (total > 0 && (hit_eof || truncated || read_error)) {
				log_cmdline_chunked(cbuf, total, truncated);
			} else if (total == 0 && !read_error) {
				logf_uptime("/proc/cmdline: zero bytes read");
			}
		} else {
			logf_uptime("open /proc/cmdline FAILED");
		}
	}

	/* dss first (for /proc/last_kmsg capture, though known corrupted on this
	 * device - kept for completeness/future devices), THEN the watchdog
	 * immediately - not after UFS/cache like V17. The leading theory is an
	 * unmanaged bootloader watchdog; managing it late defeats the test. */
	modprobe("dss");

	/* F1 fix: the bootloader passes s3c2410_wdt.tmr_atboot=1 on /proc/cmdline
	 * (confirmed live), which is what stock AOSP init's boot ends up
	 * matching, but this busybox's modprobe (CONFIG_MODPROBE_SMALL, no
	 * CONFIG_FEATURE_CMDLINE_MODULE_OPTIONS) never reads /proc/cmdline, and
	 * an execl() argv option is silently discarded by modprobe-small
	 * (verified in its source - extra argv beyond the module name is
	 * ignored). The only mechanism modprobe-small actually honors is an
	 * /etc/modules/<name> options file. Write it so the driver probes with
	 * the SAME policy as stock, making this an apples-to-apples comparison
	 * instead of an unnoticed divergence. */
	{
		/* Astra round-3 finding 4 fix: the write()'s return value was
		 * discarded, so a short write or outright failure (e.g. a real
		 * ENOSPC, reproduced live) still unconditionally logged "wrote...
		 * tmr_atboot=1" - falsely asserting the watchdog experiment's
		 * precondition was met when the options file could be empty or
		 * missing content. Now requires a confirmed complete write via
		 * write_full() before claiming success. */
		int fd = open("/etc/modules/s3c2410_wdt", O_WRONLY | O_CREAT | O_TRUNC, 0644);
		int wrote_ok = 0;
		if (fd >= 0) {
			wrote_ok = (write_full(fd, "tmr_atboot=1", 12) == 0);
			close(fd);
		}
		if (wrote_ok) {
			logf_uptime("wrote /etc/modules/s3c2410_wdt: tmr_atboot=1 (match stock bootloader policy)");
		} else {
			/* Astra round-4 finding 2 fix: on a short/failed write, a
			 * malformed fragment (e.g. "tmr_atboot=" with no value) could be
			 * left on disk from the O_TRUNC create - proven live that
			 * modprobe-small still reads and forwards this to the kernel's
			 * module-parameter parser, which (per this device's own kernel
			 * source, an integer param) can abort the module load entirely
			 * on a parse error rather than just falling back to the
			 * compiled default as the previous message claimed. Actively
			 * remove the file so modprobe-small finds none at all (its
			 * documented, safe fallback to the compiled default) instead of
			 * leaving unknown partial content behind. */
			int unlinked = (unlink("/etc/modules/s3c2410_wdt") == 0 || errno == ENOENT);
			if (unlinked) {
				logf_uptime("FAILED to write /etc/modules/s3c2410_wdt (removed any partial content) - watchdog will probe with compiled default tmr_atboot=0, diverging from stock");
			} else {
				char m[140];
				snprintf(m, sizeof(m), "FAILED to write /etc/modules/s3c2410_wdt AND failed to remove partial content errno=%d - probe outcome unknown", errno);
				logf_uptime(m);
			}
		}
	}
	modprobe("s3c2410_wdt");

	g_wd_fd = open("/dev/watchdog", O_WRONLY | O_CLOEXEC);
	/* L4 fix: same fd-escape treatment as kmsg - console open is expected to
	 * fail on this device, so fds 0-2 are free and g_wd_fd would otherwise
	 * land on fd 0. Not currently clobbered by anything, but this closes the
	 * asymmetry rather than leaving it latent for the next edit. */
	if (g_wd_fd >= 0 && g_wd_fd <= 2) {
		int moved = fcntl(g_wd_fd, F_DUPFD_CLOEXEC, 10);
		if (moved >= 0) { close(g_wd_fd); g_wd_fd = moved; }
	}
	if (g_wd_fd >= 0) {
		int timeout = 0;
		if (ioctl(g_wd_fd, WDIOC_GETTIMEOUT, &timeout) == 0) {
			char m[64];
			snprintf(m, sizeof(m), "watchdog timeout=%ds", timeout);
			logf_uptime(m);
		} else {
			char m[64];
			snprintf(m, sizeof(m), "WDIOC_GETTIMEOUT FAILED errno=%d", errno);
			logf_uptime(m);
		}
		pet_watchdog();
		logf_uptime("opened /dev/watchdog immediately after s3c2410_wdt, first pet sent");
		/* Opus round-9 F-6 fix: this is the FIRST build in the entire V10-V22
		 * series where the watchdog is armed at probe time (F1 writes
		 * tmr_atboot=1 to match the bootloader's own cmdline policy - every
		 * prior build ran with the compiled default tmr_atboot=0, which
		 * DISARMS at probe). That means a reset observed under this build
		 * could now be a genuine watchdog bite rather than evidence about
		 * the original custom-init-vs-AOSP-init mystery, and nothing else
		 * in the log distinguishes the two causes - log it explicitly next
		 * to the timeout value so this isn't buried in a comment only.
		 * Also note: stock's real safety margin does not come from
		 * tmr_atboot's DT default at all - it comes from AOSP watchdogd
		 * calling WDIOC_SETTIMEOUT to a 30s window (confirmed live via
		 * dmesg's periodic "Watchdog cluster 0/1 keepalive!" cadence).
		 * This program never calls WDIOC_SETTIMEOUT, so it runs on the raw
		 * DT default (80s) once opened - not the same timeout stock uses,
		 * even though the arm-at-probe policy now matches. */
		logf_uptime("NOTE: tmr_atboot=1 arms watchdog at probe (1st build to do so)");
		logf_uptime("NOTE: running raw DT timeout, NOT stock watchdogd's 30s SETTIMEOUT");
	} else {
		logf_uptime("open /dev/watchdog FAILED");
	}

	/* Storage controller - V17 never loaded this at all, so its cache mount
	 * was doomed regardless of the device-node fix. */
	modprobe("ufs-exynos-core");

	if (wait_for_partition("sda33", 15)) {
		logf_uptime("sda33 appeared in /proc/partitions");
		pet_watchdog(); /* D5/F2: nothing pets across this blocking ext4 mount */
		if (mount("/dev/block/sda33", "/cache", "ext4", MS_SYNCHRONOUS, NULL) == 0) {
			pet_watchdog();
			g_cache_mounted = 1; /* P4 fix: track mount ownership independent
			                      * of logging health, so the pre-reboot
			                      * unmount below runs regardless of whether
			                      * flush_early_log() below succeeds. */

			/* Astra round-2 P2 fix (counter): the prior version's error
			 * handling defaulted `prev` to 0 for ANY failure - a genuinely
			 * missing file (ENOENT, legitimate on the very first boot) was
			 * indistinguishable from a real I/O error, a short read, or
			 * invalid content, and all of them silently reset the sequence
			 * to "1". Astra also found an unchecked integer overflow: an
			 * INT_MAX counter plus one wraps to INT_MIN and gets written
			 * out as a negative number. Fixed by: treating ENOENT alone as
			 * "legitimately zero", any other open/read failure or invalid/
			 * incomplete content as "unavailable - do not touch the file",
			 * reading to true EOF instead of one read() call, validating
			 * every character AND an upper bound before incrementing, and
			 * leaving the existing file completely untouched whenever the
			 * value can't be trusted (better to lose one attempt number
			 * than silently corrupt or reset a real sequence). */
			int boot_n = -1; /* -1 = counter unavailable, logged as such */
			{
				int prev = -1; /* -1 = unknown/untrustworthy, not "reset to 0" */
				int cfd = open("/cache/v24_boot_count.txt", O_RDONLY);
				if (cfd < 0 && errno == ENOENT) {
					prev = 0; /* legitimately the first boot */
				} else if (cfd >= 0) {
					static char cb[32];
					size_t total = 0;
					/* Astra round-3 finding 1 fix: the read loop's `break` on
					 * a real (non-EINTR) read() error is indistinguishable
					 * from a normal EOF `break` to the code after the loop -
					 * both just fall through with whatever bytes happened to
					 * land in `total`. Proven live: a read() that returns 2
					 * valid digit bytes then fails with EIO left `total==2`,
					 * which validated fine as "12" and got trusted as a real
					 * prior count, silently truncating a real "123456" to
					 * "12". Track genuine EOF explicitly and only accept the
					 * buffer's content when the loop ended via EOF, never via
					 * an I/O error. */
					int hit_eof = 0;
					for (;;) {
						if (total >= sizeof(cb) - 1) break; /* buffer full, not an error */
						ssize_t rn = read(cfd, cb + total, sizeof(cb) - 1 - total);
						if (rn < 0) { if (errno == EINTR) continue; break; /* real error - hit_eof stays 0 */ }
						if (rn == 0) { hit_eof = 1; break; }
						total += (size_t)rn;
					}
					close(cfd);
					if (hit_eof && total > 0 && total < sizeof(cb) - 1) {
						size_t dl = 0;
						while (dl < total && cb[dl] >= '0' && cb[dl] <= '9') dl++;
						int trailing_ok = (dl == total) || (dl == total - 1 && cb[dl] == '\n');
						/* Astra round-3 finding 6 fix: the previous 7-digit
						 * cap validated the STORED value's digit count but
						 * not the INCREMENTED candidate - 9999999 (7 digits,
						 * accepted) plus one is 10000000 (8 digits), which
						 * the next boot's read would then reject outright,
						 * permanently wedging the counter one boot after it
						 * silently wrote a value it could no longer parse
						 * back. Reject the increment here, before writing,
						 * if the candidate itself would exceed 7 digits. */
						if (dl > 0 && dl <= 7 && trailing_ok) {
							cb[dl] = 0;
							int parsed = atoi(cb);
							if (parsed < 9999999) prev = parsed;
							/* else: candidate would be 10000000 (8 digits) -
							 * leave prev at -1 (unavailable) rather than
							 * write a value the counter can't represent. */
						}
					}
				}
				if (prev >= 0) {
					int candidate = prev + 1;
					int tfd = open("/cache/v24_boot_count.txt.tmp", O_WRONLY | O_CREAT | O_TRUNC, 0644);
					if (tfd >= 0) {
						char wb[16];
						int wl = snprintf(wb, sizeof(wb), "%d", candidate);
						if (write_full(tfd, wb, (size_t)wl) == 0 && fsync(tfd) == 0) {
							close(tfd);
							if (rename("/cache/v24_boot_count.txt.tmp", "/cache/v24_boot_count.txt") == 0) {
								boot_n = candidate;
							}
						} else {
							close(tfd);
						}
					}
				}
			}
			char banner[96];
			if (boot_n >= 0) {
				snprintf(banner, sizeof(banner), "[v24] ===== BOOT ATTEMPT #%d (V24/cinit13) =====\n", boot_n);
			} else {
				snprintf(banner, sizeof(banner), "[v24] ===== BOOT ATTEMPT #unknown, counter unavailable/untrustworthy (V24/cinit13) =====\n");
			}

			/* Astra round-2 P2 fix (ordering): queuing the banner into
			 * early_log before calling flush_early_log() does NOT make it
			 * appear first - log_line() appends at early_log_n and
			 * flush_early_log() walks forward from index 0, so the banner
			 * (queued last) is flushed LAST, still after every one of this
			 * attempt's own early lines (proven on-device: a harness of the
			 * actual queue-then-flush sequence produced "EARLY FIRST /
			 * EARLY SECOND / ===== BOOT ATTEMPT ====="). Fixed by writing
			 * the banner directly to the log file FIRST, via its own
			 * open+write_full+fsync, bypassing early_log entirely - then
			 * flush_early_log() appends this attempt's buffered pre-mount
			 * lines after it. This achieves true "banner first" at the
			 * actual file level, which queue-then-flush structurally could
			 * not. */
			/* Astra round-4 finding 3 fix: g_cache_ok was previously set to 1
			 * AFTER close(fd) returned, which left a gap between "write+
			 * fsync confirmed the banner durable" and "logging marked
			 * enabled" - a real SIGABRT delivered in exactly that gap (right
			 * after close(), before the flag assignment) still found
			 * g_cache_ok==0 and had nowhere persistent to write, proven
			 * live. Moved the assignment to happen in the same scope as the
			 * success determination, before close() - close()ing our own fd
			 * doesn't need g_cache_ok to be true, so there is no longer any
			 * gap between "banner durable" and "logging enabled" for a
			 * signal to land in. */
			int banner_ok = 0;
			{
				int fd = open("/cache/v24_log.txt", O_WRONLY | O_CREAT | O_APPEND, 0644);
				if (fd >= 0) {
					if (write_full(fd, banner, strlen(banner)) == 0 && fsync(fd) == 0) {
						banner_ok = 1;
						g_cache_ok = 1;
					}
					close(fd);
				}
			}

			/* D4 fix: only claim cache is usable for further logging after
			 * a confirmed create+write succeeds, not merely after mount()
			 * succeeds. Flush attempted regardless of banner_ok - a banner
			 * failure shouldn't also discard this attempt's actual early
			 * evidence lines. */
			int flush_ok = flush_early_log();
			if (flush_ok) {
				g_cache_ok = 1;
				if (banner_ok) {
					logf_uptime("cache mounted OK, banner + early log flushed and confirmed written");
				} else {
					logf_uptime("cache mounted OK, early log flushed but BANNER WRITE FAILED - attempt numbering may be missing for this run");
				}
			} else if (banner_ok) {
				logf_uptime("cache mounted OK, banner written, but flush_early_log() FAILED - early pre-mount lines lost, live logging continues");
			} else {
				logf_uptime("cache mounted but flush_early_log() FAILED - staying on in-memory log only");
			}
		} else {
			char m[64];
			snprintf(m, sizeof(m), "cache mount FAILED errno=%d", errno);
			logf_uptime(m);
		}
	} else {
		logf_uptime("sda33 never appeared after 15s wait - UFS probe likely failed");
	}

	/* USB chain. Deliberately does NOT include sec_reboot - it would
	 * override the clean built-in PSCI restart/poweroff handlers with
	 * Samsung's power-key-wait/5-retry path, unnecessary for this test.
	 * NOTE (L1 correction): exynos-reboot.ko IS pulled in transitively by
	 * ufs-exynos-core, dwc3-exynos-usb, and usb_notifier (confirmed via the
	 * real modules.dep closure) and it unconditionally overwrites
	 * pm_power_off. PSCI still wins the RESTART handler race (priority 129
	 * vs exynos-reboot's 128, confirmed in both drivers' source), so the
	 * timing-oracle reboot() call below is unaffected - but the file's
	 * previous claim of a "clean" PSCI-only handler chain was not quite
	 * accurate; only the restart path (not poweroff) is actually PSCI-only
	 * here. Also note (N3): CONFIG_S3C2410_SHUTDOWN_REBOOT=y rearms a
	 * SEPARATE 30s watchdog on any reboot()-triggered device_shutdown(),
	 * independent of whether this program ever opened /dev/watchdog itself -
	 * a stall between reboot() and actual PSCI restart could produce a
	 * watchdog reset that looks like "PSCI restart" in the log but isn't. */
	static const char *usb_mods[] = {
		"phy-exynos-usbdrd-super","dwc3-exynos-usb","usb_typec_manager",
		"usb_notify_layer","usb_notifier","usb_f_conn_gadget", NULL
	};
	for (int i = 0; usb_mods[i]; i++) modprobe(usb_mods[i]);

	logf_uptime("module load pass complete");

	/* Bound-device sanity check for the drivers that matter most - verified
	 * driver/device-instance name pairs against the real phone. D8 fix:
	 * platform_bound() now returns 3 states (0=no driver dir at all,
	 * 1=driver registered but this device not bound, 2=bound) instead of
	 * collapsing the first two into the same "bound=0". L7 fix: iterate by
	 * sizeof(checks)/sizeof(checks[0]) instead of a hardcoded count. */
	static const struct { const char *drv; const char *dev; } checks[] = {
		{ "s3c2410-wdt",       "10050000.watchdog_cl0" },
		{ "exynos-dwc3",       "10b00000.usb"          },
		{ "exynos-ufs",        "11100000.ufs"          },
		{ "phy_exynos_usbdrd", "10aa0000.phy"          },
	};
	for (size_t i = 0; i < sizeof(checks) / sizeof(checks[0]); i++) {
		char m[100];
		snprintf(m, sizeof(m), "platform driver %s/%s bound_state=%d (0=nodrv 1=unbound 2=bound)",
		         checks[i].drv, checks[i].dev, platform_bound(checks[i].drv, checks[i].dev));
		logf_uptime(m);
	}

	/* Decisive timing oracle: absolute monotonic deadline from true t=0
	 * (kernel boot), not a relative checkpoint taken after setup work.
	 * If the observed physical reset lands at true uptime ~45s (visible in
	 * the persisted log's own timestamps up to the last line before reset),
	 * PID1 was alive and healthy that whole time - the "crash-loop" framing
	 * is wrong and something else has its own independent schedule.
	 * reboot(RESTART) works with zero modules via the built-in PSCI restart
	 * handler - verified present on this device's DT and kernel config.
	 * L2 fix: if setup already overran the deadline, say so explicitly
	 * instead of silently skipping straight to the reboot line, which could
	 * otherwise be misread as "died before the heartbeat loop". */
	if (now_mono() >= 45.0) {
		char m[64];
		snprintf(m, sizeof(m), "deadline already passed at t=%.3fs, skipping heartbeat loop", now_mono());
		logf_uptime(m);
	}
	while (now_mono() < 45.0) {
		pet_watchdog();
		logf_uptime("heartbeat");
		sleep(1);
	}

	{
		char m[140];
		snprintf(m, sizeof(m), "reached absolute t=45s alive, last successful pet at t=%.3fs - self-rebooting now (PSCI restart)", g_last_pet_ok);
		logf_uptime(m);
	}

	/* D5 fix, hardened per Astra round-1 P4: explicitly unmount /cache before
	 * rebooting instead of relying on sync() alone. sync() does not clear
	 * ext4's needs_recovery flag - only a clean unmount does - so every
	 * prior build left the partition needing journal recovery on every
	 * single crash-loop iteration; one bad recovery could make a future
	 * mount() fail and silently lose that run's entire log.
	 *
	 * Gated on g_cache_mounted (mount ownership), not g_cache_ok (current
	 * logging health) - previously a log-write failure that cleared
	 * g_cache_ok also skipped this unmount entirely, leaving the partition
	 * mounted-and-dirty for no reason. After a successful unmount, clear
	 * BOTH flags so nothing (including the fatal-signal handler, if a
	 * signal fires during the reboot()/retry sequence below) tries to write
	 * to a filesystem that is no longer mounted. */
	if (g_cache_mounted) {
		logf_uptime("unmounting /cache before reboot");
		sync();
		/* Astra round-2 P3 fix (race): disable cache logging BEFORE
		 * attempting the unmount, not after. Previously a signal delivered
		 * between a successful umount() syscall and the flag-clear line
		 * below still found g_cache_ok==1 and let fatal_sig_handler()
		 * O_CREAT a fresh log file underneath the now-detached ramdisk
		 * tmpfs directory - a write that "succeeds" but goes nowhere
		 * persistent. Proven on-device: Astra delivered SIGABRT immediately
		 * after a real successful umount() syscall and confirmed the
		 * write landed in the underlying ramdisk, not on the unmounted
		 * ext4 device. Disabling here closes that window entirely, since
		 * there is no longer a gap between "umount succeeded" and "logging
		 * disabled" for a signal to land in. */
		g_cache_ok = 0;
		int un = umount("/cache");
		if (un == 0) {
			g_cache_mounted = 0;
		} else if (g_kmsg_fd >= 0) {
			/* Astra round-2 P4 fix: previously BOTH flags were cleared
			 * unconditionally even when umount() FAILED (reproduced live
			 * via a real EBUSY from a busy mountpoint), falsely claiming
			 * the cache was unmounted while it was, in fact, still mounted
			 * and perfectly writable. g_cache_mounted is now only cleared
			 * on a confirmed successful unmount. g_cache_ok stays disabled
			 * either way, by design (see above) - this failure is reported
			 * through kmsg, the only channel this shutdown path still
			 * trusts once it has committed to tearing cache logging down. */
			char kb[100];
			int kn = snprintf(kb, sizeof(kb), "[v24] umount(/cache) FAILED errno=%d, cache remains mounted\n", errno);
			ssize_t w = write(g_kmsg_fd, kb, kn);
			(void)w;
		}
	} else {
		sync();
	}

	/* P4 fix: reboot() failing is not itself expected (PSCI's restart
	 * handler is registered and verified present via kernel config/DT), but
	 * the prior code's response - petting forever with zero forward
	 * progress and no further evidence - gave no observable signal that
	 * this specific, unlikely failure mode occurred at all. Retry with a
	 * bounded backoff, logging each attempt to kmsg (the only channel left
	 * once /cache is unmounted), instead of one silent attempt followed by
	 * an indefinite, uninformative petting loop. */
	int attempt = 0;
	for (;;) {
		int rc = reboot(LINUX_REBOOT_CMD_RESTART);
		if (rc == 0) break; /* should not return on success, but be safe */
		attempt++;
		if (g_kmsg_fd >= 0) {
			char kb[80];
			int kn = snprintf(kb, sizeof(kb), "[v24] reboot() FAILED errno=%d attempt=%d, retrying\n", errno, attempt);
			ssize_t w = write(g_kmsg_fd, kb, kn);
			(void)w;
		}
		for (int i = 0; i < 15; i++) { pet_watchdog(); sleep(1); }
	}

	for (;;) { pet_watchdog(); sleep(5); }
	return 0;
}
