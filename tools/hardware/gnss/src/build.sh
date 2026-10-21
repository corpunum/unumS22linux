#!/bin/sh
# Build the GNSS tools for the phone (aarch64, Bionic) on the rig.
#   NDK=.../android-ndk-r27c  AOSP_INC=<prebuilts/vndk/v30/arm64/include>  SYSLIB=<phone /system/lib64 copies>
#   VENDOR=<stock vendor extract>  OUT=<dir>  sh build.sh
# gnss-guard and gnss-hold-reset are static. gnss-probe links against the phone's own platform
# libc++ (std::__1), libhidlbase, libutils and the stock gnss HIDL libraries, so it is compiled
# with a __config_site override that selects the __1 ABI namespace instead of the NDK's __ndk1.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
: "${NDK:?}" "${AOSP_INC:?}" "${SYSLIB:?}" "${VENDOR:?}" "${OUT:?}"
TC=$NDK/toolchains/llvm/prebuilt/linux-x86_64
CC="$TC/bin/aarch64-linux-android31-clang"
CXX="$TC/bin/aarch64-linux-android31-clang++"
mkdir -p "$OUT" "$OUT/cfg"
$CC -O2 -static -Wall -Wextra -o "$OUT/gnss-guard" "$HERE/gnss-guard.c"
$CC -O2 -static -Wall -Wextra -o "$OUT/gnss-hold-reset" "$HERE/gnss-hold-reset.c"
$CC -O2 -shared -fPIC -Wl,-soname,libandroid_net.so -o "$OUT/libandroid_net.so" "$HERE/stub-libandroid_net.c"
$CC -O2 -shared -fPIC -Wl,-soname,propshim.so -o "$OUT/propshim.so" "$HERE/propshim.c"
$CC -O2 -shared -fPIC -Wl,-soname,libvndksupport.so -o "$OUT/libvndksupport.so" "$HERE/stub-libvndksupport.c" -ldl
# libc++ ABI namespace __1 (platform) instead of __ndk1 (NDK)
sed 's/#define _LIBCPP_ABI_NAMESPACE __ndk1/#define _LIBCPP_ABI_NAMESPACE __1/' \
  "$TC/sysroot/usr/include/c++/v1/__config_site" > "$OUT/cfg/__config_site"
grep -q '_LIBCPP_ABI_NAMESPACE __1' "$OUT/cfg/__config_site"
INC=""
for d in $(find "$AOSP_INC/generated-headers" -type d -name gen | grep -E 'gnss|hidl/transport/base'); do INC="$INC -I$d"; done
for d in system/libhidl/base/include system/libhidl/transport/include system/core/libutils/include \
         system/core/libcutils/include system/core/libsystem/include system/core/base/include system/libfmq/include; do
  INC="$INC -I$AOSP_INC/$d"
done
L=$VENDOR/lib64
$CXX -O2 -std=c++17 -fno-rtti -Wall -Wno-deprecated-declarations -nostdinc++ -I"$OUT/cfg" -isystem "$TC/sysroot/usr/include/c++/v1" $INC \
  -o "$OUT/gnss-probe" "$HERE/gnss-probe.cpp" \
  -nostdlib++ -Wl,--allow-shlib-undefined -L"$SYSLIB" -L"$L" \
  -lc++ -lhidlbase -lutils -lcutils -lbase -llog -ldl \
  "$L/android.hardware.gnss@1.0.so" "$L/android.hardware.gnss@1.1.so" "$L/android.hardware.gnss@2.0.so" \
  "$L/android.hardware.gnss@2.1.so" -Wl,-rpath,/vendor/lib64
echo built: $(ls "$OUT")
