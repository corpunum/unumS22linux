/*
 * AOSP first-stage preserving init wrapper.
 *
 * The ramdisk keeps /init as the shipping first-stage entrypoint. The
 * shipping regular binary is renamed to /system/bin/init.android and this
 * program takes its old pathname. Every invocation is delegated unchanged except the
 * selinux_setup transition, which is native-enabled only when the explicit
 * /native-enable marker exists.
 */
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/reboot.h>
#include <sys/syscall.h>
#include <linux/reboot.h>
#include <linux/watchdog.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

static const char *const kOriginal = "/system/bin/init.android";
static const char *const kGuardian = "/system/bin/native-guardian";
static const char *const kEnable = "/native-enable";

extern char **environ;

static void log_message(const char *fmt, ...) {
  char message[768];
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(message, sizeof(message), fmt, ap);
  va_end(ap);
  if (n < 0) return;
  if ((size_t)n >= sizeof(message)) n = (int)sizeof(message) - 1;

  int fd = open("/dev/kmsg", O_WRONLY | O_CLOEXEC);
  if (fd >= 0) {
    (void)dprintf(fd, "native-init-wrapper: %.*s\n", n, message);
    close(fd);
  }
  fd = open("/native/handoff.log", O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC,
            0600);
  if (fd >= 0) {
    (void)dprintf(fd, "wrapper: %.*s\n", n, message);
    close(fd);
  }
}

static int enabled(void) {
  struct stat st;
  return stat(kEnable, &st) == 0 && S_ISREG(st.st_mode);
}

#ifdef S22_BOOT_RECOVERY_FALLBACK
static void restart_recovery(void) {
  static const char target[] = "recovery";
  int wd = open("/dev/watchdog", O_WRONLY | O_CLOEXEC);
  for (int attempt = 1; attempt <= 3; ++attempt) {
    sync();
    long rc = syscall(SYS_reboot, LINUX_REBOOT_MAGIC1, LINUX_REBOOT_MAGIC2,
                      LINUX_REBOOT_CMD_RESTART2, target);
    log_message("BOOT recovery restart attempt %d returned rc=%ld errno=%d (%s)",
                attempt, rc, errno, strerror(errno));
    if (wd >= 0) (void)ioctl(wd, WDIOC_KEEPALIVE, 0);
    sleep(1);
  }
  log_message("BOOT recovery restart unavailable; refusing Android delegation");
  for (;;) {
    if (wd >= 0) (void)ioctl(wd, WDIOC_KEEPALIVE, 0);
    sleep(1);
  }
}
#endif

static int restore_aosp_init_path(void) {
  struct stat st;
  if (stat("/system/bin/init.wrapper", &st) == 0) {
    log_message("refusing fallback: /system/bin/init.wrapper already exists");
    return -1;
  }
  if (rename("/system/bin/init", "/system/bin/init.wrapper") != 0) {
    log_message("rename wrapper aside failed: errno=%d (%s)", errno, strerror(errno));
    return -1;
  }
  if (rename(kOriginal, "/system/bin/init") != 0) {
    int saved = errno;
    (void)rename("/system/bin/init.wrapper", "/system/bin/init");
    errno = saved;
    log_message("restore original /system/bin/init failed: errno=%d (%s)", errno,
                strerror(errno));
    return -1;
  }
  log_message("restored original AOSP /system/bin/init before delegate");
  return 0;
}

static int delegate_original(int argc, char **argv) {
  (void)argc;
  execve(kOriginal, argv, environ);
  log_message("exec %s failed: errno=%d (%s)", kOriginal, errno, strerror(errno));
  return 127;
}

int main(int argc, char **argv) {
  if (argc > 1 && strcmp(argv[1], "selinux_setup") == 0 && enabled()) {
    char *guardian_argv[] = {(char *)kGuardian, NULL};
    log_message("intercepting selinux_setup; native marker is present");
    execve(kGuardian, guardian_argv, environ);
    log_message("exec %s failed: errno=%d (%s)", kGuardian, errno, strerror(errno));
#ifdef S22_BOOT_RECOVERY_FALLBACK
    if (enabled()) restart_recovery();
#else
    log_message("falling back to AOSP");
#endif
  }
  if (restore_aosp_init_path() == 0) {
    execve("/system/bin/init", argv, environ);
    log_message("exec restored /system/bin/init failed: errno=%d (%s)", errno,
                strerror(errno));
  }
  return delegate_original(argc, argv);
}
