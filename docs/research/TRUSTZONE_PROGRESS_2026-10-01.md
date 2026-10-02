# TrustZone scheduler progress collector — 2026-10-01

## Scope and evidence boundary

`tools/hardware/tz-progress-evidence.py` is a bounded, read-only procfs
collector for the source-identified `tz_worker_thread`, `tz_iwlog_thread`, and
`chub_log_kthread` tasks. It records only scheduler and context-switch
counters, task state, a small wait-channel class, and boolean matches for the
two TrustZone source wait paths. It does not read TrustZone payloads or kernel
logs, change warning settings, set `TASK_IDLE`, invoke an SMC, or perform a
hardware request.

The camera observer's historical `not_accepted` result remains in force. This
tool cannot revise that result or establish camera acceptance. Scheduling or
context-switch activity is not evidence that a secure request completed.
`tee_request_completion` and liveness remain `unknown`; a matched wait stack
does not show that a secure request is absent or complete.

## Source basis

The TrustZone wait-path source is pinned to base commit
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`. The exact camera-derived kernel
tree used for this review is `3fca50941422439b2019db2e4a3dc1016b2138a1`.
Read-only comparison of `drivers/misc/tzdev/core/kthread_pool.c`,
`drivers/misc/tzdev/core/iwlog.c`, and
`drivers/staging/nanohub/chub_log.c` found no changes between those commits in
the TrustZone source paths.

The worker source names `tz_worker_thread/%u`; Linux's 15-byte task-comm limit
means procfs can show `tz_worker_threa`. The matching iwlog comm is
`tz_iwlog_thread`; `chub_log_kthread` can appear as `chub_log_kthrea`. Matching
uses those truncated procfs names. The worker's expected wait stack includes
`tz_worker_handler`; the iwlog wait stack includes
`tz_iwlog_kthread_handler`. The CHUB logger waits for CHUB log-buffer state and
is included only for scheduler context, not as a TrustZone secure-request
wait path.

## Collection and identity rules

The remote program samples twice with a fixed two-second sleep. It reads
`/proc/<pid>/task/<tid>` records and six named kernel sysctls. Boot UUIDs are
compared inside the remote process, and only a `true`, `false`, or `null`
consistency value is returned. Kernel release is checked across the same
window. Per-task `/proc/.../stat` start ticks are retained only in the
transient capture so the host can pair slots safely; task IDs, PIDs, boot
UUIDs, raw stacks, and network endpoints are not included in the aggregate
report. A task slot is comparable only when its start ticks match across both
samples. Missing tasks, unreadable records, identity changes, counter
regressions, changed boot identity, or an invalid sample window prevent a
no-change conclusion.

The collector stops after 4096 task records scanned or 16 matching targets
per sample. Reads are capped at 128 bytes for sysctl, uptime, and boot-ID
records; 256 bytes for the kernel release and schedstat; 512 bytes for wchan;
4096 bytes for stat; 65536 bytes for status; and 32768 bytes for stack.
Oversized or unavailable records become unknown, or cause the host classifier
to reject malformed/overflowed numeric values. Capture input and remote
output are capped at 2 MiB. The USB SSH operation has a 15-second deadline;
an error or timeout is not retried and yields no progress conclusion.

The shell's pathname expansion materializes proc task paths before the
iteration limit is checked. `MAX_SCAN` bounds task records visited and read,
but does not cap that temporary pathname list independently of the live
kernel's procfs task count.

The top-level report says `observed` or `not_observed` only when the two-sample
boot and task identity checks pass, enumeration is complete, all three task
roles have paired scheduler and context-switch counters, and every counter
remains monotonic. Missing counter coverage stays `unknown`. A per-role row
can record an increase for a valid same-task pair, but an incomplete overall
capture keeps the top-level assessment `unknown`. No result measures TEE
request completion.

## Transport and verification

`--capture` uses the existing `audio-recovery-reboot-once.py` read-only USB
route (`_default_remote`), which uses the sealed `tools/s22-ssh` wrapper and
trusted SSH executable path. The collector runs as native POSIX `sh`; it has
no ADB or Android `/system/bin/sh` path. `--input` only classifies local JSON.
For this implementation, the remote path was not invoked and the phone,
SSH/ADB services, deploy/reboot paths, and hardware were not accessed.

The fake-procfs tests execute the rendered collector with synthetic procfs
files. They cover clipped comm names, positive counter deltas, stack
redaction, boot-ID and task-starttime changes, missing boot IDs and counters,
the target cap, input-size bounds, and the sealed USB helper handoff. These
tests establish host behavior only; they do not establish device runtime
behavior or TrustZone liveness.

```sh
python3 tools/hardware/test-tz-progress-evidence.py
python3 -O tools/hardware/test-tz-progress-evidence.py
PYTHONOPTIMIZE=1 python3 tools/hardware/test-tz-progress-evidence.py
python3 tools/hardware/tz-progress-evidence.py --render-remote | /bin/sh -n
```
