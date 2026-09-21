# Native Xclipse shader diagnostics — 2026-09-21

This is a bounded diagnostic record, not an acceleration result. The known
baseline is the pinned RADV fork `d1b295e8c61c013c454e8015983a1d6b6df82bf2`
and SGPU kernel `4e5c5ad7d950e4de0688b5663965f2075654b2ad`. The relevant
implementation is Samsung's `drivers/gpu/drm/samsung/sgpu`; no standard AMD
GPU claim is implied.

## What is established

- The internal CP-DMA readback captured the GPU-visible descriptor words and
  all 18 shader dwords exactly as CPU-side bytes. It completed a real fence
  wait (`expired=1`) but was explicitly not a compute pass:
  [internal-readback.txt](../evidence/gpu-shader-20260921/internal-readback.txt).
- The companion register readback captured `COMPUTE_USER_DATA_2=0x00040000`,
  `COMPUTE_PGM_LO=0`, `COMPUTE_PGM_HI=0x80`,
  `COMPUTE_PGM_RSRC1=0x602c0001`, `COMPUTE_PGM_RSRC2=0x00000086`, and
  `COMPUTE_PGM_RSRC3=0`, alongside the same descriptor/code bytes:
  [internal-register-readback.txt](../evidence/gpu-shader-20260921/internal-register-readback.txt).
- The wave32 shader was recorded and then submitted with the same workload;
  it still timed out and faulted in TCP with a write fault. This rejects
  wave32 as a sufficient fix:
  [record-wave32-confirmed.txt](../evidence/gpu-shader-20260921/record-wave32-confirmed.txt),
  [compute-wave32.txt](../evidence/gpu-shader-20260921/compute-wave32.txt).
- The explicit-SMEM-zero probe inserted `s_mov_b32 s1, 0` and changed the
  scalar load from the `null` zero-offset operand to an explicitly zeroed SGPR. It
  completed a real fence query but produced the same failed output and a TCP
  write fault:
  [record-smem-zero.txt](../evidence/gpu-shader-20260921/record-smem-zero.txt),
  [compute-smem-zero.txt](../evidence/gpu-shader-20260921/compute-smem-zero.txt).
- The legal low-VA allocator diagnostic produced coherent recorded addresses
  (including output `0x0000000100010000`, shader code `0x0000000008000000`,
  and descriptor `0x0000000008040000` in the low-VA capture), but the submitted
  workload timed out and ended in a CPF fault after reset. It is rejected as
  a fix:
  [record-low-va.txt](../evidence/gpu-shader-20260921/record-low-va.txt),
  [compute-low-va.txt](../evidence/gpu-shader-20260921/compute-low-va.txt).
- The 32-bit-only low-VA variant kept command/output buffers in the original
  high aperture while moving shader/descriptor BOs below 4 GiB and setting
  `address32_hi=0`. Recording confirmed this split. Submission still timed
  out, followed by automatic resets and a CPF fault. Neither low-VA variant
  is accepted. The CPF fault captured after reset is not proven to be the
  initiating cause:
  [record-low-shader-va.txt](../evidence/gpu-shader-20260921/record-low-shader-va.txt),
  [compute-low-shader-va.txt](../evidence/gpu-shader-20260921/compute-low-shader-va.txt).
- Transfer-only recovery remains the accepted boundary; the compute shader
  has not passed. The broader submission status and transfer evidence are in
  [GPU_SUBMISSION_2026-09-21.md](GPU_SUBMISSION_2026-09-21.md).

The logged candidate hashes, where supplied by the diagnostic itself, are
retained in the evidence files. For example, the internal-readback candidate
hashes follow its timestamp; the wave32, SMEM-zero, and low-VA record
files likewise contain their staged-library hashes.

The [readback comparison](../evidence/gpu-shader-20260921/readback-audit.json)
independently checked all four descriptor words and 18 ISA words in both CP
captures, plus seven defined setup-register values in the second capture.
Unused USER_DATA registers were not treated as expected shader inputs.

## Isolation and scope

Each candidate was selected per process with an isolated ICD/library path and
shader cache disabled; no desktop configuration, NPU, resident CPU model, boot
service, kernel image, module, or partition was changed. The failing submissions
did trigger the kernel's automatic GPU recovery; no manual reset or phone
reboot was issued. These captures do
not establish production Vulkan support, desktop rendering, or model
offload.

## Literal-descriptor isolation

At 04:45:53 UTC, a special exact-shader-only diagnostic replaced the descriptor
load with four literal `s_mov_b32` instructions. Host LLVM verified their
GFX10.3 encodings. The recorder checked the exact descriptor words, real output
allocation address, zero dispatch offsets and 4/1/1 direct dispatch before
allowing execution. Unknown shaders or incompatible modes fail closed.

This still failed: real fence `expired=1`, first output `0xA5A5A5A5` rather than
7, exit 4, TCP write fault `0x0000ff7072d14000`. It is not a shader fix and does
not establish that the substituted instructions executed correctly. It does
not support blaming only the scalar descriptor load. Evidence:
[record-literal.txt](../evidence/gpu-shader-20260921/record-literal.txt),
[compute-literal.txt](../evidence/gpu-shader-20260921/compute-literal.txt).

## Reproducible diagnostic source, not an installed driver

The cumulative [diagnostic patch](../tools/omarchy-trial/radv-xclipse-shader-diagnostics.patch)
applies directly to the pinned fork base, not on top of the prior submission
patch. It retains the BO-list/native-fence repairs and contains explicit opt-in
readback, exact-ISA, and VA experiments. All new diagnostic modes default off.
Failed experiments are preserved for reproducibility, not promoted as fixes.

- Patch SHA256: `cb01e374118604d13bfc914eb284602504167f0ed47cf817553b84910da1ee82`.
- Final local-only diagnostic ICD SHA256:
  `51145f8dccf90d4253e5449532a2704fa1a41797fe91243e48e1dc4cf63c7fb5`.
- Final local-only diagnostic probe SHA256:
  `bfb99ebf8fb29b109d37f03b2a4a678b6983fd25154bb349bf3366151bf43ab4`.

Cross-build and source diff checks passed; the cumulative patch passed
`git apply --check` in a clean pinned-base worktree. The probe also compiled
on the host with `-Wall -Wextra -Werror`; missing arguments and all six pairs
of incompatible operating modes exited 2 before Vulkan loading. No host GPU
workload was run. Binaries, firmware, models and raw kernel captures are not
published. CPU mapping pointers are redacted; GPU VAs remain for diagnosis.

## Final phone check

At 04:49:35 UTC the original transfer-capable ICD hash still matched
`ffc1f7ea42afa180d924875b1ef9655511c8a560c46c9077ae8f89e1b7f5f5d0`.
Its transfer probe again validated every word, checksum
`0xd1cc9f9595a48f83`, real fence complete, exit 0, with no new GPU errors in
the captured kernel delta. BORE remained 760/RECOVERY, uptime 29336 seconds;
CPU model health was `ok`, WPA `COMPLETED`, and Hyprland, Quickshell and
Squeekboard were running. Battery was 30.1 C. These are point checks, not
new claims of touchscreen, telephony, Bluetooth or NPU support:
[final-health-transfer.txt](../evidence/gpu-shader-20260921/final-health-transfer.txt).

## Current conclusion

GPU-visible CP-DMA bytes, command registers, real fence completion, wave32
selection, explicit scalar-zero encoding, and a legal low-VA allocation policy
have each been separately observed. None fixes the compute workload. The root
cause of the shader failure remains unresolved; no general shader fix or GPU
model claim is made. Further work needs execution-time shader/VM evidence;
another model benchmark against this known failing path would not be useful.
