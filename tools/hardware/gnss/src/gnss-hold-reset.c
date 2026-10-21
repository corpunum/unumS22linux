/* gnss-hold-reset: put the Kepler GNSS core back into reset (GNSS_IOCTL_RESET, 0x4b00)
 * and print gnss_status. Use after gpsd exits: closing /dev/gnss_ipc does not power Kepler off.
 * usage: gnss-hold-reset [--status-only] */
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

static void status(const char *when) {
    char b[64] = {0};
    int fd = open("/sys/devices/platform/gnssif/gnss_status", O_RDONLY);
    if (fd >= 0) {
        ssize_t n = read(fd, b, sizeof(b) - 1);
        close(fd);
        if (n > 0 && b[n - 1] == '\n') b[n - 1] = 0;
    }
    printf("gnss_status %s: %s\n", when, b[0] ? b : "?");
}

int main(int argc, char **argv) {
    status("before");
    if (argc > 1 && !strcmp(argv[1], "--status-only")) return 0;
    int fd = open("/dev/gnss_ipc", O_RDWR);
    if (fd < 0) {
        perror("open /dev/gnss_ipc");
        return 1;
    }
    int r = ioctl(fd, 0x4b00, 0);
    printf("GNSS_IOCTL_RESET -> %d\n", r);
    close(fd);
    status("after");
    return r ? 1 : 0;
}
