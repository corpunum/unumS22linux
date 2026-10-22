/* Minimal libnativewindow.so for the isolated Bionic NPU runtime: the NNAPI SL
 * driver links AHardwareBuffer_*; we never use AHardwareBuffer memory, so all
 * calls fail cleanly (-ENOSYS). Version node LIBNATIVEWINDOW like Android's. */
#include <stddef.h>
#include <string.h>
typedef struct AHardwareBuffer AHardwareBuffer;
void AHardwareBuffer_acquire(AHardwareBuffer *b) { (void)b; }
void AHardwareBuffer_release(AHardwareBuffer *b) { (void)b; }
int AHardwareBuffer_allocate(const void *desc, AHardwareBuffer **out) { (void)desc; if (out) *out = NULL; return -38; }
void AHardwareBuffer_describe(const AHardwareBuffer *b, void *desc) { (void)b; if (desc) memset(desc, 0, 40); }
int AHardwareBuffer_lock(AHardwareBuffer *b, unsigned long long u, int fence, const void *rect, void **va) { (void)b; (void)u; (void)fence; (void)rect; if (va) *va = NULL; return -38; }
int AHardwareBuffer_unlock(AHardwareBuffer *b, int *fence) { (void)b; if (fence) *fence = -1; return -38; }
int AHardwareBuffer_getId(const AHardwareBuffer *b, unsigned long long *id) { (void)b; if (id) *id = 0; return -38; }
const void *AHardwareBuffer_getNativeHandle(const AHardwareBuffer *b) { (void)b; return NULL; }
