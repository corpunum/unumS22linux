#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../rootfs/model-bench" && pwd)
MODEL=${1:?usage: run-arm64-bench.sh MODEL.gguf [threads]}
THREADS=${2:-4}
CTX=${CTX:-4096}
export LD_LIBRARY_PATH="$ROOT/libc:$ROOT/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$ROOT/libc/ld-linux-aarch64.so.1" --library-path "$ROOT/libc:$ROOT/lib" \
  "$ROOT/bin/llama-bench" -m "$ROOT/models/$MODEL" -t "$THREADS" -p 512 -n 128 -r 2 -o json
