/* S22-only diagnostic libdrm interposer; no kernel or partition changes.
 * The pinned Samsung DPP uses fb->width for RDMA_SRC_WIDTH, ignoring linear
 * RGB pitches. Register the padded width but leave the compositor's visible
 * plane source at 1080. Enable only in the compositor: S22_LINEAR_STRIDE=1.
 * See s22-linear-stride.md for the source contract and deployment status.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <xf86drm.h>
#include <xf86drmMode.h>
#include <drm_fourcc.h>

static uint32_t scanout_width(const char *driver, uint32_t width,
                             uint32_t height, uint32_t format,
                             const uint32_t handles[4],
                             const uint32_t pitches[4],
                             const uint32_t offsets[4],
                             const uint64_t modifiers[4], uint32_t flags) {
    /* Deliberately not a generic format/stride policy. Unknown layouts pass
     * through unchanged. No buffer contents or pitch values are modified. */
    if (!driver || strcmp(driver, "exynos-drmdpu") || width != 1080 ||
        height != 2340 || format != DRM_FORMAT_XRGB8888 ||
        !handles || !pitches || !offsets || !handles[0] ||
        pitches[0] != 4352 || offsets[0] != 0 ||
        (flags & ~DRM_MODE_FB_MODIFIERS) ||
        ((flags & DRM_MODE_FB_MODIFIERS) && !modifiers))
        return width;
    for (unsigned i = 0; i < 4; ++i) {
        if (modifiers && modifiers[i] != DRM_FORMAT_MOD_LINEAR)
            return width;
        if (i && (handles[i] || pitches[i] || offsets[i]))
            return width;
    }
    return 1088;
}

#ifndef S22_STRIDE_UNIT_TEST
static uint32_t checked_width(int fd, uint32_t width, uint32_t height,
                              uint32_t format, const uint32_t handles[4],
                              const uint32_t pitches[4], const uint32_t offsets[4],
                              const uint64_t modifiers[4], uint32_t flags) {
    const char *enabled = getenv("S22_LINEAR_STRIDE");
    if (!enabled || strcmp(enabled, "1"))
        return width;
    drmVersionPtr (*version_fn)(int) = dlsym(RTLD_NEXT, "drmGetVersion");
    void (*free_fn)(drmVersionPtr) = dlsym(RTLD_NEXT, "drmFreeVersion");
    if (!version_fn || !free_fn)
        return width;
    drmVersionPtr v = version_fn(fd);
    if (!v)
        return width;
    uint32_t result = scanout_width(v->name, width, height, format,
                                    handles, pitches, offsets, modifiers, flags);
    free_fn(v);
    return result;
}

int drmModeAddFB2(int fd, uint32_t width, uint32_t height, uint32_t format,
                  const uint32_t handles[4], const uint32_t pitches[4],
                  const uint32_t offsets[4], uint32_t *id, uint32_t flags) {
    typeof(&drmModeAddFB2) real = dlsym(RTLD_NEXT, "drmModeAddFB2");
    if (!real) { errno = ENOSYS; return -ENOSYS; }
    uint32_t padded = checked_width(fd, width, height, format, handles,
                                    pitches, offsets, NULL, flags);
    int ret = real(fd, padded, height, format, handles, pitches, offsets, id, flags);
    if (!ret && padded != width)
        fprintf(stderr, "s22-linear-stride: fb=%u width=%u->%u pitch=%u\n",
                *id, width, padded, pitches[0]);
    return ret;
}

int drmModeAddFB2WithModifiers(int fd, uint32_t width, uint32_t height,
                  uint32_t format, const uint32_t handles[4],
                  const uint32_t pitches[4], const uint32_t offsets[4],
                  const uint64_t modifiers[4], uint32_t *id, uint32_t flags) {
    typeof(&drmModeAddFB2WithModifiers) real =
        dlsym(RTLD_NEXT, "drmModeAddFB2WithModifiers");
    if (!real) { errno = ENOSYS; return -ENOSYS; }
    uint32_t padded = checked_width(fd, width, height, format, handles,
                                    pitches, offsets, modifiers, flags);
    int ret = real(fd, padded, height, format, handles, pitches, offsets,
                   modifiers, id, flags);
    if (!ret && padded != width)
        fprintf(stderr, "s22-linear-stride: fb=%u width=%u->%u pitch=%u\n",
                *id, width, padded, pitches[0]);
    return ret;
}
#endif
