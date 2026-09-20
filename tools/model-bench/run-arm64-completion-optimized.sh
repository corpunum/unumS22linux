#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../rootfs/model-bench-optimized" && pwd)
BASE=$(CDPATH= cd -- "$(dirname -- "$0")/../../rootfs/model-bench" && pwd)
MODEL=${1:?usage: run-arm64-completion-optimized.sh MODEL.gguf 'prompt' [threads]}
PROMPT=${2:?usage: run-arm64-completion-optimized.sh MODEL.gguf 'prompt' [threads]}
THREADS=${3:-4}
export LD_LIBRARY_PATH="$ROOT/libc:$ROOT/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$ROOT/libc/ld-linux-aarch64.so.1" --library-path "$ROOT/libc:$ROOT/lib" \
  "$ROOT/bin/llama-completion" -m "$BASE/models/$MODEL" -t "$THREADS" -c 4096 -n 64 -p "$PROMPT"
