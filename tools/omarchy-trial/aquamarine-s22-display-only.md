# Aquamarine S22 display-only workaround

Current result: the patch rendered Hyprland/Omarchy on the phone after the
consistent GCC16-runtime rebuild documented in `aquamarine-s22-gcc16-build.md`.
The first mixed-toolchain library described below crashed at runtime and is
superseded. The public patch is `aquamarine-s22-display-only.patch`, against
Aquamarine v0.15.1 commit `f31c47a1b9d300847d8fc3108ad959448103dfc1`.
Full upstream reference files and the local checkout are intentionally excluded.

## Code assessment

Aquamarine 0.15.1's `attempt()` makes the first enumerated DRM device the
primary backend and passes it as `primary` to later devices. Therefore the
safe single-card trial must select only `/dev/dri/card1` (for this phone's
`exynos-drmdpu`) with `AQ_DRM_DEVICES=/dev/dri/card1`; otherwise card1 remains a
secondary backend and the workaround must not apply.

On a sole backend, `registerGPU()` leaves `primary == nullptr`. Upstream then
tries an EGL renderer in `onReady()` whenever `rendererRequired` is true. That
is the failure observed on the phone: the display-only DPU KMS fd has no EGL
matching render device. The isolated source variant
`aquamarine-s22-display-only.cpp` adds one fail-closed condition:

```
AQ_S22_DISPLAY_ONLY=1 && !primary && drmVerName == "exynos-drmdpu"
```

It only sets `rendererRequired = false` and logs a warning. This suppresses
`onReady()`/`initMgpu()` EGL probing for that exact, explicitly opted-in sole
device. All upstream behavior is unchanged otherwise, including `buildGlFormats`
and secondary renderer handling. Scanout still uses `CBackend::primaryAllocator`
and the DRM plane format list; this patch does not claim that allocation or
atomic commit will succeed.

Suggested isolated trial environment:

```
AQ_DRM_DEVICES=/dev/dri/card1 \
AQ_S22_DISPLAY_ONLY=1 \
AQ_NO_MODIFIERS=1 \
HYPRLAND_EGL_NO_MODIFIERS=1 \
Hyprland --config ./hyprland-minimal.lua
```

`AQ_NO_MODIFIERS` is optional and should be tested as a separate variable; it
is not part of the source workaround.

## Build status

The exact upstream v0.15.1 source was cloned into
`aquamarine-s22-source`, and the patch was built with the signed Arch ARM
toolchain in the isolated `rootfs/aquamarine-s22-build` root. The replacement
library is:

`rootfs/aquamarine-s22-lib/libaquamarine.so.0.15.1`

SHA256: `7791ac78ffbac2ac39abbfb76f518c1c423336173234eddced8549235b4b5b11`

`file` confirms ELF aarch64; SONAME is `libaquamarine.so.14`. The library
translation units, including patched `src/backend/drm/DRM.cpp`, compiled and
linked successfully. Test executables were not linked because the host
cross-linker is GCC 13 while staged Arch ARM dependencies were built with GCC
16/glibc 2.43 (`GLIBCXX_3.4.34/35`, `GLIBC_2.43` unresolved). This does not
invalidate the shared-library link, but runtime compatibility with the staged
Hyprland binary still requires matching ABI checks before a phone trial.
