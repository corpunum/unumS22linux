# S22 bounded audio stage probe — 2026-09-27

## Actual node-repaired trial and independent interpretation

The coordinator ran `audio-zero-node-20260927` once after independent review.
The exact missing PCM node was created; PREPARE and seven write ioctls succeeded.
The child reached its ten-second deadline (62 EAGAINs), was reaped, and the
selectors, amplifier mute, PCM closure and same-boot health were verified.
The diagnostic failed; the guard records `failed-cleanup-confirmed`.

Independent host review by `/root/driver_review_20260926` confirmed 19 contiguous
RUNNING samples with `appl_ptr=8192`, `hw_ptr=0`, RDMA2 CTRL enable=0, and zero
STATUS progress/position. RDMA2 FE and UAIF1 BE report start; BCLK divider/gate
enable counts are one. These are software observations, not measured clock pins.
The SIFS0 and SPUS OUT2-SIFS0 DAPM widgets remain Off, a separate power-graph
observation. No audible-output or proven physical-DMA-failure claim follows.

Pinned `abox_rdma_trigger()` sends `PCM_PLTDAI_TRIGGER` asynchronously
(`atomic=1,sync=0`); successful queueing is not DSP completion. Firmware's later
`PCM_PLTDAI_POINTER` callback updates the pointer. There is no exposed per-stream
ack counter. The evidence localizes the missing progress at/after firmware
task-trigger to RDMA activation/pointer return, but does not distinguish the
exact cause. The retained kernel delta contains normal firmware-ready, params,
trigger/start/stop and shutdown messages, without an ABOX fault. Postflight
shows service=1/reset=0, suspended runtime/cache-only and routes RESERVED.
The exact root:root/0600 node intentionally remains for this boot; this is not
a persistent bootstrap repair. No further audio trial was automatically run.

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
The earlier note cited `abox_cmpnt_3.c`, but that is not the component selected
by this build: its kernel config is `CONFIG_SND_SOC_SAMSUNG_ABOX_VERSION=0x40001`
with V4 enabled, and the ABOX Makefile selects `abox_soc_4.o`, `abox_soc.o`, and
`abox_cmpnt.o`. In that V4 graph, the path is
`SPUS OUT2 -> SPUS OUT2-SIFS0 -> SIFS0 -> SIFS0 PGA -> STMIX -> SIFS0 OUT ->
UAIF1 SPK -> UAIF1 PLA` (`abox_cmpnt.c:6776,6861,6944,6952,6954,6970,7026`).
`STMIX` and `SIFS0 PGA` are NOPM graph widgets with no local kcontrol in this
component source. The UAIF1 speaker enum maps index 0 to `RESERVED` and index 1
to `SIFS0` (`abox_cmpnt.c:5719-5724`); the bounded stream temporarily set
`ABOX SPUS OUT2=1` and `ABOX UAIF1 SPK=1` (SIFS0), then verified cleanup restored
both to 0. Those active-trial writes and the later cleanup state must not be
conflated. Runtime topology/template effects remain outside this
static-source check.

The DPCM FE walk starts from the FE CPU DAI and stops when it reaches a `no_pcm`
backend DAI widget (`soc-pcm.c:1251-1269,1271-1288`); `dpcm_add_paths()` connects
the matching BE at that widget (`soc-pcm.c:1371-1388`). Source therefore does
not support that `AMP Enable Switch=off` itself severs FE-to-BE DPCM traversal.
That fact does **not** establish a powered V4 DAPM path or prove RDMA2 progress.
In the node-repaired receipt, three sampled ABOX widgets
(`SPUS OUT2-SIFS0`, `SIFS0`, `SIFS0 OUT`) read Off in each of 19 RUNNING samples;
`UAIF1 SPK` and `UAIF1 PLA` were not sampled there. Those are partial software
widget snapshots, not proof of why the DMA pointer stayed at zero. A separate
read-only audit after cleanup found `SIFS0 OUT Switch=on` and `UAIF1 Switch=on`,
but PCM was closed and both route selectors were back at 0; all-Off idle widgets
are not the trial's active route state. The expected ASoC DAPM widget
power tracepoint was not present in either checked tracefs event tree. Keep the
speaker amps off and gain unchanged; neither amp-off nor these partial/idle
observations diagnose the zero pointer or support an audio-output claim.

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
