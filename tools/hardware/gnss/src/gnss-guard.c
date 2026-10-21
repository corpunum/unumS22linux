/* gnss-guard: run a command under a seccomp filter that blocks the two gpsd
 * paths that reach outside GNSS, plus reboot and module loading.
 *
 *   ioctl(*, 0x4b10)  GNSS_IOCTL_SET_WATCHDOG_RESET -> dbg_snapshot_expire_watchdog(): whole-phone reset
 *   ioctl(*, 0x6f27)  IOCTL_GET_CP_STATUS on umts_boot0: also clears cpif's sim_state.changed
 *   reboot, kexec_load, kexec_file_load, init_module, finit_module, delete_module
 *
 * All of them fail with EPERM (visible in strace). The filter is inherited by every
 * child and survives execve (PR_SET_NO_NEW_PRIVS), so it covers gpsd, the HAL client
 * and anything they start. Static, no libc dependencies beyond syscalls.
 *
 * usage: gnss-guard CMD [ARGS...]      (gnss-guard --selftest: try the blocked calls)
 */
#include <errno.h>
#include <fcntl.h>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <unistd.h>

#define WATCHDOG_RESET 0x4b10u
#define CP_STATUS 0x6f27u

#define ARG1_LO offsetof(struct seccomp_data, args[1])
#define DENY (SECCOMP_RET_ERRNO | (EPERM & SECCOMP_RET_DATA))

static int install(void) {
    struct sock_filter f[] = {
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, arch)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_AARCH64, 1, 0),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_reboot, 7, 0),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_kexec_load, 6, 0),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_kexec_file_load, 5, 0),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_init_module, 4, 0),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_finit_module, 3, 0),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_delete_module, 2, 0),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_ioctl, 2, 0),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
        BPF_STMT(BPF_RET | BPF_K, DENY),
        /* ioctl: compare the low 32 bits of the request (an unsigned int in the kernel) */
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, ARG1_LO),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, WATCHDOG_RESET, 1, 0),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, CP_STATUS, 0, 1),
        BPF_STMT(BPF_RET | BPF_K, DENY),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
    };
    struct sock_fprog prog = {.len = sizeof(f) / sizeof(f[0]), .filter = f};
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) return -1;
    return prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &prog);
}

static int selftest(void) {
    /* /dev/null rejects both requests with ENOTTY when they get through; EPERM proves the filter */
    int fd = open("/dev/null", O_RDONLY), bad = 0;
    long r = ioctl(fd, WATCHDOG_RESET, 0);
    printf("ioctl 0x4b10 -> %ld errno %d (%s)\n", r, errno, errno == EPERM ? "blocked" : "NOT BLOCKED");
    bad |= errno != EPERM;
    r = ioctl(fd, CP_STATUS, 0);
    printf("ioctl 0x6f27 -> %ld errno %d (%s)\n", r, errno, errno == EPERM ? "blocked" : "NOT BLOCKED");
    bad |= errno != EPERM;
    errno = 0;
    r = ioctl(fd, 0x4b11, 0);
    printf("ioctl 0x4b11 -> %ld errno %d (%s)\n", r, errno, errno != EPERM ? "passes" : "WRONGLY BLOCKED");
    bad |= errno == EPERM;
    /* reboot with bad magic: the kernel returns EINVAL if it is not filtered */
    r = syscall(__NR_reboot, 0, 0, 0, 0);
    printf("reboot(bad magic) -> %ld errno %d (%s)\n", r, errno, errno == EPERM ? "blocked" : "NOT BLOCKED");
    bad |= errno != EPERM;
    return bad;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: gnss-guard CMD [ARGS...] | --selftest\n");
        return 2;
    }
    if (install()) {
        perror("gnss-guard: seccomp");
        return 3;
    }
    if (!strcmp(argv[1], "--selftest")) return selftest();
    execvp(argv[1], argv + 1);
    perror("gnss-guard: exec");
    return 127;
}
