# Aquamarine S22 GCC16 artifact

The display-only source change was rebuilt in the isolated ARM64 Arch root
`rootfs/aquamarine-s22-build`, configured by CMake with GNU C/C++ 16.1.1.
All ordinary translation units were compiled by `/usr/sbin/c++` from that
root.  GCC16's ARM `cc1plus` crashes under QEMU on the large `DRM.cpp` unit
even at `-O0`; that is a compiler/emulation failure, not a source diagnostic.
The same unit was therefore compiled in the same root with the installed
ARM64 Clang 22 driver, using GCC16's libstdc++ headers/runtime, `-std=gnu++23`
and `-O0`.  The final shared-library link was performed by native ARM64
GCC16 in the isolated root.  No host GCC13 object was used in this artifact.

Artifact:

* `rootfs/aquamarine-s22-lib-gcc16/libaquamarine.so.0.15.1`
* SHA-256: `6fb3f4377ac3d14e4d74a18bdc5316e0acc23779e6894a1dab449cdb294d42ca`
* SONAME: `libaquamarine.so.14` (symlink supplied alongside it)
* ELF: AArch64, unstripped, BuildID `15dca9c0e7787f3ba2296c807a0da9384769db61`

Subsequently tested on the phone: this exact artifact rendered Hyprland and
the real Omarchy UI through software rendering, including the session left
running at handoff. See `docs/DRIVER_MODELS_2026-09-20.md` and the captured
frames under `evidence/driver-model-20260920/`. This is not GPU acceleration.
