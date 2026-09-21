# Samsung Xclipse llama.cpp Vulkan evidence (2026-09-21)

## Hardware and backend admission

The isolated Bionic llama.cpp Vulkan process now admits the physical Samsung
Xclipse 920 device. The successful `llama-vulkan-list-v4-clearerror` receipt
reports:

- `Vulkan0: Samsung Xclipse 920`;
- FP16 support, warp/subgroup size 64, and 32768 bytes shared memory;
- unchanged boot and zero new GPU messages/kernel faults.

This is an isolated headless process with UID/GID 1000, no capabilities and
`NoNewPrivs=1`; it is not an Android graphics service or WSI/compositor path.
The bridge used for this acceptance is v4,
`libvulkan-v4.so` SHA-256
`9c9a7c748ce9170a5b19bc76bf653932b7f02505d64e6cdcd804a17c047195c4`.

The earlier help/linker failure, v2 crash, and v3 diagnostic crash remain in
the curated receipt set as failed bring-up history. v4 corrected the bridge's
stale thread-local `dlerror()` validation state before HMI lookup; this does
not assert a vendor-driver defect.

Public receipts are under
[`evidence/gpu-compat-20260921/summary.json`](../../evidence/gpu-compat-20260921/summary.json).
The original exact stdout/stderr and trace files remain in the mode-0700
`rootfs/gpu-compat-20260921/trials/` tree. The earlier private export copy is
retained under `rootfs/gpu-compat-20260921/export-private-raw/`; neither is
part of the public evidence tree. Public trace text removes repetitive GIPA
lines and redacts HMI pointer addresses. Raw model benchmark/text output is
private-only; public JSON contains measurements and hashes, not generated
text.

## Accepted llama backend operation

The `llama-vulkan-mulmat-small` receipt ran the Vulkan backend's numerical
operation test against the Samsung device. Six registered tests passed: the
f32, f16, and q4_0 input variants, each appearing in the two registered
precision cases. The CPU backend was explicitly skipped, so this result is
not a CPU fallback. The curated JSON records:

```json
{
  "backend_numerical_tests": 6,
  "operation_pass": true,
  "reference_correct": true,
  "cpu_fallback_used": false
}
```

This is llama.cpp operation evidence, not the standalone SPIR-V checksum
field used by the direct HAL probe. `numerical_compute_pass` intentionally
remains null for this llama trial; `operation_pass` is the applicable field.

## Bounded model result and production boundary

The Qwen3.5 0.8B Q4_0 run supports bounded working-inference acceptance in
this private Vulkan trial. The first cold, no-warmup GPU receipt (`-ngl 99`, one sample)
recorded prompt 32 at 25.5434 tokens/s and generation 16 at 18.1952
tokens/s. A warmed, verbose three-sample GPU receipt recorded 25/25 layers,
prompt 32 at `322.7206 +/- 1.84` tokens/s and generation 32 at
`38.1953 +/- 0.42` tokens/s. These are scoped measurements; they do not
extrapolate to a 4B model or establish general model quality.

The strict CPU control used `--device none --no-op-offload --no-kv-offload`
and recorded prompt 32 at `212.2805 +/- 55.39` tokens/s and generation 32 at
`31.5856 +/- 2.34` tokens/s. The GPU and CPU text-control payloads matched
byte-for-byte after the isolated-run marker (public metadata hash
`c2238f33d756d76818e679939c011871a928824c1a5214e0a4b94f0b005c1e4f`); this
is a deterministic parity check, not a full quality evaluation. The GPU
completion measurement was 30.81 tokens/s versus the CPU control's 42.86
tokens/s for that short cold text run, so no universal speed advantage is
claimed.

The verbose warmed GPU run explicitly logged `offloaded 25/25 layers to GPU`
and a `Vulkan0` model buffer of 526.50 MiB. The strict CPU control logged
`offloaded 0/25 layers to GPU` and a CPU-mapped 526.50 MiB buffer. These logs
establish actual offload for this 0.8B diagnostic, not merely an `-ngl 99`
request. The benchmark/text receipts are exported as metadata-only JSON; their
raw outputs remain private. This does not establish full logits/quality
coverage, 4B-model support, a production default, or long-duration
reliability. The warmed GPU result is scoped to this 0.8B Q4_0 build and trial
configuration.

For provenance, the private llama source marker is
`adb55e5148dc93bcdca7212a2d1df3ccc422959a`; the bench receipt's
`build_commit=0d2beca` identifies the enclosing S22 build workspace and is not
being presented as the llama.cpp source commit. The staged binary hashes are
recorded in `rootfs/gpu-compat-20260921/llama-vulkan-artifacts.json`.

Exact accepted Android31/AArch64 artifacts (NDK r27c):

- `llama-bench`: SHA256 `47f73f8eda13257e25e5a4158c02762288571308c991cd1b386db2f0e871151e`.
- `test-backend-ops`: SHA256 `1f32a170a50475c0484abc0244c4efc06e393cba0b051fc578587d2a8d397cfc`.
- `llama-completion`: SHA256 `5c5d838303dda52dad9bf69d2430609c51c077ae85237f8d4d60ebd8f0221b3c`.
- Model: SHA256 `57d1997790d1744fba5b40a7317df71ea5e2acee28c47e78f0cce39c0703f8cf`, 563036064 bytes.
- Samsung Vulkan HAL: SHA256 `c42a069e05087e925dd09bfa4c6bd47c6d6667d39093bbf6ef315b0ef814dfeb`.

The tested compatibility runtime persists on Linux userdata. This is not a
change to the resident service or its startup configuration.

No claim is made here for WSI, display, compositor, generic Vulkan-loader
compatibility, other models, or long-duration reliability.
