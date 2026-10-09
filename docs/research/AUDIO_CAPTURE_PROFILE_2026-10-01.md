# S22 ABOX capture profile (2026-10-01)

## Finding

Added `tools/hardware/audio-log-capture-profile.py`, a host-only profile for a
sanitized capture summary and the pinned kernel source at
`3fca50941422439b2019db2e4a3dc1016b2138a1`. It does not access the phone,
open trace or Memlogger payload readers, change trace/log settings, build a
kernel, or start a stream.

The muted trial summary has 19 `RUNNING` samples. Every paired sample reports
`hw_ptr=0` and RDMA enable `false`. The request queue result, mailbox sender
result, and selected pointer-handler marker are absent from the retained
summary. Coverage is unknown, so those gaps remain unknown observations; they
do not establish that a request or callback did not occur.

The pinned source shows where the missing observations belong. The RDMA trigger
builds `IPC_PCMPLAYBACK`/`PCM_PLTDAI_TRIGGER` for its channel and enters the
asynchronous `abox_request_ipc()` path. In that path the PCM caller receives
the queue insertion result; queued work later calls `abox_ipc_send()` and
`abox_msg_send()`. The sender’s return is not traced at the queued call site.
The IRQ dispatcher selects a registered handler, and
`abox_rdma_ipc_handler()` reads the message channel/type and pointer payload,
but the ABOX source has no static tracepoint call or declaration for these
boundaries. Its debug messages use `abox_dbg()`.

The source declares an ALSA `snd_pcm:hwptr` event and calls it after the driver
pointer callback returns. `pcm_lib.c` compiles the event only when
`CONFIG_SND_PCM_XRUN_DEBUG` is set; otherwise `trace_hwptr()` is a no-op. The
missing `.config` leaves its runtime availability unknown. If compiled and
already captured, the event carries the ALSA card/device/substream and computed
position. It does not report the trigger queue result, mailbox send result, or
the selected ABOX handler’s channel, message type, and pointer payload.

The saved current metadata has `abox-mem` enabled at level 2 and `abox-file`
disabled at level 2. Pinned source sets `MEMLOG_LEVEL_DEBUG` to 5 and rejects
messages above an object’s level. This explains why current debug markers can
be filtered; the metadata is not linked to the trial window, so it does not
prove the historical logger state or coverage. The Memlogger character-device
reader and `to_string` reader advance the same shared cursor; the profile
refuses both.

## Trace reader boundary

Pinned `kernel/trace/trace.c` distinguishes the static `trace` buffer from
`trace_pipe`. The `trace` file iterates a private ring-buffer iterator and does
not consume shared ring records. `trace_pipe` calls the ring-buffer consume
path. Reading `trace` can still pause active tracing when `pause-on-trace` is
set, and opening it with write/truncate can clear the buffer. Current owner,
pause setting, event enablement, and trial-window linkage were not inspected.
The profile therefore refuses trace access and every trace-policy write. A
later owner-reviewed read may use `O_RDONLY trace` only after confirming the
owner, `pause-on-trace` state, event enablement, and capture-window linkage;
never use `trace_pipe` for this diagnostic.

## Preprocessing evidence and limits

The prior O-tree at `/tmp/s22-hci-candidate-build-20260924` is absent after the
host reboot. The pinned source tree has no `.config` or ABOX `.cmd` records, and
no durable command record was found for this exact source commit. The older
saved ABOX command report was produced from source commit
`f52cbbd7e2783d529e1e5742d94e0fd64889bbdf`; it is not build evidence for
`3fca50941422439b2019db2e4a3dc1016b2138a1`. Whole ABOX translation-unit
preprocessing was not performed.

To exercise the real kernel macro selection without implying a reconstructed
build, the tool runs `cc -E` over the verbatim `dev_dbg()` selection block from
the pinned public `include/linux/dev_printk.h`. The fixture sets each macro
case explicitly and supplies only minimal surrounding names. It confirms that
no enabling macros select the compiled-out branch, `DEBUG` selects the printk
branch, `CONFIG_DYNAMIC_DEBUG` selects dynamic debug, `CONFIG_DYNAMIC_DEBUG_CORE`
alone does not select it, and core plus `DYNAMIC_DEBUG_MODULE` does. This is
executed preprocessing of that source fragment, not of `abox_rdma.c` or the
actual module command. The target build’s effective macro state remains
unknown.

The executed fragment SHA-256 was
`38e4e969c57638b53d80e50ba7f370683951204963b101d5076fba408b12247c` using
`/usr/bin/cc` (Ubuntu 13.3.0-6ubuntu2~24.04.1). The observed branches were:
no defines → compiled out; `DEBUG` → printk; `CONFIG_DYNAMIC_DEBUG` → dynamic
debug; `CONFIG_DYNAMIC_DEBUG_CORE` alone → compiled out; core plus
`DYNAMIC_DEBUG_MODULE` → dynamic debug.

The profile reports the trace snapshot as not ready because the runtime
configuration and ownership are unknown. It performs no device or tracefs
operation, and its source audit does not establish runtime event availability,
firmware receipt, or physical DMA behavior.

## Input and run

The CLI accepts only a small sanitized aggregate. It rejects raw trace/log or
payload keys and requires explicit confirmation that no Memlogger payload
reader was opened. Example summary shape:

```json
{
  "schema": "s22-audio-capture-summary/v1",
  "trial_id": "muted-trial19",
  "sample_count": 19,
  "status_counts": {"RUNNING": 19},
  "running_hw_ptr_counts": {"0": 19},
  "running_rdma_enable_counts": {"false": 19},
  "retained_boundary_marker_counts": {
    "request_queue_result": 0,
    "mailbox_sender": 0,
    "selected_pointer_handler": 0
  },
  "capture_coverage": "unknown",
  "log_policy": {
    "scope": "current",
    "linked_to_trial": false,
    "debug_level": 5,
    "objects": {
      "abox-mem": {"enabled": true, "level": 2},
      "abox-file": {"enabled": false, "level": 2}
    }
  },
  "memlog_payload_reader_opened": false
}
```

Run the tests with the normal interpreter, optimization, and the
`PYTHONOPTIMIZE` environment setting:

```sh
python3 -I -B tools/hardware/test-audio-log-capture-profile.py
python3 -O -I -B tools/hardware/test-audio-log-capture-profile.py
PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-audio-log-capture-profile.py
```

Run the profile by passing that JSON on stdin with summary path `-` and the
read-only pinned source tree as `--source-tree`. Output contains source
contracts and summary counts only; no trace payload is emitted.
