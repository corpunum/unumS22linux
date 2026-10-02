# ABOX trigger request error path (2026-10-02)

## Finding

The pinned RDMA trigger helper changed its software duplicate-suppression bit
before asking ABOX to publish the request:

```c
if (data->enabled == start)
	return 0;

data->enabled = start;
...
return abox_rdma_request_ipc(data, &msg, atomic, 0);
```

If the queue rejects that request, `abox_rdma_request_ipc()` returns a negative
error but `data->enabled` keeps the requested value. A repeated START or STOP
then takes the early return above and reports success without submitting the
request again. The field is only read and written in this helper in the pinned
`abox_rdma.c`, so this stale value directly suppresses the caller's next
attempt.

The repair in
`tools/hardware/audio-ipc-error-path-2026-10-02.patch` stores the immediate
request return and commits `data->enabled = start` only when the return is
nonnegative. A negative queue result leaves the previous state intact, so a
later caller-controlled START/STOP can submit a fresh request. It adds no
automatic retry and does not change queue ownership or scheduling.

`data->enabled` is an unsynchronized field in the pinned driver. The extracted
tests are single-threaded and do not establish ordering between concurrent PCM
trigger and DAI mute callbacks. That caller-serialization assumption needs
independent review before device deployment; this patch adds no lock.

## Source and return boundaries

The patch targets ABOX source commit
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`, also present as the base commit in
derived read-only tree
`3fca50941422439b2019db2e4a3dc1016b2138a1`.

The production PCM callback maps START/RESUME/PAUSE_RELEASE to `start=true` and
STOP/SUSPEND/PAUSE_PUSH to `start=false`, then returns the helper's result. The
helper calls `abox_rdma_request_ipc(..., atomic, 0)`. Thus even for the callback's
`atomic=true` path, `abox_request_ipc()` takes the asynchronous scheduling path:
the immediate result is queue insertion, not the later sender result. The
pinned queue insertion returns 0 or a negative error (`-EBUSY` for full,
`-EINVAL` for an oversized message). Later, `abox_process_ipc()` calls
`abox_ipc_send()`; negative send results are sent to the existing failsafe
path. In the pinned ABOX IPC setup, the message layer's configured result
values are 0 and `-EIO`.

Consequently, the repaired `data->enabled` means “the most recently accepted
local trigger request.” It does not mean the asynchronous sender succeeded,
firmware acknowledged the trigger, the RDMA engine was enabled, DMA advanced,
or audio was produced. The existing
[`AUDIO_IPC_OBSERVATION_2026-10-02.md`](AUDIO_IPC_OBSERVATION_2026-10-02.md)
trace patch records queue and sender boundaries, but no firmware trigger
completion response is defined by this code path. Later asynchronous send
failure remains an unknown remote state; this patch does not roll back or
retry it.

The saved capture still has `hw_ptr=0` and RDMA disabled in its 19 RUNNING
samples. Its missing queue, sender, and selected-handler markers have unknown
coverage and remain unknown evidence. The source repair does not explain that
historical stall or establish firmware failure, DMA progress, or physical
audio.

## Ordered patch application

Apply the already-reviewed observation patch first, then this error-path
patch. This preserves the source order and keeps both changes reviewable as
separate patches:

```sh
git apply --check tools/hardware/audio-ipc-observation-fix.patch
git apply tools/hardware/audio-ipc-observation-fix.patch
git apply --check tools/hardware/audio-ipc-error-path-2026-10-02.patch
git apply tools/hardware/audio-ipc-error-path-2026-10-02.patch
```

The host test repeats ordinary `git apply --check` and application in that
order on a temporary copy of the exact pinned fixture. It runs the extracted
production `abox_rdma_trigger_ipc()` and `abox_rdma_trigger()` functions from
both baseline and patched source. Its injected queue outcomes reproduce the
baseline stale START and STOP state, then verify the patched caller preserves
state on rejection and permits a later explicit request. It also covers
successful state commit, duplicate suppression, invalid commands, backend
bypass, IPC fields, and the existing atomic asynchronous scheduling arguments.

Run the fixture test using either the local read-only derived tree or the
public pinned-file path:

```sh
python3 tools/hardware/test-audio-ipc-error-path.py \
  --source-tree /home/corpunum/s22-workers/camera-kernel-build-20260927
python3 -O tools/hardware/test-audio-ipc-error-path.py \
  --source-tree /home/corpunum/s22-workers/camera-kernel-build-20260927
PYTHONOPTIMIZE=1 python3 tools/hardware/test-audio-ipc-error-path.py \
  --source-tree /home/corpunum/s22-workers/camera-kernel-build-20260927

python3 tools/hardware/test-audio-ipc-error-path.py
python3 -O tools/hardware/test-audio-ipc-error-path.py
PYTHONOPTIMIZE=1 python3 tools/hardware/test-audio-ipc-error-path.py
```

Each Python invocation compiles the extracted actual C at `-O0` and `-O2`.
The test relies on the existing IPC observation test's capped, hash-verified
public fixture loader; only the four pinned source files are fetched, each at
most 256 KiB with a five-second timeout. A public fixture environmental
unavailability is reported as exit 77. An explicit but invalid local tree is
a hard failure and does not fall back to the network.

All six modes passed against both source routes. At each C optimization level,
the baseline reproduced the expected stale-state START/STOP failures and the
patched production functions passed. Patch application checks passed in the
documented order. These are extracted-C and host fixture results only: no
kernel build, phone, SSH, ADB, PCM stream, firmware, or hardware operation was
used. The patch has not been deployed or accepted on the device.
