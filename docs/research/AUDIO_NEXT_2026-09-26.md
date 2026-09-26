# S22 audio next-stage capture — 2026-09-26

## Finding and remaining ambiguity

The last instrumented zero-stream already established **no RDMA2 movement**:
seven snapshots observed ALSA `RUNNING`, while `hw_ptr`, RDMA2 status `0x1230`
and status-add `0x1238` remained zero. ABOX was runtime-active, regmap
cache-only was `N`, service was `1`; the child hit its 10-second deadline and
the PCM was closed with both amp-enable controls off and route selectors
restored. This is stronger than an ALSA-pointer-only symptom, but it did not
capture the DPCM FE-to-BE state or the exact UAIF1 clock framework nodes. The
first stalled boundary therefore remains unlocalized.

The new observer samples the same timed stream at these ordered boundaries:

1. ALSA FE state and `RDMA2` DPCM backend state (`UAIF1`, if active).
2. UAIF1 BCLK, BCLK gate, and mux clock-framework bookkeeping.
3. RDMA2 status pair (`0x1230`, `0x1238`) and ALSA `hw_ptr` / period proxy.
4. Source-named DAPM widget states across the card, ABOX, and both CS35L41s.

Each observation is sequence- and monotonic-time-stamped; DPCM and clock
evidence is paired from the same sample. Progress, stationary-RDMA, and
RDMA-versus-`hw_ptr` comparisons require at least two adjacent captured samples
with increasing time. Gaps and missing observations remain unknown. A running
FE with “No active DSP links” points to the frontend-to-DPCM boundary. A
started UAIF1 backend with zero BCLK/gate enable counts points to the Linux DAI
clock-enable stage. Moving RDMA status with a stationary `hw_ptr` points past
DMA consumption toward firmware pointer IPC or ALSA notification. If DPCM and
clock-framework stages are reached but RDMA is stationary, the observer
deliberately leaves DSP consumption versus the physical clock unresolved.
There is no per-stream firmware IPC-response counter here, and Linux clock
counts/rates do not prove a physical pin waveform.

## Source basis and safe observation points

The pinned source is
`/home/corpunum/s22-linux/lineage/android_kernel_samsung_s5e9925` (read-only):

- `sound/soc/samsung/abox/abox_rdma.c:1601-1635` prepares SIFS and sends
  `PCM_PLTDAI_PREPARE`; lines `1640-1690` send start/stop trigger IPC.
- `abox_rdma.c:1329-1355` handles `PCM_PLTDAI_POINTER` by updating
  `data->pointer` and calling `snd_pcm_period_elapsed`. Lines `1692-1737`
  show the pointer callback reading RDMA status and using the progress bit,
  offset and count. `abox_soc_4.h:618-644` defines the status offsets and
  fields; the observer reads only the pre-existing metadata-checked RDMA2
  records.
- `sound/soc/soc-pcm.c:62-119,158-174` formats the DPCM FE, backend list and
  backend states, and creates `RDMA2/state` directly under the card debugfs
  root. This is why `/sys/kernel/debug/asoc/Rainbow-Prince/RDMA2/state` is
  sampled; the earlier `.../dpcm/RDMA2/state` path was at the wrong level.
- `abox_if.c:175-215` enables/disables the `bclk` and `bclk_gate` clocks in
  UAIF startup/shutdown. `arch/arm64/boot/dts/exynos/s5e9925.dts:5892-5903`
  maps UAIF1 ID 1 and names `bclk`, `bclk_gate`, `mux`; the binding IDs are in
  `include/dt-bindings/clock/s5e9925.h:41,51,60`.
- `drivers/clk/clk.c:3287-3293` returns cached `core->rate` for debugfs
  `clk_rate`; lines `3456-3457` expose cached prepare/enable counters. These
  reads do not call the clock provider or recalculate via MMIO. The observer
  still samples them **only** when ABOX runtime status is `active`, regmap
  `cache_only` is `N`, and ABOX service is `1`; otherwise it reports
  `skipped_pm_gate`, not missing nodes. Missing fixed clock/DAPM paths have
  separate per-node statuses and never become an “inactive” claim.
- `sound/soc/soc-dapm.c:2388-2471` reports DAPM widget power under the card
  DAPM mutex. The observer reads only the 24 source-named widget paths and
  does not enumerate debugfs or infer sound from `On` states.

The coordinator's latest bounded idle read found the PCM closed and ABOX
suspended/cache-only, `reset_count=0`, `service=1`, all 24 source-mapped DAPM
paths readable and `Off`, plus DPCM `RDMA2/Playback` state `new` with “No
active DSP links.” RDMA registers were explicitly skipped at idle. This proves
idle-path availability only, not an active route or DMA state. The card DAI
list is inventory only. Do not treat an absent old wrong-level DPCM/DAPM path
as a hardware fault.

## Proposed next operation (coordinator review required)

This is a proposed bounded state-changing experiment, **not authorized or
cleared to run by this host patch**. The legacy runner's global serialization
and staging behavior have not received a complete independent review; that
review must clear them before any live execution. Candidate identity and
preflight gates do not substitute for that runner review or separate approval.
If the coordinator separately approves after those reviews, the proposed
command from the audio worker checkout is:

```sh
cd /home/corpunum/s22-workers/audio-next-20260926
python3 -I -B tools/hardware/run-audio-route-prepare-once.py \
  audio-next-20260926 --execute --zero-second --sample-progress
```

The runner first verifies the existing completed HCI observer receipt and
current dynamic boot continuity, the expected GNU build ID
`b2dda820b18d410d9bf12f1bd2584567d545991d`, RECOVERY boot identity, and the
full `/dev/block/by-name/recovery` SHA-256
`42da267f3dd9f94f30f62a95fb2ac13f91d4cf98f1a2307f7cc14e45d9c49be5`. `uname`
release alone is not an identity check. It uses the original reviewed
`/home/corpunum/s22-linux` checkout only for the existing pinned USB wrapper,
known-hosts, and transport helper; audio code and private trial receipts stay
in the worker checkout. It does not inspect, remove, or reuse the consumed HCI
one-shot marker, and it does not publish the dynamic boot ID.

Before any selector/PCM operation, the runner requires the native guardian,
RainbowPrince card and exact PCM device `116:3`, connected ECM carrier,
`pcm2p/sub0/status=closed`, ABOX service `1` and reset count `0`, both
`Left/Right AMP Enable Switch=off`, and each of `ABOX SPUS OUT2` and
`ABOX UAIF1 SPK` at the exact `RESERVED (0)` value with `SIFS0` available.
Identity failures and mismatched boot continuity stop before audio preflight;
preflight failures stop before route writes. Immediately before opening PCM,
the wrapper rechecks guardian, closed PCM, both amp-enable controls off and
both route selectors still at `RESERVED (0)`. The only mixer writes are those
two selectors to `SIFS0`; it changes no gains, amp enables or pin switches.

The child submits one second of digital zero to native `hw:0,2`, 48 kHz,
stereo S16_LE, nonblocking, with 1024-frame periods and an 8192-frame buffer.
The wrapper samples once per second with the bounded observer and enforces a
10-second child deadline. Ordinary PCM open/start/close may runtime-wake ABOX;
there is no explicit power sysfs write, ABOX unbind/rebind, reset or firmware
log flush. Route cleanup runs only after the child is reaped **and** the PCM
status is closed. It then restores each captured selector value, verifies
both amp enables remain off and records any cleanup error. If the child cannot
be reaped or PCM is not closed, it does not restore selectors automatically;
stop and inspect that trial receipt before any retry. The run performs no
capture and is not a listening/acoustic test.

Immediately afterward, review the private receipt and confirm same boot,
candidate identity, PCM closed, exact selector restoration, both amp enables
off, service `1`, reset count `0`, and cleanup verification before interpreting
the stage assessment. Any failed precondition, incomplete capture or failed
cleanup is a stop condition, not a reason to retry blindly. Even a complete
capture cannot establish physical BCLK waveform or audible output.

## Host-only verification

The focused suites are directly runnable from the checkout; all must remain
valid under optimized Python (so safety gates cannot depend on `assert`):

```sh
python3 -I -B tools/hardware/test-audio-route-assessment.py
python3 -I -B tools/hardware/test-audio-progress-snapshot.py
python3 -I -B tools/hardware/test-audio-wrapper-cleanup.py
python3 -O -I -B tools/hardware/test-audio-route-assessment.py
python3 -O -I -B tools/hardware/test-audio-progress-snapshot.py
python3 -O -I -B tools/hardware/test-audio-wrapper-cleanup.py
```

These tests prove host-side parsing, fail-closed stage classification,
candidate-gate ordering, and wrapper cleanup logic only. They are not device or
audio-functionality evidence.
