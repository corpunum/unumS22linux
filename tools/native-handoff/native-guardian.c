/*
 * PID 1 rescue guardian for the native handoff.
 *
 * It owns the one watchdog fd, supervises the recovery-side native-start
 * process, and keeps the original AOSP selinux_setup fallback available for
 * native startup failure.  A missing host ACK after the grace period does not
 * transition away from a functioning native Linux session.
 */
#include <errno.h>
#include <fcntl.h>
#include <linux/watchdog.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static const char *const kNativeStart = "/native/native-start";
static const char *const kNativeLoader = "/native/lib/ld-musl-aarch64.so.1";
static const char *const kNativeBusybox = "/native/bin/busybox";
static const char *const kAospInit = "/system/bin/init.android";
static const char *const kReady = "/run/native-ready";
static const char *const kGadgetOwned = "/run/native-gadget-owned";
static const char *const kLog = "/native/handoff.log";
static const int kDeadlineSeconds = 120;

extern char **environ;

static int watchdog_fd = -1;
static pid_t native_pid = -1;
static int ack_confirmed = 0;
static int timeout_elapsed = 0;
static int watchdog_failures = 0;

static void log_message(const char *fmt, ...) {
  char message[1024];
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(message, sizeof(message), fmt, ap);
  va_end(ap);
  if (n < 0) return;
  if ((size_t)n >= sizeof(message)) n = (int)sizeof(message) - 1;

  int fd = open("/dev/kmsg", O_WRONLY | O_CLOEXEC);
  if (fd >= 0) {
    (void)dprintf(fd, "native-guardian: %.*s\n", n, message);
    close(fd);
  }
  fd = open(kLog, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
  if (fd >= 0) {
    (void)dprintf(fd, "guardian: %.*s\n", n, message);
    close(fd);
  }
}

static int monotonic_seconds(void) {
  struct timespec ts;
  if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return 0;
  return (int)ts.tv_sec;
}

static int ensure_dir(const char *path) {
  struct stat st;
  if (stat(path, &st) == 0) return S_ISDIR(st.st_mode) ? 0 : -1;
  if (errno != ENOENT) return -1;
  return mkdir(path, 0755) == 0 || errno == EEXIST ? 0 : -1;
}

static void ensure_runtime_dirs(void) {
  (void)mkdir("/native", 0755);
  (void)mkdir("/tmp", 0755);
  (void)mkdir("/run", 0755);
}

static int ensure_watchdog_node(void) {
  if (ensure_dir("/dev") != 0) {
    log_message("/dev is not a directory");
    return -1;
  }
  struct stat st;
  if (stat("/dev/watchdog", &st) == 0) {
    if (!S_ISCHR(st.st_mode) || major(st.st_rdev) != 10 || minor(st.st_rdev) != 130) {
      log_message("existing /dev/watchdog has unexpected device identity");
      return -1;
    }
    return 0;
  }
  if (errno != ENOENT || mknod("/dev/watchdog", S_IFCHR | 0600, makedev(10, 130)) != 0) {
    log_message("mknod /dev/watchdog failed: errno=%d (%s)", errno, strerror(errno));
    return -1;
  }
  return 0;
}

static int open_watchdog(void) {
  if (ensure_watchdog_node() != 0) return -1;
  watchdog_fd = open("/dev/watchdog", O_WRONLY | O_CLOEXEC);
  if (watchdog_fd < 0) {
    log_message("open /dev/watchdog failed: errno=%d (%s)", errno, strerror(errno));
    return -1;
  }
  int timeout = 30;
  if (ioctl(watchdog_fd, WDIOC_SETTIMEOUT, &timeout) != 0) {
    log_message("WDIOC_SETTIMEOUT(30) failed: errno=%d (%s)", errno, strerror(errno));
    close(watchdog_fd);
    watchdog_fd = -1;
    return -1;
  }
  log_message("watchdog owned with timeout=%d seconds", timeout);
  return 0;
}

static int pet_watchdog(void) {
  if (watchdog_fd < 0) return 0;
  if (ioctl(watchdog_fd, WDIOC_KEEPALIVE, 0) == 0) {
    watchdog_failures = 0;
    return 0;
  }
  watchdog_failures++;
  log_message("WDIOC_KEEPALIVE failed (%d): errno=%d (%s)", watchdog_failures, errno,
              strerror(errno));
  return watchdog_failures >= 3 ? -1 : 0;
}

static int disable_watchdog(void) {
  if (watchdog_fd < 0) return 0;
  int options = WDIOS_DISABLECARD;
  int rc = ioctl(watchdog_fd, WDIOC_SETOPTIONS, &options);
  if (rc != 0) {
    log_message("WDIOC_DISABLECARD failed: errno=%d (%s)", errno, strerror(errno));
  }
  ssize_t magic_close_result = write(watchdog_fd, "V", 1);
  (void)magic_close_result;
  close(watchdog_fd);
  watchdog_fd = -1;
  return rc;
}

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
  if (rename(kAospInit, "/system/bin/init") != 0) {
    int saved = errno;
    (void)rename("/system/bin/init.wrapper", "/system/bin/init");
    errno = saved;
    log_message("restore original /system/bin/init failed: errno=%d (%s)", errno,
                strerror(errno));
    return -1;
  }
  log_message("restored original AOSP /system/bin/init before fallback");
  return 0;
}

static void write_text(const char *path, const char *text) {
  int fd = open(path, O_WRONLY | O_TRUNC | O_CLOEXEC);
  if (fd < 0) return;
  ssize_t write_result = write(fd, text, strlen(text));
  (void)write_result;
  close(fd);
}

static void cleanup_gadget(void) {
  struct stat st;
  if (stat(kGadgetOwned, &st) != 0) return;

  write_text("/config/usb_gadget/g1/UDC", "none\n");
  (void)unlink("/config/usb_gadget/g1/configs/b.1/f1");
  (void)rmdir("/config/usb_gadget/g1/functions/ecm.usb0");
  (void)rmdir("/config/usb_gadget/g1/configs/b.1/strings/0x409");
  (void)rmdir("/config/usb_gadget/g1/configs/b.1");
  (void)rmdir("/config/usb_gadget/g1/strings/0x409");
  (void)rmdir("/config/usb_gadget/g1");
  (void)unlink(kGadgetOwned);
  log_message("native gadget cleanup attempted before fallback");
}

static void terminate_native(void) {
  if (native_pid <= 0) return;
  (void)kill(-native_pid, SIGTERM);
  for (int i = 0; i < 10; ++i) {
    if (waitpid(native_pid, NULL, WNOHANG) == native_pid) {
      native_pid = -1;
      return;
    }
    usleep(100000);
  }
  (void)kill(-native_pid, SIGKILL);
  (void)waitpid(native_pid, NULL, 0);
  native_pid = -1;
}

static void fallback_to_aosp(const char *reason) {
  log_message("native fallback: %s", reason);
  terminate_native();
  cleanup_gadget();
  if (restore_aosp_init_path() != 0) {
    log_message("cannot safely fallback while wrapper owns /system/bin/init");
    for (;;) {
      (void)pet_watchdog();
      sleep(1);
    }
  }
  if (disable_watchdog() != 0) {
    log_message("watchdog disable ioctl failed; proceeding after best-effort close");
  }
  char *argv[] = {(char *)"/system/bin/init", (char *)"selinux_setup", NULL};
  execve("/system/bin/init", argv, environ);
  log_message("exec restored /system/bin/init failed: errno=%d (%s)", errno,
              strerror(errno));
  _exit(127);
}

static pid_t start_native(int resume) {
  pid_t pid = fork();
  if (pid < 0) {
    log_message("fork failed: errno=%d (%s)", errno, strerror(errno));
    return -1;
  }
  if (pid == 0) {
    (void)setpgid(0, 0);
    int fd = open(kLog, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
    if (fd >= 0) {
      (void)dup2(fd, STDOUT_FILENO);
      (void)dup2(fd, STDERR_FILENO);
      close(fd);
    }
    int nullfd = open("/dev/null", O_RDONLY | O_CLOEXEC);
    if (nullfd >= 0) {
      (void)dup2(nullfd, STDIN_FILENO);
      close(nullfd);
    }
    char *argv[] = {(char *)kNativeLoader, (char *)kNativeBusybox, (char *)"sh",
                    (char *)kNativeStart, resume ? (char *)"resume" : NULL, NULL};
    execve(kNativeLoader, argv, environ);
    dprintf(STDERR_FILENO, "native-start exec failed: errno=%d (%s)\n", errno,
            strerror(errno));
    _exit(127);
  }
  (void)setpgid(pid, pid);
  log_message("started native-start pid=%d resume=%d", (int)pid, resume);
  return pid;
}

static int marker_exists(const char *path) {
  struct stat st;
  return stat(path, &st) == 0 && S_ISREG(st.st_mode);
}

static int reap_children(void) {
  int tracked_exited = 0;
  for (;;) {
    int status = 0;
    pid_t pid = waitpid(-1, &status, WNOHANG);
    if (pid <= 0) {
      if (pid < 0 && errno != ECHILD && errno != EINTR) {
        log_message("waitpid(all) failed: errno=%d (%s)", errno, strerror(errno));
      }
      break;
    }
    if (pid == native_pid) {
      native_pid = -1;
      tracked_exited = 1;
      log_message("tracked native-start exited status=0x%x", status);
    } else {
      log_message("reaped untracked child pid=%d status=0x%x", (int)pid, status);
    }
  }
  return tracked_exited;
}

static void usage(const char *argv0) {
  dprintf(STDOUT_FILENO,
          "usage: %s [--help]\nPID 1 native guardian; native mode requires /native-enable.\n",
          argv0);
}

int main(int argc, char **argv) {
  if (argc > 1 && strcmp(argv[1], "--help") == 0) {
    usage(argv[0]);
    return 0;
  }
  if (getpid() != 1) log_message("warning: guardian pid=%d (expected PID 1)", (int)getpid());

  ensure_runtime_dirs();
  (void)unlink(kReady);
  (void)unlink(kGadgetOwned);
  if (open_watchdog() != 0) fallback_to_aosp("watchdog setup failed");

  native_pid = start_native(0);
  if (native_pid < 0) fallback_to_aosp("native-start could not be forked");
  int deadline = monotonic_seconds() + kDeadlineSeconds;

  for (;;) {
    if (pet_watchdog() != 0) {
      log_message("watchdog keepalive remains degraded; retaining native Linux");
    }
    if (!ack_confirmed && marker_exists(kReady)) {
      ack_confirmed = 1;
      log_message("host readiness ACK marker observed; native Linux is confirmed");
    }
    if (!timeout_elapsed && monotonic_seconds() >= deadline) {
      timeout_elapsed = 1;
      if (!ack_confirmed) {
        log_message("unacknowledged native SSH; remaining Linux");
      }
    }

    if (reap_children()) {
      if (!ack_confirmed && !timeout_elapsed) {
        fallback_to_aosp("native-start exited before host ACK");
      }
      log_message("native-start exited after startup grace; restarting SSH rescue");
      sleep(1);
      native_pid = start_native(1);
      if (native_pid < 0) {
        log_message("rescue restart failed; guardian remains alive");
        sleep(2);
      }
    }
    sleep(1);
  }
}
