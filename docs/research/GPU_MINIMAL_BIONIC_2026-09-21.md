# Minimal Bionic runtime for the S22 GPU path

Host-only source/design audit, 2026-09-21. This note does not load a vendor
GPU ELF, expose `/dev/dri`, modify a vendor/probe file, access the phone, or
change a boot image.

## Existing known-good runtime files

The unpacked Lineage recovery is the useful local Bionic reference. The same
bytes are present in `initramfs/nativev19`, `initramfs/nativev24`, and
`lineage/build-20260915/unpacked/ramdisk_extracted`. The source image is
`lineage/build-20260915/recovery.img`, SHA256
`b5bf01c4a47091eb95078fc69b133b44c2b453b31c23433594c5b605e3747b55`:

| file | role | SHA256 (all three trees) |
| --- | --- | --- |
| `system/bin/linker64` | AArch64 Bionic dynamic linker (`ld-android.so`) | `df0f69682d6a425e9a63c0894e87b6d1184537cc23773bff25fd3ca33e62f731` |
| `system/bin/sh` | smallest useful dynamically linked AArch64 test program | `e569bee746eba30595591273e438a59c346ffc161a74aae2bb38b20334af7bea` |
| `system/bin/toybox` | optional shell utility set | `4a8a32f3fb78d19cb64c43e6a07f7d6b2690b147db22d94ab32e3d11365e9695` |
| `system/lib64/libc.so` | Bionic libc | `25070ab0f962f590131d93b884271808e33d9821eb777fd32b7645d2509e1a59` |
| `system/lib64/libm.so` | Bionic math library | `1d9bf76ca52aa39280ecbd4a53903591b5df3e77927336065683960cb898a743` |
| `system/lib64/libdl.so` | Bionic dynamic-loading API | `1357c5712745eb86f2934742b012acd1d4f8fa49e77d09aaea38b704630fcb07` |
| `system/lib64/libc++.so` | Android libc++ used by graphics support | `dd327fb7c8d4abcb0fbb5bb839a946d365102af120466b956637e3559f4353e8` |
| `system/lib64/liblog.so` | Android logging API | `1b2fd659e9b7fc11016a8c866613eb12e2b3ec77fa27a908ef8bec2fec493b82` |
| `system/lib64/libcutils.so` | Android utility API | `29cc1cf71b226da5a68f1de7265a2eeae487847987da52b9e4cb0e89c3ecc137` |
| `system/lib64/libbase.so` | Android base/property helpers | `393372a74ab689ef2d51fccf35d8e44c003e93ae0a66698ccb706c94dd4473d0` |
| `system/lib64/libutils.so` | Android utility support | `b378438ad413577460cac8ce5c3a1c1fbf7a8e3921ce5fc30be223b15b4c7240` |
| `system/lib64/libhidlbase.so` | HIDL support imported by SBWC/mapper | `5746dd6bfd7ba57b5402fc26f342b95f77a2448146bc16335ba43a6b65a2502f` |

`sh` and `toybox` use interpreter `/system/bin/linker64`; the Bionic libraries
use Android SONAMEs (`libc.so`, not glibc's `libc.so.6`). Do not combine these
with the host's glibc/musl libraries. `libc.so` itself needs `ld-android.so`
and `libdl.so`, so the linker and the core three libraries are an indivisible
minimum.

The minimal base has now been exercised on the phone's native Linux side. The
parent staged 74 recovery files (about 19.8 MiB) under the isolated
`/srv/s22/gpu-compat-20260921/base` tree and ran Bionic `toybox true` from a
native-Alpine chroot with UID/GID 1000, all bounding capabilities dropped, and
`NoNewPrivs=1`. It exited 0 with no `/dev` devices or Android services
exposed. The only observed issue was the warning that generated
`/linkerconfig/ld.config.txt` was absent; receipt:
`rootfs/gpu-compat-20260921/base-test.json`. This proves the minimal Bionic
base can execute in the native Linux environment, not that a vendor GPU ELF
can load or compute.

The recovery linker configuration is deliberately small:

```
dir.recovery = /system/bin
[recovery]
namespace.default.isolated = false
namespace.default.search.paths = /system/${LIB}
```

The generated `linkerconfig/ld.config.txt` in the recovery tree is empty. It
does not describe `/vendor`, APEX namespaces, or a GPU-specific executable.
The AOSP linker configuration format maps an executable directory to a
section, and namespace search/permitted paths control what `DT_NEEDED` can
resolve ([AOSP format](https://android.googlesource.com/platform/bionic/%2Bshow/master/linker/ld.config.format.md)).

## Vendor closure found on the host

The private, already recovered owner-backup inputs are under
`rootfs/gpu-vendor-reuse-20260921/lib64/`. Static `readelf -dW` resolution of
the six recovered GPU ELFs gives this boundary:

| recovered ELF | direct extra requirements not in recovery `system/lib64` |
| --- | --- |
| `libSGPUOpenCL.so` | `libsync.so`, `libnativewindow.so`, SGR mapper |
| `libdrm_sgpu.so` | `libdrm.so`, `libion_exynos.so` |
| `libsbwchelper.so` | `libnativewindow.so`, `libexynosgraphicbuffer_core.so` |
| `android.hardware.graphics.mapper@4.0-impl-sgr.so` | `libeis_utils.so`, `libgralloctypes.so`, `libhardware.so`, `libion_exynos.so`, `android.hardware.graphics.mapper@4.0.so`, `libsync.so` |
| `vulkan.samsung.so` | `libhardware.so`, `libsync.so`, `libnativewindow.so`, SGR mapper, SBWC helper |

The read-only vendor image `rootfs/vendor-pristine-20260920.img` is indexed by
the retained `/tmp/blockmap.txt`; it contains source entries for
`/lib64/libion_exynos.so`, `/lib64/libdrm.so`, `/lib64/libeis_utils.so`,
`/lib64/libexynosgraphicbuffer_core.so`, and `/lib64/libexynosgraphicbuffer.so`.
Those are source locations only in this audit. They were not recovered or
added to the private GPU directory because this branch is docs-only. The
Lineage device source independently names the Android graphics closure in
`android_device_samsung_s5e9925-common/device-common.mk`, its vendor
`sepolicy/vendor/file_contexts`, and the graphics entries in
`proprietary-files.txt`.

Several imports show why this is more than a filename copy: OpenCL and Vulkan
import `AHardwareBuffer_*` from `LIBNATIVEWINDOW`, `sync_wait`, Android
property APIs, and SGR metadata; the mapper imports Android gralloc/HIDL APIs;
SBWC imports HIDL binder/service-manager symbols. These imports identify the
potential interop boundary, but do not prove that every library or service is
needed on a headless OpenCL path. The complete graphics/service closure and
the exact code paths reached by a GPU load remain unproven.

## Linker, APEX, and property requirements

The recovery copies are non-APEX Bionic files. The linker binary contains
fixed Android paths for `/system/lib64`, `/vendor/lib64`, `/system/etc/ld.config.txt`,
`/linkerconfig/`, and `/apex/com.android.runtime/...`; it does not contain an
`ANDROID_ROOT` string. Therefore:

- Keep the isolated runtime's `/system/bin/linker64` and `/system/lib64` as
  absolute paths. Do not invoke it through a host loader.
- Supply a custom `system/etc/ld.config.txt` section for the test executable
  whose default namespace searches `/system/${LIB}`, `/vendor/${LIB}`, and
  `/vendor/${LIB}/hw`, with isolation disabled only inside the private test
  namespace. Put the executable at the mapped path; `LD_LIBRARY_PATH` alone
  must not be treated as a namespace solution.
- Do not invent APEX libraries. Either keep the tested closure entirely in
  `/system` plus `/vendor`, as the recovery files permit, or provide a real
  `/apex/com.android.runtime` closure and matching linker configuration. A
  fake APEX path would make a pass meaningless.
- `LD_CONFIG_FILE` is an AOSP linker escape hatch only for the VNDK-lite path
  (`ro.vndk.lite`); do not rely on it without recording the property and
  linker branch actually selected. The deterministic option is an executable
  path that maps to the custom section.

Bionic property lookup is not an ordinary environment-variable lookup. The
libc property implementation can use a serialized area under
`/dev/__properties__` (or its legacy workspace-FD fallback); Android init
creates and initializes this area ([AOSP property service](https://android.googlesource.com/platform/system/core/%2B/master/init/property_service.cpp),
[Bionic property ABI](https://android.googlesource.com/platform/bionic/%2B/master/libc/include/sys/system_properties.h)).
The measured Bionic `toybox true` gate did not provide a property area and
still exited 0, so a property area is not a demonstrated prerequisite for the
base runtime or for that command. Whether the vendor GPU libraries actually
query properties during load or compute is unknown. If a future GPU load
reports property lookup failure, construct a private area with the matching
Bionic property ABI and verify its magic, version, ownership, and read-only
mode; do not treat that setup as already-proven acceptance evidence.

Static strings in the recovered vendor ELFs identify these property keys as
possible early reads: `ro.arch`, `ro.product.brand`,
`ro.product.manufacturer`, `ro.board.first_api_level`, `ro.build.version.sdk`,
`ro.vendor.build.version.release`, `vendor.hwc.display.h`,
`vendor.hwc.display.w`, `debug.sgr.*`, `vendor.sbwchelper.*`, and
`vendor.socl.*`. The known-good recovery properties provide the board/runtime
values `ro.bionic.arch=arm64`, `ro.bionic.cpu_variant=cortex-a76`,
`ro.product.board=s5e9925`, `ro.board.platform=s5e9925`,
`ro.board.first_api_level=31`, `ro.build.version.sdk=36`,
`ro.vendor.build.version.release=16`, `ro.hardware.egl=samsung`,
`ro.hardware.vulkan=samsung`, `ro.hwui.use_vulkan=true`, and
`ro.vendor.gpu.dataspace=1`. The display dimensions and debug toggles are not
present in this recovery property file; do not synthesize them until a real
call site or runtime error demonstrates that they are required.

## First isolated loader gate

The measured base gate is an AArch64 process in a private chroot/credential
boundary with only the Bionic runtime and `/dev` hidden. It used no property
area, GPU libraries, or Android services. A subsequent vendor-library gate
should retain that isolation and add only the statically resolved closure;
whether it needs a private property area should be determined by recorded
errors, not assumed. The target device set remains `/dev/null` and
`/dev/urandom` only, with no `/dev/dri`, `/dev/ion`, DMA heap, binder, or
service-manager device visible.
A tiny helper should:

1. prove its interpreter and Bionic closure with the custom linker section;
2. record `DT_NEEDED` resolution and process maps; and
3. only then attempt the selected `dlopen`/entry-point path in that child.

`linker64 --list` is available (the binary contains `Usage: ... [--list]`),
but it is a loader operation, not a harmless static dry-run: mapping,
relocation, and library initialization can occur. Treat it as a real test,
run it in the isolated child, enforce a timeout, and record every open/error;
never call it acceptance and never expose a GPU device at this stage. The
next gate, only after this one is understood, can expose a render node and
run a known-output OpenCL operation. A successful linker load alone is not GPU
acceptance.

## Current conclusion

There is an exact, reusable Bionic/linker base in the local Lineage recovery,
and its no-device execution is now measured and passing. There is not yet a
minimal working Samsung headless GPU runtime. The missing Android graphics
libraries are the concrete static blockers to an actual vendor-library load;
property-area and full-service requirements remain unknown rather than
accepted prerequisites. The smallest credible route is a private Bionic
namespace with the closure needed by the selected headless path, followed by
a separate device-enabled known-output test. Full Android services remain
unproven and must not be assumed either way.
