# ABOX PCM trigger queue/state ownership repair — 2026-10-02

## Result

The previous repair committed the RDMA duplicate-suppression bit only after
`abox_request_ipc()` returned. That return follows queue insertion, but the
queue lock has already been released. An overlapping FE START and BE STOP can
therefore observe the old bit between publication and commit: the STOP may
take the duplicate early return even though START is already in the queue.
Conversely, committing before insertion loses state on a full-queue error.

`tools/hardware/audio-ipc-error-path-2026-10-02.patch` replaces that split
operation with an internal `abox_request_pcm_trigger_ipc()` path. The
duplicate-state check, successful ring insertion/index advance, and
`data->enabled` update now occur under the existing `ipc_queue_lock`. That lock
is the linearization point for both queue order and the cached state:

1. If the cached state already equals the requested direction, the trigger is
   a duplicate/no-op. For a direction changed by an earlier request, that state
   could only have been committed after successful insertion under this lock;
   the initial already-stopped state remains the existing STOP no-op. It
   returns success without queue work or a queue trace event, preserving the
   prior duplicate fast path.
2. If the state differs and the ring has room, the code fills the queue entry,
   advances `ipc_queue_end`, and commits the new state before unlocking.
   Concurrent FE/BE requests then observe the accepted state and append in
   lock/FIFO order.
3. A full or invalid queue insertion leaves the state unchanged. The caller
   still receives the insertion error and can retry explicitly.

The borrowed `bool *enabled` exists only for the synchronous request call; it
is not stored in the queue or a callback. The generic `abox_request_ipc()`
signature and behavior remain unchanged, including its atomic+synchronous
direct-send branch. The specialized trigger entry stays internal to the
ABOX module composite (`abox.o` and `abox_rdma.o` share that composite in the
pinned Makefile); it does not add an exported symbol.

The existing `queue_work()`, queue-full retry count, atomic delay, and
non-atomic `flush_work()` behavior are unchanged. The queue lock is released
before scheduling, delay, or flush; no lock is held over `flush_work()`. An
accepted result still means local queue acceptance only. Later sender failure
is an unknown remote outcome: the cached bit is not rolled back and this patch
adds no automatic retry. The path defines no firmware trigger-completion
acknowledgement.

## Caller behavior and limits

The FE `.trigger` callback and BE `.mute_stream` callback use the same
trigger-specific queue path. The host regression extracts both callbacks and
executes their shared helper. It also extracts the pinned
`snd_soc_dai_digital_mute()` wrapper and actual DAPM `snd_soc_dai_link_event()`:
the wrapper propagates `-EBUSY`, while the DAPM POST_PMU caller warns and resets
its return to success. The pinned `soc_pcm_prepare()` and `soc_pcm_hw_free()`
callers also ignore `snd_soc_dai_digital_mute()` results; they were source
inspected, not extracted into this bounded C harness. Thus a BE error can be
unobservable to some higher-level ASoC callers even though the state bit no
longer claims a request that was not queued.

The saved muted stream still has 19 RUNNING samples with `hw_ptr=0` and RDMA
disabled. The absent queue/sender/pointer markers in that retained summary
have unknown historical coverage, not proven failure. This host repair does
not explain the DMA stall, prove sender success or firmware action, establish
DMA progress, or accept physical speaker/microphone output. No phone, SSH,
ADB, stream, trace-policy, firmware, gain, amplifier, kernel build, or ABOX
teardown operation was performed.

## Ordered patches

Apply the observation patch first and the queue/state repair second:

```sh
git apply --check tools/hardware/audio-ipc-observation-fix.patch
git apply tools/hardware/audio-ipc-observation-fix.patch
git apply --check tools/hardware/audio-ipc-error-path-2026-10-02.patch
git apply tools/hardware/audio-ipc-error-path-2026-10-02.patch
```

The second patch is based on source commit
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`, also contained in the read-only
derived tree at `3fca50941422439b2019db2e4a3dc1016b2138a1`. It modifies only
`abox.c`, internal `abox.h`, and `abox_rdma.c` in the patch artifact.

## Host verification

`tools/hardware/test-audio-ipc-error-path.py` reconstructs six capped,
SHA-256-pinned source files: the four existing ABOX fixtures plus
`sound/soc/soc-dai.c` and `sound/soc/soc-dapm.c`. The optional local source-tree
path must match the exact derived HEAD and pinned blobs; public fetching uses
the existing strict 256 KiB per-file cap and five-second timeout. Both
`git apply --check` and ordinary patch application run in observation-then-fix
order on a temporary fixture tree.

The C harness extracts the real queue empty/full/put/get functions, process
boundary, scheduler, generic and specialized request APIs, RDMA request
wrapper, FE helper/callback, BE mute callback, `snd_soc_dai_digital_mute()`,
and the DAPM link event. With pthread-backed queue locking and a controlled
`queue_work()` gate, FE START is paused after ring insertion but before its
helper returns; the overlapping BE STOP must observe the committed state,
enqueue after START, and leave exactly START→STOP in FIFO order. Other cases
cover queue-full START/STOP rejection and explicit retry, unchanged retry and
scheduling counts, duplicate suppression without queue work, generic IPC
non-deduplication and direct synchronous send, BE error propagation, and the
actual DAPM swallowed-error path. Before the repair, the extracted original
helper reproduces exactly the four rejected-state/retry failures; after it,
all cases pass. The test compiles both versions at C `-O0` and `-O2`.

All three Python modes passed with the exact local derived tree:

```sh
python3 tools/hardware/test-audio-ipc-error-path.py \
  --source-tree /home/corpunum/s22-workers/camera-kernel-build-20260927
python3 -O tools/hardware/test-audio-ipc-error-path.py \
  --source-tree /home/corpunum/s22-workers/camera-kernel-build-20260927
PYTHONOPTIMIZE=1 python3 tools/hardware/test-audio-ipc-error-path.py \
  --source-tree /home/corpunum/s22-workers/camera-kernel-build-20260927
```

Public pinned-fixture fetch passed in normal Python, `python3 -O`, and
`PYTHONOPTIMIZE=1`. The last mode first returned the documented environmental
skip (exit 77) while the network was unreachable, then passed when retried
after connectivity recovered. These are extracted-C host tests, not a full
kernel build or device/runtime acceptance.
