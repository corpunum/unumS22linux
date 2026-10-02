# ABOX audio IPC source observations (2026-10-02)

## Result and scope

`tools/hardware/audio-ipc-observation-fix.patch` adds three opt-in static
tracepoints at source boundaries missing from the 2026-10-01 capture profile.
The trace records contain only routing/result fields; they never copy or print
IPC payloads, DMA pointers, firmware content, or device pointers. They are
disabled by default. No tracefs event was enabled and no phone, stream,
firmware, module, image, or hardware operation was performed for this work.

The patch is based on source commit
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`, whose ABOX files match the
read-only derived source tree at `3fca50941422439b2019db2e4a3dc1016b2138a1`.
The host test verifies those commits, the SHA-256 of each exact fixture, and a
256 KiB per-file read cap before reconstructing a temporary tree and applying
the patch there.

## Events and interpretation

- `abox_pcm_trigger_queue` records each enqueue attempt for
  `IPC_PCMPLAYBACK` / `PCM_PLTDAI_TRIGGER`: an internal monotonic sequence,
  monotonic timestamp, IPC/channel/type, queue result, retry index, and the
  caller's atomic/synchronous flags. The event is emitted before the existing
  `queue_work()` call. A successful result means only that the local queue
  accepted the request.
- `abox_pcm_trigger_send` records the queued worker's return from
  `abox_ipc_send()` for that trigger, carrying the same local sequence when
  available, timestamp, IPC/channel/type, and local return value. This is the
  sender-call result only; it is not a firmware acknowledgement or proof that
  firmware acted on the request. It can be enabled independently of the queue
  event.
- `abox_rdma_pointer_handler` records timestamp, selected channel, and message
  type only after channel selection and the non-backend pointer-handler check.
  It deliberately has no fabricated sequence link: the pointer message carries
  no source trigger token. The pointer value remains private to the existing
  driver state/callback path and is absent from the event.

The correlation sequence is internal queue metadata, never part of an IPC
message. Sequence/timestamp work and allocation are avoided when observation is
disabled. Existing retry count, queue-work scheduling, queue ownership,
sender return handling, failsafe reporting, and pointer callback behavior are
preserved. Reused queue slots clear a stale nonzero sequence when the next
untraced item is inserted. The synchronous direct-send path is not represented
as an asynchronously queued request.

The pinned ABOX Makefile puts `abox.o` and `abox_rdma.o` in the same
`snd-soc-samsung-abox.o` composite. Because this patch changes the internal
`struct abox_ipc` layout, rebuild the whole composite/module from matching
sources; do not substitute only one object. The reviewed defconfig has
`CONFIG_SND_SOC_SAMSUNG_ABOX=m`, `CONFIG_TRACEPOINTS=y`, and `CONFIG_FTRACE=y`.
That establishes source/config feasibility only, not the running kernel's
configuration or tracepoint availability.

## Host-only verification

`tools/hardware/test-audio-ipc-observation.py` reconstructs four capped,
hash-verified pinned source fixtures, applies the patch in a temporary copy,
and extracts/compiles the actual patched queue put/get, scheduler, worker,
request, sender, and RDMA handler functions. Its host shims include the real
trace-event header so the actual `TP_fast_assign` field assignments execute.
The nine scenarios cover disabled-default side effects, asynchronous queue vs
sender boundary, sender-only selection, local sender failure, all queue-full
retries, capture/non-trigger filters, stale metadata on ring wrap, direct
synchronous send, and valid/invalid/backend RDMA callback selection. It checks
that disabled tracing does not call the monotonic clock, increment the
correlation counter, or allocate; host compilation is performed at `-O0` and
`-O2`.

Run from this worktree with the verified derived source tree:

```sh
python3 tools/hardware/test-audio-ipc-observation.py \
  --source-tree /home/corpunum/s22-workers/camera-kernel-build-20260927
python3 -O tools/hardware/test-audio-ipc-observation.py \
  --source-tree /home/corpunum/s22-workers/camera-kernel-build-20260927
PYTHONOPTIMIZE=1 python3 tools/hardware/test-audio-ipc-observation.py \
  --source-tree /home/corpunum/s22-workers/camera-kernel-build-20260927
```

All three runs passed: nine compiled scenarios at each C optimization level.
Each run performs normal `git apply --check` and application against its
temporary fixture copy. This is host/source evidence, not a complete kernel
build, a tracepoint ABI/runtime test, or audio acceptance.

## Remaining unknowns

The 19 retained `RUNNING` sample pairs from the capture profile still report
`hw_ptr=0` and RDMA disabled. The queue, sender, and pointer-handler markers
were absent from that retained summary, but coverage of those boundaries was
not established; these remain unknown observations and do not demonstrate DSP
failure. Firmware acknowledgement/behavior, DMA progress, physical audio
output, runtime tracepoint availability, and device acceptance remain
unverified. Enabling events and collecting a device trial require the
coordinator's separate owner-reviewed device workflow.
