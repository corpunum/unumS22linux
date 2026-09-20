#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../rootfs/model-server-optimized" && pwd)
BASE=$(CDPATH= cd -- "$(dirname -- "$0")/../../rootfs/model-bench" && pwd)
MODEL=${1:-Qwen3.5-0.8B-Q4_0.gguf}
THREADS=${2:-4}
MODEL_PATH=$MODEL
case "$MODEL_PATH" in
  /*) ;;
  *) MODEL_PATH="$BASE/models/$MODEL_PATH" ;;
esac
test -r "$MODEL_PATH"
exec nice -n 19 "$ROOT/libc/ld-linux-aarch64.so.1" \
  --library-path "$ROOT/libc:$ROOT/lib" \
  "$ROOT/bin/llama-server" \
  -m "$MODEL_PATH" -t "$THREADS" -C f0 -c 4096 \
  --host 127.0.0.1 --port 8089
