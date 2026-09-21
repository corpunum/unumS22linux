/* Host-only S5E9925 VS4L NPU BOOTUP ABI verifier; no device access. */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <stddef.h>
#define NPU_HWDEV_ID_NPU 0x2u
struct vs4l_ctrl { uint32_t ctrl, value, mem_size, mem_addr, mem_addr_h; int32_t reserved; };
#define VS4L_VERTEXIOC_BOOTUP _IOWR('V', 13, struct vs4l_ctrl)
_Static_assert(sizeof(struct vs4l_ctrl) == 24, "vs4l_ctrl ABI size");
_Static_assert(offsetof(struct vs4l_ctrl, value) == 4, "vs4l_ctrl value offset");
_Static_assert(VS4L_VERTEXIOC_BOOTUP == 0xc018560dUL, "VS4L BOOTUP ioctl ABI");
static void usage(const char *n) { fprintf(stderr, "usage: %s --dry-run | %s --execute [device]\n", n, n); }
int main(int argc, char **argv) {
 struct vs4l_ctrl c = {0};
 if (argc == 2 && !strcmp(argv[1], "--dry-run")) { printf("abi=vs4l_ctrl size=%zu ioctl=0x%lx ctrl=0x0 value=0x2 mem=0\n", sizeof(c), (unsigned long)VS4L_VERTEXIOC_BOOTUP); return 0; }
 if (argc < 2 || argc > 3 || strcmp(argv[1], "--execute")) { usage(argv[0]); return 2; }
 (void)c;
 fprintf(stderr, "refusing --execute: BOOTUP has unbounded kernel waits and is not authorized by this host artifact\n");
 return 3;
}
