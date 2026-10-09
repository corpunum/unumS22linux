# S22 synchronized audio DMA/IPC evidence check — 2026-09-27

## Scope

`tools/hardware/audio-dma-evidence.py` is a host-only interpreter for an
existing route receipt, its kernel-delta text, and optionally a sanitized
`audio-log-coverage.py` report. It reuses the existing IPC marker parser; it
does not contact the phone, open Memlogger readers, alter logging policy, or
collect new data. Output is aggregate-only and omits raw receipt/log payloads.
This does not authorize or prepare another audio stream.

The sample pairing follows the observer's explicit `CLOCK_MONOTONIC` window:
for a RUNNING sample, ALSA status and the RDMA2 register record must each have
successful timed reads wholly inside that same sample window. The sample's
PM gate, trial identity, flat/decode register agreement, and timestamps must
also validate. The reads are sequential within a bounded window, not
simultaneous. It reports paired sampled values and changes between adjacent
samples only; it does not infer DSP consumption, firmware delivery, callback
completion, a physical clock, or audible output.

Kernel-delta marker counts are kept in a separate aggregate lane and are not
time-joined to individual userspace samples. A current Memlogger-policy report
can describe current filtering, but never establishes the policy or
per-callsite capture coverage during a past trial. Missing or filtered markers
therefore remain unknown, not evidence that the corresponding boundary was
not reached.

## Retained receipt/delta interpretation

Running the host parser against the retained private
`audio-zero-node-20260927` receipt and kernel delta yielded:

| Evidence | Sanitized observation |
| --- | ---: |
| RUNNING ALSA samples | 19 |
| Same-sample ALSA/RDMA2 timed pairs | 19/19 |
| Maximum ALSA/RDMA2 read-midpoint separation | 169,160,664 ns |
| Adjacent paired comparisons | 18 |
| Sampled `hw_ptr` values | `{0}` |
| RDMA2 CTRL enable / status progress | `{false}` / `{false}` |
| RDMA2 status offset / count / current address | `{0}` / `{0}` / `{0}` |
| Device-prefixed RDMA2 trigger API markers in delta | 2 |
| Async schedule / matching sender / pointer-handler markers | 0 / 0 / 0 |

These are observations from the saved trial, not a new device read. The
sampled flat values support “no change in these sampled registers and pointer
between adjacent paired samples”; they do not localize the reason. The two
trigger markers mean API entries only. The saved log-coverage audit found
current `abox-mem` level 2 below the level-5 debug threshold and `abox-file`
disabled, but those post-trial settings do not establish historical coverage.
The parser consequently reports absent IPC markers as unknown, not failed
delivery or proof that a callback did not occur.

The source boundary in
[the pinned ABOX IPC note](AUDIO_IPC_2026-09-27.md) remains applicable:
`abox_rdma_trigger_ipc()` queues asynchronous work; a schedule/host-sender
marker is not firmware completion; and the `PCM_PLTDAI_POINTER` handler is a
separate later boundary. The progress snapshot's timestamps pair userspace
reads; the aggregate printk delta does not expose per-sample callback IDs.

## Use and tests

Run locally against private files; do not publish raw inputs or traces:

```sh
python3 -I -B tools/hardware/audio-dma-evidence.py \
  /path/to/private/receipt.json /path/to/private/kernel-delta.txt \
  --log-coverage /path/to/sanitized-audio-log-coverage.json

python3 -I -B tools/hardware/test-audio-dma-evidence.py
python3 -O -I -B tools/hardware/test-audio-dma-evidence.py
PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-audio-dma-evidence.py
```

Regressions cover same-window pairing, invalid/missing time evidence,
cross-trial refusal, adjacent sampled movement versus no movement, aggregate
marker attribution, filtered-current-policy handling, and output exclusion of
raw payloads. These are host parser tests only, not kernel-C execution or
device functionality evidence.
