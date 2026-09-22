# NPU POWER_NOTIFY enqueue/result foundation — 2026-09-22

Status: host-only compiled candidate; no timeout, cancellation, firmware boot,
device operation, or deployment. This is not evidence that NPU BOOTUP is safe.

## Change

`tools/hardware/npu-power-enqueue-result.patch` changes only
`npu_session_NW_CMD_POWER_NOTIFY()` in the pinned NPU source:

* capture the return from `npu_session_put_nw_req()`;
* return `-EIO` without waiting when enqueue returns zero or negative;
* after the existing unbounded wait, propagate `session->nw_result.result_code`;
* retain the existing wait, emergency handling, and all protocol `BUG_ON` paths.

It does not add a timeout or alter request/session ownership. A successful
enqueue can still leave a raw session pointer queued until mailbox completion;
the callback lifetime defect and BOOTUP unwind hazards remain exactly as
documented in `NPU_CALLBACK_LIFETIME_2026-09-22.md`.

## Exact-source application and compile receipt

The patch was applied only to the detached worktree:

* source: `builds/npu-enqueue-result-20260922`
* base: `4e5c5ad7d950e4de0688b5663965f2075654b2ad`
* patch check: `git -C builds/npu-enqueue-result-20260922 diff --check`
* target: actual `drivers/vision/npu/core/npu-session.c`
* configuration/toolchain: existing exact Clang 21 / captured config build
  receipt under `builds/close-range-clang21-llvm1-O-20260922`
* command: the recorded command in
  `drivers/vision/npu/core/.npu-session.o.cmd`, with only the source and
  output paths redirected to the detached worktree and `/tmp` object
* result: successful compilation with ThinLTO, CFI/KCFI, MODVERSIONS and
  kernel warning flags unchanged
* output: `/tmp/npu-session-enqueue-result.o`
* SHA-256: `19e2f0eb1de5ef5b76570d0f8175e5ac01aca7adf71d63d9d3b3471b7cdc14d1`

The object is LLVM IR bitcode, as expected for the captured ThinLTO build.
No live/reference kernel checkout was modified.

## Remaining limits

The result code is an NPU protocol error value, not necessarily a portable
negative errno; the caller currently returns it unchanged. Enqueue failure is
normalized to `-EIO` because no request result exists. The function still
waits indefinitely after a positive enqueue, and a later session close can
still race the callback unless the session-retention/close-barrier design is
implemented. This candidate therefore improves error propagation only and
must not be used to justify a BOOTUP ioctl or claim NPU usability.

## Independent firmware-path progress

The normal system path is source-backed separately: with the captured
`CONFIG_DSP_USE_VS4L=y`, `npu-binary.h` selects the bare request name
`AIE.bin`; `npu-system.c:1949-1955` calls the signature-reading path, which
ultimately requests that name. The recovered private artifact is recorded at
`rootfs/npu-firmware-closure-20260921/vendor/firmware/AIE.bin` with SHA-256
`a9843bbf520c08263c563f3200f0cbceb09c68fbdd1279d24236c3da704d9dc2`. It has
not been staged or loaded. `vectors.bin` has no caller in this pinned normal
boot path and is not a normal BOOTUP blocker. This filename/hash evidence is
independent of the enqueue-result change and does not establish loader,
signature, mailbox, or lifecycle safety.
