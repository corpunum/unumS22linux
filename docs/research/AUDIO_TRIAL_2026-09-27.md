# S22 bounded audio stage probe — 2026-09-27

## Purpose and evidence boundary

This change prepares one coordinator-reviewed, one-second digital-zero playback
on the native RDMA2 PCM. Its purpose is to capture the first active boundary
that does not advance: ALSA frontend, DPCM backend link, Linux UAIF1 clock
bookkeeping, RDMA2 source registers, or the ALSA `hw_ptr` notification path.
It is not a listening test and does not establish a physical BCLK waveform or
audible output.

The first supervised zero-stream attempt was consumed under
`audio-zero-20260927`, but failed before ALSA could open the PCM: the traced
`openat()` of `/dev/snd/pcmC0D2p` returned `ENOENT`. No PCM ioctl, PREPARE,
sample, or DMA observation occurred. The route selectors were restored to
RESERVED (0), both amplifier enables remained off, PCM status stayed closed,
and the same boot/model health remained. This is a missing device-node finding,
not a DMA or DSP-stall diagnosis. Idle evidence from the coordinator separately
established that the named source paths are readable while the PCM is closed:
ABOX suspended/cache-only, service 1, reset count 0, and the 24 source-mapped
DAPM widgets `Off`.

The failed operation revealed matching ALSA sysfs registration: the class
entry and `/sys/dev/char/116:3` resolve to the RDMA2 playback PCM, with uevent
`MAJOR=116`, `MINOR=3`, `DEVNAME=snd/pcmC0D2p`, `DEVTYPE=pcm`. Both native-root
views lacked the corresponding `/dev/snd/pcmC0D2p` node.

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

The only next `--execute` identity is `audio-zero-node-20260927`; aliases and
retries are rejected. The prior `audio-zero-20260927` trace, receipt, and guard
marker remain untouched. The new operation takes the shared host-global lock,
performs the read-only candidate/audio preflight while holding it, and fsyncs
its durable pending marker immediately before the exact PCM node provision.
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

The node helper rechecks the fixed card ID, closed PCM status, both exact sysfs
links and the full `116:3` uevent identity. If the node is still missing, it
creates only `/dev/snd/pcmC0D2p` as root:root mode `0600`; `mknod` is anchored
to the opened `/dev/snd` directory and fails with `EEXIST` if the basename
appears concurrently. A wrong type, symlink, device number, or permissions
fail closed without replacement. It does not open PCM, start `mdev` or
`ueventd`, scan other devices, or alter the boot scripts. This is a current-boot
fix only: `/dev` is a tmpfs, and a separate reviewed post-registration startup
hook would be needed for persistence.

The pinned guardian sources explain the bounded repair target: `initramfs/cinit12.c`
early-creates only `/dev/null`, `/dev/kmsg`, `/dev/console`, `/dev/watchdog`,
and `/dev/block/sda33`. `tools/native-handoff/native-start[-persistent]`
performs a one-time `mdev -s` or `/sys/dev/{char,block}` scan and then creates
its fixed base nodes; these scripts do not run a persistent uevent listener.
The observed sysfs-present/devnode-missing state is consistent with ALSA
registering after that scan. Exact event timing is not inferred from source
alone. A separately reviewed persistence fix could add a bounded wait in the
native-session startup path for this exact class/devchar identity, then call
the same one-node helper; it should not trigger a global device rescan. No
startup hook is changed by this diagnostic patch.

The trace destination is fixed at
`/srv/s22/audio-trials-20260927/audio-zero-node-20260927/trace.strace`. Staging
opens each component with no-follow directory descriptors; checks root owner,
mode, and separation from `/srv`; checks free bytes/inodes; creates a private
unique trial directory; and reserves the trace with O_EXCL/no-follow. The
bounded readback reopens the fixed path without following links and requires
the reserved device/inode, private regular-file mode, one link, and size at
most 2,000,000 bytes. It reads exactly the initially observed size and checks
the size again after the read, so a short read cannot be reported as a complete
trace. Existing/stale trial paths fail closed; the runner never truncates an
unvalidated old trace. Host artifacts are O_EXCL owner-only files under
`rootfs/audio-trials-20260927/audio-zero-node-20260927/`.

With progress sampling enabled, the wrapper takes an immediate cold-start
snapshot after spawning the PCM child, then polls at 200 ms intervals while
the child is alive. A one-second child can therefore yield several active
samples rather than only one endpoint sample. The hard child deadline remains
10 seconds and never extends the requested one-second digital-zero playback.
The observer's sequence/time continuity, runtime-PM gate, and same-sample
source pairing remain required; absent/gapped evidence stays unknown. Cleanup
still occurs only after the child is reaped and PCM status is closed. Host
timeout, unconfirmed cleanup, or an ambiguous node/trace staging failure
leaves the durable operation pending/UNKNOWN; there is no automatic retry.
The private disposition says whether the exact PCM node may have been created,
whether remote trace staging may be partial, and that this runner did not
invoke route or PCM operations. It never claims remote storage was unchanged
and does not terminalize an ambiguous operation.

## Exact coordinator command (review and authorization still required)

This is the next prepared invocation; the prior trial ID is consumed. An
independent code review of the exact node helper and guard integration, followed
by coordinator approval, must precede execution.

```sh
cd /home/corpunum/s22-workers/audio-trial-20260927
python3 -I -B tools/hardware/run-audio-route-prepare-once.py \
  audio-zero-node-20260927 --execute --zero-second --sample-progress
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
