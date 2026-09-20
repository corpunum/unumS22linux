# RADV/SGPU fence failure analysis (host-only)

Historical analysis. Subsequent real-fence runs and a CPU-map lifecycle fix
are documented in `docs/DRIVER_MODELS_2026-09-20.md`. GPU computation still
faults. The standalone diagnostic patch was regenerated from the valid
combined patch before publication; the original handwritten hunk was corrupt.

The phone result is **not PASS**. The recorded run selected
`AMD Radeon Graphics (RADV VANGOGH)`, submitted `ip=0 ring=0 num_chunks=3`
with chunk IDs `1,1,5`, then returned `vkWaitForFences(..., 3s) = -13`
(`VK_ERROR_UNKNOWN`).

## Exact source finding

In `/tmp/radv-xclipse-audit.d1QiDb/src/amd/vulkan/winsys/amdgpu/radv_amdgpu_cs.c`:

* The user-fence chunk is disabled with `if (0 && has_user_fence)`.
* BO-handles submission is disabled for SGPU.
* Syncobj waits are disabled with `if (0 && sem_info->cs_emit_wait ...)`.
* The code queries `amdgpu_cs_query_fence_status()` but then unconditionally
  overwrites the result variable: `expired = 1; // FORȚEAZĂ SEMNALIZAREA HARDWARE INSTANTANEE`.
* If the query return `pr` is zero, it manually calls `drmSyncobjSignal()` on
  the queue syncobj and every `sem_info->signal.syncobj` immediately, rather
  than obtaining a kernel completion signal. This is an eager host-side signal,
  not proof that the IB ran or that the output buffer is complete.

The log's `expired=1` is therefore synthetic and cannot be interpreted as a
hardware fence result. The source has also disabled the normal synchronization
paths needed to connect Vulkan's fence to SGPU completion. The observed
`VK_ERROR_UNKNOWN` is consistent with this broken synchronization contract, but
the exact failing ioctl/return path still needs the requested strace and dmesg
capture; no single kernel cause is claimed here.

## Safe next experiment (requires owner coordination)

Do not bypass `vkWaitForFences`, accept a timeout as success, or read back and
label output PASS. First capture `strace -e ioctl` and dmesg around one run.
Then make an isolated source variant that only logs the unmodified
`pr`/`expired`, removes the unconditional `expired = 1`, and preserves the
fence result. Compare `vkGetFenceStatus` before/after `vkWaitForFences` and
record whether `vkQueueSubmit` itself returns success. Any manual syncobj
signal must remain diagnostic-only and must not be used for correctness.

The likely repair area is the SGPU synchronization ABI: whether this kernel
accepts `AMDGPU_CHUNK_ID_SYNCOBJ_OUT` (chunk 5), user-fence chunks, or only the
legacy sequence fence. That cannot be selected safely from Mesa source alone;
the ioctl trace must establish which request returns the error. No shared Mesa
worker source was modified by this audit.
