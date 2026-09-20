# ENN/NPU runtime feasibility audit

The recovered `libenn_public_api_cpp_lib.so` (`__7c1`, AArch64 ELF) exports
the C++ ENN API entry points `enn::api::EnnInitialize`, `EnnOpenModel`,
`EnnCreateBuffer`, `EnnSetBuffers`, `EnnExecuteModel`, `EnnExecuteModelWait`,
`EnnGetMetaInfo`, and related preference/buffer calls. Its dynamic closure
requires `libenn_user_lib.so`, `libc++.so`, `libc.so`, `libm.so`, and `libdl.so`.
The exported API is therefore identifiable, but no matching public headers or
ABI structs are present, and the required user-library closure is Android
Bionic-oriented rather than a native Alpine/glibc runtime.

The exact stock NPU module is
`initramfs/nativev19/lib/modules/5.10.260-g4e5c5ad7d950/npu.ko`, SHA256
`b2110bd9f95c19e6ce2fca97d4a0cc9817029dff92427088dbb8b483d9948da3`. `modinfo`
reports vermagic `5.10.260-g4e5c5ad7d950`, AArch64, and dependencies on
`exynos_devfreq`, `exynos-bts`, `exynos_pm_qos`, `dss`, `exynos-chipid_v2`,
`imgloader`, `freq-qos-tracer`, `exynos-s2mpu`, `memlogger`, `abc`, and
`exynos_sci`. Strings show VS4L/session operations, NCP-v25 validation, and
firmware requests from `/vendor/firmware/` for `vectors.bin`; additional
strings identify `AIE.bin` and `dsp_reloc_rules.bin`. `NPU.bin` was not found
as a concrete request string and is not a proven requirement.

The vendor image does contain recovered/decompressed `AIE.bin` and
`dsp_reloc_rules.bin`, but the exact `vectors.bin` is absent; `NPU.bin` remains
unproven rather than a confirmed missing dependency.
The image has Android ENN init/service/VINTF declarations and numerous ENN
libraries/models, but no standalone Linux loader, matching headers, Bionic
closure, or accepted ioctl contract. Consequently no runnable host/native
inference probe is staged: constructing one would require guessing ABI,
buffer structs, or VS4L ioctls. A valid future probe needs the exact Android
ENN headers/runtime closure plus `vectors.bin`, followed by a separately
approved device test.

## Primary-source check

Samsung/ENNDelegate repository commit `977585f75811a22814735f9fc9c806c088b59813`
(`https://github.com/Samsung/ENNDelegate/tree/977585f75811a22814735f9fc9c806c088b59813`)
contains versioned self-extracting archive scripts, not a portable public ENN
header/UAPI package. Samsung/ONE master
(`https://github.com/Samsung/ONE/tree/master`) documents a general compiler and
runtime for multiple targets, but does not provide this Exynos-2200 downstream
VS4L ABI or the recovered proprietary ENN structs. The public repositories
therefore do not close the native Linux probe gap.
