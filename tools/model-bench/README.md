# ARM64 CPU model benchmark

This directory is the host-staged runtime for the native Alpine ARM64 phone.
The executable is llama.cpp commit `e613ef2c81bae98d59850d061ac29e6e3e88cb00`
(0.4.1-dev), cross-built with clang 18 for aarch64 Linux, CPU backend only.
The build-tree wrappers below expect the matching `rootfs/` directory layout
on the executing ARM64 machine; they are not host-to-phone deployment commands:

```sh
tools/model-bench/run-arm64-bench.sh Qwen3.5-0.8B-Q4_0.gguf 4
tools/model-bench/run-arm64-bench.sh Qwen3.5-2B-Q4_0.gguf 4
tools/model-bench/run-arm64-completion.sh Qwen3.5-0.8B-Q4_0.gguf "Say hello in one sentence." 4
tools/model-bench/run-arm64-bench-optimized.sh Qwen3.5-0.8B-Q4_0.gguf 4
```

The command uses 4 threads, 512 prompt and 128 generation tokens, two
repetitions, and JSON output. The benchmark sizes its context for the selected
prompt/depth/generation workload; it does not establish performance at the
model's maximum advertised context. The optimized sibling was compiled
with `-march=armv8.6-a+dotprod+i8mm`; run it only after confirming the phone
feature set. The `libc/` directory is a private
glibc loader/runtime closure, so Alpine's musl system libraries are untouched.
This is a benchmark configuration, not a speed prediction; CPU feature
dispatch must be verified on-device. The model files are fetched from the
pinned URLs and their expected sizes/hashes are recorded in `MANIFEST.sha256`.

For this connected S22, use `tools/model-bench/stage-phone.sh` on the host,
then `tools/s22-ssh` to connect. `s22-chat` is the original per-request CPU
client. The actual short benchmark harness is
`tools/linux-rootfs/s22-benchmark` (phone-side), with pp256/tg64, 2 repetitions,
and explicit CPU affinity. It is separate from the build-tree wrappers above.
Results and the depth4096 run are in
`docs/DRIVER_MODELS_2026-09-20.md`. Model/runtime files are RAM-only on the phone.

## Resident chat server

After model staging, run `bash tools/model-bench/start-phone-server.sh` from
the host. This transfers a SHA-verified private ARM64 runtime to the phone's
existing tmpfs, starts a CPU-only server on **127.0.0.1:8089**, and checks
`/health`. It refuses to overwrite a running server. Context is 4096, one
slot, four fast CPU cores, no GPU layers, no web UI. It does not replace
Alpine's system libc, flash a partition, or start a persistent boot service.
Server log/PID: `/mnt/model-bench/server.log` and `server.pid` on the phone.

`tools/linux-rootfs/s22-chat-http.py` is the streaming terminal client used by
the Arch desktop. The original Alpine `s22-chat` remains a CLI fallback.
Measured resident-client first text: 0.6 and 0.4 seconds for two short test
requests; total responses 1.1 and 3.7 seconds. This is request-specific latency,
not a general agent-performance claim. The on-screen keyboard test answered
`hi` in 1.3 seconds with first text at 0.7 seconds.

Server artifact hashes and build provenance are recorded in
`evidence/model-bench-20260920/server-optimized.*`.
