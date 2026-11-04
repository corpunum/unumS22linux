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

## Local model speed: the current server, and how to pick a faster setting (2026-11-04)

The sections above describe the September 2026 setup. Since 2026-10-22 the phone's local model ("Local
(Gemma E2B)" in the shell, OpenUnum's offline fallback) is **Gemma-4 E2B QAT Q4_0 + its MTP drafter**,
served on demand by `tools/hardware/s22-llama-ondemand.py`: CPU only (`-dev none -ngl 0`), pinned to the
three Cortex-A710 cores (cpu4-6) with 2 threads and the cluster capped at 1.8 GHz while loaded (the "cool
profile"; the X2 core and full clocks reach the 70 C guardian line within seconds).

**Binary provenance.** `/system/bin/llama-server-b30266` in the isolated runtime is
`tools/gpu-compat/build-llama-vulkan.sh` with `LLAMA_SERVER=ON` on llama.cpp `f2cc7282` (2026-10-11),
NDK r27c, `-march=armv8.2-a+dotprod+fp16+i8mm+bf16`, Vulkan ON (unused at `-dev none`), KleidiAI OFF.
The build tree on the rig produced llama-server sha256 `cfe04cee4066…a0e039afe` and llama-bench
`dc0251f0faeb…c98d57a6`, and the phone's copies hash-match them (checked 2026-11-04). With these flags
ggml repacks Q4_0 weights to its NEON+i8mm 4x8 kernels (the kernel exposes HWCAP2_I8MM), so the CPU path
already uses the int8 matrix instructions; KleidiAI is the remaining kernel option.

Reproduce or vary it (out of tree, the source stays read-only):

```sh
GPU_COMPAT_ROOT=$PWD/builds/gpu-compat ANDROID_NDK=<ndk-r27c> LLAMA_SRC=<llama.cpp checkout> \
LLAMA_BUILD=$PWD/builds/llama-<variant> LLAMA_COMMIT=f2cc7282 LLAMA_SERVER=ON \
KLEIDIAI=ON VULKAN=OFF TARGETS="llama-server llama-bench" sh tools/gpu-compat/build-llama-vulkan.sh
```

**Measured on the phone (2026-10-11, llama-bench, one repetition):** Gemma-4 E2B prefill 32 tok/s and
decode 8.0 tok/s at the cool profile (10.8 tok/s decode with the MTP drafter, 73 % acceptance); 55 tok/s
prefill with 3 threads at full clock (thermal abort at 68 C after a sustained run); Vulkan on the Xclipse
920 104 tok/s prefill and 11.8 decode, but BIG reaches 69-70 C within 15-90 s, so the GPU is not usable
for sustained turns. Turn time to first token with the lean profile (2026-10-22): 8.4-9.9 s warm, 27.6 s
after an unload.

**`s22-llm-bench.py`** (this directory; runs on the phone as root, stdlib only) measures what the next
setting should be: the production cool profile against prefill-thread (`-tb`) candidates and any other
binary, with a `turn` workload shaped like the real local chat turn (stable ~700-token prefix, new
~300-token question, 96-token answer):

```sh
python3 s22-llm-bench.py plan                                  # the jobs, nothing runs
python3 s22-llm-bench.py run --out /srv/s22/state/llm-bench/results.jsonl
python3 s22-llm-bench.py report /srv/s22/state/llm-bench/results.jsonl
```

It refuses to run while a CPU clock cap is active (unload the local model first), holds
`/run/s22-hold/llama` so OpenUnum uses the cloud model meanwhile, waits for both CPU zones below 50 C
before each job, aborts at 66 C or MemAvailable < 450 MB, kills only its own PIDs and restores exactly
the clock limits it wrote. `report` picks the configuration with the shortest turn prompt time that
stayed under the temperature limit without losing decode speed, and prints the one line to apply it:
`s22-llama-ondemand` reads `/srv/s22/state/llama-ondemand/threads-batch` (or `S22_LLAMA_THREADS_BATCH`)
at every model load and adds `-tb N` (prefill threads only; decode stays at 2). It is off until measured.
Host tests: `python3 tools/model-bench/test_s22_llm_bench.py`.
