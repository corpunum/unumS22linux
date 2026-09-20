#define S22_STRIDE_UNIT_TEST
#include "s22-linear-stride.c"
#include <assert.h>

int main(void) {
    uint32_t handles[4] = {9}, pitches[4] = {4352}, offsets[4] = {0};
    uint64_t modifiers[4] = {0};
#define WIDTH(d,w,h,f,m,flags) scanout_width(d,w,h,f,handles,pitches,offsets,m,flags)
#define VALID WIDTH("exynos-drmdpu",1080,2340,DRM_FORMAT_XRGB8888,modifiers,DRM_MODE_FB_MODIFIERS)
    assert(VALID == 1088);
    assert(WIDTH("exynos-drmdpu",1080,2340,DRM_FORMAT_XRGB8888,NULL,0) == 1088);
    assert(WIDTH("amdgpu",1080,2340,DRM_FORMAT_XRGB8888,modifiers,0) == 1080);
    assert(WIDTH(NULL,1080,2340,DRM_FORMAT_XRGB8888,modifiers,0) == 1080);
    assert(WIDTH("exynos-drmdpu",1081,2340,DRM_FORMAT_XRGB8888,modifiers,0) == 1081);
    assert(WIDTH("exynos-drmdpu",1080,1080,DRM_FORMAT_XRGB8888,modifiers,0) == 1080);
    assert(WIDTH("exynos-drmdpu",1080,2340,DRM_FORMAT_NV12,modifiers,0) == 1080);
    assert(WIDTH("exynos-drmdpu",1080,2340,DRM_FORMAT_XRGB8888,NULL,DRM_MODE_FB_MODIFIERS) == 1080);
    assert(WIDTH("exynos-drmdpu",1080,2340,DRM_FORMAT_XRGB8888,modifiers,1U<<31) == 1080);
    for (unsigned i=0; i<4; ++i) {
        modifiers[i] = 1; assert(VALID == 1080); modifiers[i] = 0;
        offsets[i] = 4; assert(VALID == 1080); offsets[i] = 0;
        if (i) {
            handles[i] = 2; assert(VALID == 1080); handles[i] = 0;
            pitches[i] = 64; assert(VALID == 1080); pitches[i] = 0;
        }
    }
    pitches[0] = 4320; assert(VALID == 1080); pitches[0] = 4352;
    handles[0] = 0; assert(VALID == 1080);
    puts("25 stride selection assertions passed; no device operations");
}
