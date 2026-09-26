# S22 bounded audio stage probe — 2026-09-27

## Purpose and evidence boundary

This change prepares one coordinator-reviewed, one-second digital-zero playback
on the native RDMA2 PCM. Its purpose is to capture the first active boundary
that does not advance: ALSA frontend, DPCM backend link, Linux UAIF1 clock
bookkeeping, RDMA2 source registers, or the ALSA `hw_ptr` notification path.
It is not a listening test and does not establish a physical BCLK waveform or
audible output.

The previous run observed ALSA `RUNNING` with `hw_ptr=0` and RDMA2 status and
status-add both zero, but did not have useful simultaneous DPCM/clock evidence.
Idle evidence from the coordinator established only that the named source
paths are readable while the PCM is closed: ABOX suspended/cache-only, service
1, reset count 0, and the 24 source-mapped DAPM widgets `Off`. No route or PCM
operation was performed for this patch.

## Why both amplifier-enable controls remain off

The pinned read-only vendor source separates the codec amplifier path from the
ABOX frontend-to-backend traversal. `cs35l41.c:386-387,1121,1192-1194` defines
`AMP Enable` as a DAPM switch between `AMP Playback` and codec-side ASPRX1/2.
The ABOX DAPM graph contains the selected `SPUS OUT2 -> SIFS0 -> SIFS0 OUT ->
UAIF1 SPK -> UAIF1 PLA` path (`abox_cmpnt_3.c:4852-4855,4940,5011,5025,5074`).
The DPCM FE walk starts from the FE CPU DAI and stops when it reaches a `no_pcm`
backend DAI widget (`soc-pcm.c:1251-1269,1271-1288`); `dpcm_add_paths()` connects
the matching BE at that widget (`soc-pcm.c:1371-1388`). The reviewed route
selectors and FE/BE link are therefore observable before the codec amp switch.

Source does **not** support the claim that `AMP Enable Switch=off` itself
disconnects the RDMA2 FE from the UAIF1 ABOX backend or prevents observing RDMA2
progress. It does constrain the codec-side signal path and intentionally keeps
the speaker amps disabled. The route, amp-off, and no-gain-change guards stay
in place; a muted diagnostic cannot be promoted into a codec-output or sound
claim.

## Runner boundaries

The only `--execute` identity is `audio-zero-20260927`; aliases/retries are
rejected. It takes the shared host-global operation lock, performs the
read-only candidate and audio preflight while holding that lock, and fsyncs the
durable pending marker immediately before the first remote filesystem write.
The guard marker is not deleted: pending/UNKNOWN requires explicit evidence-
based reconciliation and blocks retry.

The runner requires the reviewed HCI candidate's GNU build ID
`b2dda820b18d410d9bf12f1bd2584567d545991d`, RECOVERY identity and full
RECOVERY SHA-256 `42da267f3dd9f94f30f62a95fb2ac13f91d4cf98f1a2307f7cc14e45d9c49be5`,
plus same-boot continuity. It does not rely on uname release and does not
inspect or reuse the consumed HCI marker. Dynamic boot identifiers stay only
in owner-private host receipts.

Before mutation, it checks native guardian, RainbowPrince and PCM `116:3`,
ECM carrier, PCM closed, ABOX service 1/reset count 0, both AMP Enable controls
off, and both selectors at RESERVED (0) with SIFS0 available. The wrapper
rechecks the same critical route/amp/closed-PCM state before opening PCM. Its
only mixer writes are the two selectors to SIFS0; it makes no gain, amp-enable,
pin-switch, capture, ABOX unbind/reset, or firmware-log-flush operation.

The trace destination is fixed at
`/srv/s22/audio-trials-20260927/audio-zero-20260927/trace.strace`. Staging
opens each component with no-follow directory descriptors; checks root owner,
mode, and separation from `/srv`; checks free bytes/inodes; creates a private
unique trial directory; and reserves the trace with O_EXCL/no-follow. The
bounded readback reopens the fixed path without following links and requires
the reserved device/inode, private regular-file mode, one link, and size at
most 2,000,000 bytes. It reads exactly the initially observed size and checks
the size again after the read, so a short read cannot be reported as a complete
trace. Existing/stale trial paths fail closed; the runner never truncates an
unvalidated old trace. Host artifacts are O_EXCL owner-only files under
`rootfs/audio-trials-20260927/audio-zero-20260927/`.

With progress sampling enabled, the wrapper takes an immediate cold-start
snapshot after spawning the PCM child, then polls at 200 ms intervals while
the child is alive. A one-second child can therefore yield several active
samples rather than only one endpoint sample. The hard child deadline remains
10 seconds and never extends the requested one-second digital-zero playback.
The observer's sequence/time continuity, runtime-PM gate, and same-sample
source pairing remain required; absent/gapped evidence stays unknown. Cleanup
still occurs only after the child is reaped and PCM status is closed. Host
timeout, unconfirmed cleanup, or an ambiguous remote trace-staging failure
leaves the durable operation pending/UNKNOWN; there is no automatic retry.
For a remote-stage exception, the private disposition records that staging may
be partial and that this runner did not invoke route or PCM operations. It does
not claim the remote filesystem was unchanged, and it does not terminalize the
operation; a partial reservation must be inspected and explicitly reconciled
before any different operation is authorized.

## Exact coordinator command (review and authorization still required)

This is the single prepared invocation; it has not been run by this worktree.
An independent code review of the guard integration and coordinator approval
must precede execution.

```sh
cd /home/corpunum/s22-workers/audio-trial-20260927
python3 -I -B tools/hardware/run-audio-route-prepare-once.py \
  audio-zero-20260927 --execute --zero-second --sample-progress
```

Afterward, accept evidence only if same boot/candidate identity, child reaped,
PCM closed, selectors restored to RESERVED (0), both amp enables still off,
service/reset gates pass, trace inode/readback is valid, and cleanup is
verified. If the DPCM FE reports no active DSP links, the frontend/backend
boundary is the finding. If UAIF1 started while source-mapped BCLK/gate enable
counts are zero, localize at the Linux clock-enable bookkeeping stage. If
RDMA2 moves while paired ALSA `hw_ptr` does not, report the pointer/IPC/
notification boundary only. Stationary RDMA2 after reaching earlier stages
still does not distinguish DSP consumption from physical clock output.

## Host-only verification

Run from the worker checkout:

```sh
python3 -I -B tools/hardware/test-audio-route-assessment.py
python3 -I -B tools/hardware/test-audio-wrapper-cleanup.py
python3 -O -I -B tools/hardware/test-audio-route-assessment.py
python3 -O -I -B tools/hardware/test-audio-wrapper-cleanup.py
```

The assessment suite exercises the real embedded staging/readback snippets on
a temporary filesystem, including exclusive reservation, stale-path refusal,
symlink ancestry refusal and inode replacement refusal. Wrapper tests execute
the embedded route wrapper with mocked mixer/PCM calls for success, timeout,
route-restore failure and subsecond sample cadence. These are host safety and
classification tests only—not evidence that the phone route or DMA works.
