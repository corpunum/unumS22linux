#!/bin/sh
set -eu

# DIAGNOSTIC ONLY, NOT A WORKING MODEL-OFFLOAD PORT. This admits Xclipse but
# upstream GATED_DELTA_NET and matrix-kernel selectors still require Adreno/Intel.
# Do not install this as a working GPU backend or run it on the phone as a fix.
# Private-tree Android build for inspecting the Xclipse admission patch.
# This script never changes the tracked llama.cpp checkout and never executes ARM64
# output.  Set GPU_COMPAT_ROOT to override the artifact root.

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=${GPU_COMPAT_ROOT:-"$SCRIPT_DIR/../../rootfs/gpu-compat-20260921"}
SRC="$ROOT/llama-src"
NDK=${ANDROID_NDK:-"$ROOT/toolchain/android-ndk-r27c"}
HEAD="adb55e5148dc93bcdca7212a2d1df3ccc422959a"
PATCH="$SCRIPT_DIR/llama-xclipse-opencl.patch"
BUILD="$ROOT/llama-xclipse-opencl-build"
OPENCL_HEADERS="$ROOT/opencl-headers"
OPENCL_VENDOR=${OPENCL_VENDOR:-"$ROOT/../gpu-vendor-reuse-20260921/lib64/libSGPUOpenCL.so"}

test -d "$SRC" || { echo "missing private source tree: $SRC" >&2; exit 2; }
test -x "$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android31-clang" || {
    echo "missing NDK clang: $NDK" >&2; exit 2;
}
test -f "$NDK/build/cmake/android.toolchain.cmake" || {
    echo "missing NDK CMake toolchain: $NDK/build/cmake/android.toolchain.cmake" >&2; exit 2;
}
test -f "$OPENCL_HEADERS/CL/cl.h" || {
    echo "missing private OpenCL headers: $OPENCL_HEADERS/CL/cl.h" >&2; exit 2;
}
test -f "$OPENCL_VENDOR" || { echo "missing vendor OpenCL DSO: $OPENCL_VENDOR" >&2; exit 2; }
test "$(sha256sum "$OPENCL_VENDOR" | awk '{print $1}')" = \
    "0da7a4f54dad4102801fdae8e91a04127ca66de73b9913e28897a5a2758ceb81" || {
    echo "vendor DSO hash mismatch: $OPENCL_VENDOR" >&2; exit 2;
}

# The source archive is intentionally checked by its recorded immutable HEAD marker.
test -f "$ROOT/llama-src.source-head" && grep -qx "$HEAD" "$ROOT/llama-src.source-head" || {
    echo "private source HEAD marker mismatch" >&2; exit 2;
}
grep -q 'enum GPU_FAMILY' "$SRC/ggml/src/ggml-opencl/ggml-opencl.cpp" || {
    echo "private source is not the expected llama.cpp archive" >&2; exit 2;
}
if ! grep -q 'GPU_FAMILY::XCLIPSE' "$SRC/ggml/src/ggml-opencl/ggml-opencl.cpp"; then
    patch -d "$SRC" -p1 < "$PATCH"
fi

# Keep this check explicit: the reviewed build must not compile Adreno kernels.
if grep -q '^#define GGML_OPENCL_USE_ADRENO_KERNELS' "$SRC/ggml/src/ggml-opencl/ggml-opencl.cpp"; then
    echo "unexpected source-level Adreno enable" >&2
    exit 2
fi

mkdir -p "$BUILD"
cmake -S "$SRC" -B "$BUILD" -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
    -DANDROID_ABI=arm64-v8a \
    -DANDROID_PLATFORM=android-31 \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_FLAGS='-march=armv8.2-a+dotprod+fp16+i8mm+bf16' \
    -DCMAKE_CXX_FLAGS='-march=armv8.2-a+dotprod+fp16+i8mm+bf16' \
    -DCMAKE_EXE_LINKER_FLAGS="-L$(dirname "$OPENCL_VENDOR") -Wl,-rpath,/vendor/lib64/hw" \
    -DOpenCL_INCLUDE_DIR="$OPENCL_HEADERS" \
    -DOpenCL_LIBRARY="$OPENCL_VENDOR" \
    -DBUILD_SHARED_LIBS=OFF \
    -DGGML_NATIVE=OFF \
    -DGGML_CPU=ON \
    -DGGML_OPENMP=OFF \
    -DGGML_LLAMAFILE=OFF \
    -DGGML_OPENCL=ON \
    -DGGML_OPENCL_TARGET_VERSION=300 \
    -DGGML_OPENCL_EMBED_KERNELS=ON \
    -DGGML_OPENCL_USE_ADRENO_KERNELS=OFF \
    -DGGML_OPENCL_USE_ADRENO_BIN_KERNELS=OFF \
    -DGGML_VULKAN=OFF \
    -DLLAMA_BUILD_TESTS=ON \
    -DLLAMA_BUILD_TOOLS=ON \
    -DLLAMA_BUILD_EXAMPLES=ON

cmake --build "$BUILD" --target llama-bench test-backend-ops -j2

file "$BUILD/bin/llama-bench" "$BUILD/bin/test-backend-ops"
readelf -d "$BUILD/bin/llama-bench" | grep -E 'NEEDED.*(SGPUOpenCL|c\\+\\+|c\.so)' || true
echo "built private Xclipse OpenCL artifacts in $BUILD"
