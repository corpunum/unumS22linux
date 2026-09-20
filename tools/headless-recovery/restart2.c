/*
 * Tiny static ARM64 Linux selector for the Samsung downstream kernel.
 *
 * This calls Linux RESTART2 directly with the literal target consumed by the
 * matching sec_reboot notifier.  It never writes Android BCB/MISC and accepts
 * only "recovery", "download", or "normal". The normal target is for the
 * separately authorized native BOOT install. Do not use it from Android.
 */
#include <errno.h>
#include <linux/reboot.h>
#include <stdio.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>

static void usage(const char *argv0) {
  dprintf(STDOUT_FILENO, "usage: %s recovery|download|normal\n", argv0);
  dprintf(STDOUT_FILENO, "direct Linux RESTART2 selector; no BCB/MISC writes\n");
}

int main(int argc, char **argv) {
  if (argc != 2 || (strcmp(argv[1], "recovery") != 0 &&
                    strcmp(argv[1], "download") != 0 &&
                    strcmp(argv[1], "normal") != 0)) {
    usage(argv[0]);
    return argc == 2 ? 2 : 1;
  }
  /* syscall() is used deliberately: no libc reboot wrapper or init path. */
  long rc = syscall(SYS_reboot, LINUX_REBOOT_MAGIC1, LINUX_REBOOT_MAGIC2,
                    LINUX_REBOOT_CMD_RESTART2, argv[1]);
  dprintf(STDERR_FILENO, "RESTART2 %s failed: errno=%d (%s)\n", argv[1],
          errno, strerror(errno));
  return rc == 0 ? 0 : 1;
}
