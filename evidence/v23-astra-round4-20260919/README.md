# V23 / cinit12.c — Astra-only round 4 review

Result: NOT converged. Two incompletely closed races and three additional diagnostic issues remain. No production edits, flashing, reboot, live watchdog operation, or actual module loading.

Reviewed all 1056 lines of cinit12.c and the V22 diff. Snapshot SHA256:
44175ad33711c90750ae206e6e012058547eab706ca4d282baab3d895dc4d3e0
The workspace source still matched this hash after testing.

Device: <redacted>, root ADB in recovery, aarch64 Linux 5.10.260-g4e5c5ad7d950.

## Ranked findings

1. P2, residual: modprobe child can still fabricate PID1 death before completing resets.
   cinit12.c:357, 366-387; handler attribution at 516.
   more.c mode 20 faults with a real null-pointer store at the execl boundary, AFTER all
   resets: exit=-1, no fatal record (fixed). Mode 21 faults with a real null-pointer store
   immediately after fork returns in the actual child, BEFORE resets: fatal sig=11,
   exit=139, PARENT STILL ALIVE. Mode 22 raises real SIGABRT after the first reset:
   fatal sig=6, exit=134, PARENT STILL ALIVE. No timing probability is claimed.
   Fix direction: make handler attribution depend on actual process identity. Resetting
   sequential dispositions alone cannot cover fork return or the reset sequence itself.

2. P2, new failure-path finding: options write failure does not establish default policy.
   cinit12.c:681-690, 693; BusyBox modprobe-small.c:723-728, 830.
   Actual RLIMIT_FSIZE=11, SIGXFSZ ignored in disposable child, causes a short write then
   EFBIG. Actual source leaves 11 bytes, tmr_atboot=, while logging that watchdog WILL
   probe with compiled default tmr_atboot=0. Packaged nativev23/busybox forwards precisely
   that malformed option to finit_module and init_module (trace-output.txt).
   Both module syscalls were denied by an inherited seccomp filter independently of
   ptrace: no module loaded. Trace fixture replayed the exact malformed bytes produced
   by the options harness. The integer parameter and module parse-error abort path are
   also present in the previously downloaded device-kernel source at
   /tmp/s22-round5-source/drivers_watchdog_s3c2410_wdt.c:46 and kernel_module.c:4133.
   Rejection before probe is a source-based conclusion, NOT an on-device real driver
   insertion result. Missing dependencies in the isolated BusyBox fixture are expected.
   Fix direction: remove/replace incomplete options, check cleanup, report policy unknown
   until established; do not promise compiled-default probe on arbitrary write failure.

3. P2, residual: durable-banner-to-cache-enable gap still loses fatal evidence.
   cinit12.c:862-880, fatal gate 527.
   Real SIGABRT after the actual banner close returns yields a 48-byte banner-only cache
   log; the fatal line exists only in the kmsg surrogate. Same boundary used in round 3
   still reproduces. A signal on opening the subsequent early-log flush DOES now persist
   its fatal line: the long flush window is fixed, but close still precedes flag enable.
   Fix direction: enable inside successful write/fsync branch before close. If an atomic
   transition against asynchronous signals is required, protect the transition explicitly.

4. P2, new: cmdline read errors masquerade as normal end-of-input.
   cinit12.c:641-651.
   Wrapped read returns 12 actual fixture bytes then EIO: logs only
   cmdline[0]: console=none, with no failure/truncation marker. Initial EIO emits nothing.
   The source handles open failure and buffer exhaustion, but neither read error case.
   This can hide reset_reason/watchdog fields without recording that evidence is incomplete.
   These are injected syscall errors; no spontaneous procfs EIO was observed or claimed.
   Fix direction: preserve read errno and distinguish EOF/error/exhaustion explicitly.

5. P3, new: every fatal line emitted by V23 says v22.
   cinit12.c:508. All five tested signals persisted [v22 ...] into v23_log.txt.
   Fix direction: correct the hand-built version tag, preferably sharing its definition.

## Six incoming fixes

- Counter EOF validation: correct. Actual two-byte read then injected EIO preserves
  123456 and reports boot=-1; EINTR plus real one-byte reads through EOF produces 123457.
- Increment ceiling: correct. 9999998 -> 9999999; 9999999 stays unchanged, boot=-1;
  10000000 and INT_MAX rejected. Saturation is deliberately unavailable, not rollover.
- Cache enable before early flush: correct for flush; residual close boundary above.
- Split diagnostics: correct. Worst tested 3-digit errnos produce 106/131-byte complete
  summary records at current five-digit uptime; both watchdog notes preserve newlines.
- Options complete-write gate: correct for success claims. Actual char 1:7 ENOSPC reports
  failure; RLIMIT_FSIZE 10/11 reports failure and 12 reports success. Failure-policy claim
  remains wrong as above. The 12-byte write length is correct (no extra NUL).
- Child default handlers: correct AFTER resets; residual windows above.

## Other verification

- Counter ENOENT, empty, invalid characters/newlines, integer overflow input, read/open
  errors. Temp write ENOSPC, fsync EIO, rename EIO all preserve original count and return
  unknown. EINTR + capped writes complete correctly.
- Normal append ordering: banner, queued early records, live record.
- Banner open/fsync failure, flush write/fsync failure, both durability failures; observed
  conservative cache flags and warning branches. No claim that injected fsync errors model
  actual ext4 power loss; bytes can remain visible despite unconfirmed durability.
- write_full retries EINTR/short writes in fatal handler. All five installed fatal signals
  exit with 128+signal. Real permanent FSIZE failure demonstrates best-effort limits.
- Real private tmpfs umount EBUSY leaves mounted=1/ok=0; successful unmount sets both zero;
  subsequent fatal signal creates no underlying ramdisk log.
- 100 counter/banner/flush cycles: open fds before=4, after=4 (fds 0..255 checked).
- Live read-only proc/sys checks: dss, wdt, ufs present; all four driver/device pairs bound;
  missing device/driver states 1/0. sda33 present, da33/a33/sda330 rejected.
- Real 3329-byte cmdline reconstructs exactly from 28 chunks. u2a boundary tests through
  ULONG_MAX have zero failures. No live kernel cmdline contents copied to output.
- Source-only aarch64 gcc -O2 -Wall -Wextra -Wformat=2 -fsyntax-only: clean.
  Recorded harness warnings are setup/unused test helpers, not production warnings.
- Static pass covered fd relocation/inheritance, explicit timed-out-child retention,
  module/partition bounds, early queue capacity for this call graph, signal-safe operations,
  watchdog ownership, shutdown ordering and known blocking/time-oracle limitations.

## Reproduction and scope

Final C harnesses here are authoritative; no regeneration required. They include the exact
snapshot with main renamed and NEVER call production main. Embedded fragments are verbatim:
768-899 counter/banner/flush, 994-1030 shutdown, 607-618 summaries, 733-734 watchdog notes,
681-691 options, 636-655 cmdline read. Syscall wrappers supply deterministic faults.

Build each of harness, more, matrix, live, trace:

    aarch64-linux-gnu-gcc -static -O2 -o /tmp/s22-r4-more more.c
    adb push /tmp/s22-r4-more /tmp/s22-r4-more
    adb shell '/tmp/s22-r4-more /tmp/s22-r4-NEW-UNUSED-fixture'

harness/more/matrix take fresh fixture paths; live takes no arguments. Mutation harnesses
unshare the mount namespace, make propagation recursively private, and chroot into isolated
/tmp fixtures. Cache/kmsg/watchdog are surrogates; no real block partition or watchdog is
opened. Core dumps are disabled in more/matrix. Source shutdown fragment calls sync().

trace takes an already prepared isolated chroot, with the packaged static busybox at
/busybox, copied watchdog .ko under /lib/modules/$(uname -r), empty fixture /proc/modules
(or missing), and /etc/modules/s3c2410_wdt containing tmr_atboot=. A two-line modules.dep.bb
fixture was supplied; BusyBox additionally discovered embedded dependencies and reported
those absent. The seccomp filter denies AArch64 init_module=105/finit_module=273 before
exec. Ptrace only observes their entry arguments; nothing reaches a module insertion.

No global PID1 fault, ext4 power-cut durability, real watchdog expiry, hardware restart,
or freshly probed module behavior was exercised. Recovery/ADB remained up at end; see
final-device-state.txt. These limits are not substitutes for the five concrete findings.
V23 is not ready to close the review loop while they remain open. This is no flash advice.
