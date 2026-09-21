/* Per-process compatibility experiment, not a kernel repair or sandbox.
 * Return ENOSYS for close-range so libc can close descriptors individually without the broken kernel range loop.
 * No syscall is granted that another inherited filter disallows.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <unistd.h>

#if defined(__aarch64__)
#define EXPECTED_ARCH AUDIT_ARCH_AARCH64
#elif defined(__x86_64__)
#define EXPECTED_ARCH AUDIT_ARCH_X86_64
#else
#error Unsupported audit architecture
#endif

int main(int argc, char **argv)
{
    struct sock_filter instructions[] = {
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, arch)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, EXPECTED_ARCH, 1, 0),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_close_range, 0, 1),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | ENOSYS),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
    };
    struct sock_fprog program = {
        .len = sizeof(instructions) / sizeof(instructions[0]),
        .filter = instructions,
    };
    int self_test = argc == 2 && !strcmp(argv[1], "--self-test");
    if (!self_test && (argc < 3 || strcmp(argv[1], "--"))) {
        fprintf(stderr, "usage: %s --self-test | -- COMMAND [ARG...]\n", argv[0]);
        return 2;
    }
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0 ||
        prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &program) != 0) {
        perror("install close-range compatibility filter");
        return 3;
    }
    /* Reversed bounds cannot close descriptors even if the filter were missing. */
    errno = 0;
    long result = syscall(__NR_close_range, 1u, 0u, 0u);
    if (result != -1 || errno != ENOSYS ||
        prctl(PR_GET_SECCOMP, 0, 0, 0, 0) != SECCOMP_MODE_FILTER ||
        prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) != 1) {
        fprintf(stderr, "close-range filter verification failed\n");
        return 4;
    }
    if (self_test) {
        puts("close-range=ENOSYS seccomp=filter NoNewPrivs=1");
        return 0;
    }
    fprintf(stderr, "close-range compatibility filter verified; executing %s\n", argv[2]);
    fflush(stderr);
    execvp(argv[2], &argv[2]);
    perror("exec compatibility target");
    return 5;
}
