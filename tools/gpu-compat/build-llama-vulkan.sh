#!/bin/sh
set -eu

# Build the private Android ARM64 Vulkan llama.cpp tree.  The source tree is
# deliberately read-only for this recipe; all generated files stay below the
# compatibility root.

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=${GPU_COMPAT_ROOT:-"$SCRIPT_DIR/../../rootfs/gpu-compat-20260921"}
SRC="$ROOT/llama-src"
BUILD="$ROOT/llama-vulkan-build"
DEPS="$ROOT/vulkan-build-deps"
NDK=${ANDROID_NDK:-"$ROOT/toolchain/android-ndk-r27c"}
HOST_GLSLC=${GLSLC:-/usr/bin/glslc}
JOBS=${JOBS:-2}

check_sha() {
    actual=$(sha256sum "$1" | awk '{print $1}')
    test "$actual" = "$2" || {
        echo "pinned dependency hash mismatch: $1 ($actual)" >&2
        exit 2
    }
}

test -d "$SRC" || { echo "missing private source tree: $SRC" >&2; exit 2; }
test -x "$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android31-clang" || {
    echo "missing NDK clang: $NDK" >&2; exit 2;
}
test -f "$NDK/build/cmake/android.toolchain.cmake" || {
    echo "missing Android CMake toolchain: $NDK" >&2; exit 2;
}
test -x "$HOST_GLSLC" || { echo "missing host glslc: $HOST_GLSLC" >&2; exit 2; }
test -f /usr/include/vulkan/vulkan.hpp || {
    echo "missing host Vulkan-Hpp headers; install libvulkan-dev" >&2; exit 2;
}
test -f /usr/include/spirv/unified1/spirv.hpp || {
    echo "missing host SPIR-V headers; install spirv-headers" >&2; exit 2;
}
check_sha /usr/include/vulkan/vulkan.hpp \
    2680718d7452870e5b2127b7cbfafcc2f71af7dd74106c44d25927698c8b18ac
check_sha /usr/include/vulkan/vulkan_core.h \
    3dba3ef8adaff8f24e9080e74d365fc64b5fe53ec8bda607c29c103e15888fcf
check_sha /usr/include/spirv/unified1/spirv.hpp \
    2b9333cdf1d580c9875e37dad2579c6bf808081dda2a69499774a00537a289bd
check_sha /usr/share/cmake/SPIRV-Headers/SPIRV-HeadersConfig.cmake \
    2c008209f67305a7f416f66984a72b110ee0697b200328ecc46c940c7d795a70
check_sha /usr/share/cmake/VulkanHeaders/VulkanHeadersConfig.cmake \
    74123918434075a770a00cc8cbc1848c5014253203aa94769cf57596d6b0aef9
check_sha "$HOST_GLSLC" \
    71d72c98dc379daa3a7727740fcdd418e6f0426c8f8662b32018eba7e699bbc7

# The NDK r27c sysroot has the Android Vulkan C headers but no vulkan.hpp.
# Copy only header trees and CMake package metadata into a private prefix;
# never add host include directories to the cross compiler's search path.
mkdir -p "$DEPS/include" "$DEPS/lib/cmake"
if [ ! -f "$DEPS/.headers-ready" ]; then
    rm -rf "$DEPS/include/vulkan" "$DEPS/include/spirv" \
        "$DEPS/lib/cmake/SPIRV-Headers" "$DEPS/lib/cmake/VulkanHeaders"
    cp -a /usr/include/vulkan "$DEPS/include/"
    cp -a /usr/include/spirv "$DEPS/include/"
    mkdir -p "$DEPS/lib/cmake/SPIRV-Headers" "$DEPS/lib/cmake/VulkanHeaders"
    cp -a /usr/share/cmake/SPIRV-Headers/*.cmake "$DEPS/lib/cmake/SPIRV-Headers/"
    cp -a /usr/share/cmake/VulkanHeaders/*.cmake "$DEPS/lib/cmake/VulkanHeaders/"
    : > "$DEPS/.headers-ready"
fi

NDK_PREBUILT="$NDK/toolchains/llvm/prebuilt/linux-x86_64"
ANDROID_SYSROOT="$NDK_PREBUILT/sysroot"
ANDROID_VULKAN="$ANDROID_SYSROOT/usr/lib/aarch64-linux-android/31/libvulkan.so"
test -f "$ANDROID_VULKAN" || { echo "missing NDK Vulkan link stub: $ANDROID_VULKAN" >&2; exit 2; }

# Keep the source tree immutable and make the requested feature selection
# explicit, including static core/backend libraries and no OpenCL path.
cmake -S "$SRC" -B "$BUILD" -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
    -DANDROID_ABI=arm64-v8a \
    -DANDROID_PLATFORM=android-31 \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_FLAGS='-march=armv8.2-a+dotprod+fp16+i8mm+bf16' \
    -DCMAKE_CXX_FLAGS='-march=armv8.2-a+dotprod+fp16+i8mm+bf16' \
    -DCMAKE_PREFIX_PATH="$DEPS" \
    -DSPIRV-Headers_DIR="$DEPS/lib/cmake/SPIRV-Headers" \
    -DVulkan_INCLUDE_DIR="$DEPS/include" \
    -DVulkan_LIBRARY="$ANDROID_VULKAN" \
    -DVulkan_GLSLC_EXECUTABLE="$HOST_GLSLC" \
    -DGGML_VULKAN_SHADERS_GEN_TOOLCHAIN= \
    -DBUILD_SHARED_LIBS=OFF \
    -DGGML_STATIC=ON \
    -DGGML_BACKEND_DL=OFF \
    -DGGML_NATIVE=OFF \
    -DGGML_CPU=ON \
    -DGGML_OPENMP=OFF \
    -DGGML_LLAMAFILE=OFF \
    -DGGML_OPENCL=OFF \
    -DGGML_VULKAN=ON \
    -DGGML_VULKAN_CHECK_RESULTS=OFF \
    -DLLAMA_BUILD_TESTS=ON \
    -DLLAMA_BUILD_TOOLS=ON \
    -DLLAMA_BUILD_EXAMPLES=ON \
    -DLLAMA_BUILD_SERVER=OFF \
    -DLLAMA_BUILD_APP=OFF \
    -DLLAMA_BUILD_UI=OFF \
    -DLLAMA_OPENSSL=OFF \
    -DLLAMA_LLGUIDANCE=OFF \
    -DLLAMA_USE_SYSTEM_GGML=OFF \
    -DGGML_VULKAN_RUN_TESTS=OFF

cmake --build "$BUILD" --target llama-bench test-backend-ops -j"$JOBS"

file "$BUILD/bin/llama-bench" "$BUILD/bin/test-backend-ops"
readelf -h "$BUILD/bin/llama-bench" | grep -E 'Class:|Machine:|Type:'
readelf -d "$BUILD/bin/llama-bench" | grep -E 'NEEDED.*(vulkan|c\\+\\+|c\\.so)' || true
echo "built private Android ARM64 Vulkan artifacts in $BUILD"
