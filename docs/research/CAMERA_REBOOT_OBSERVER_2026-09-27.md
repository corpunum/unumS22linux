# Camera RECOVERY reboot observer — 2026-09-27

## Scope and current authorization

This host-only adapter consumes the camera deployer's boot-bound flash
receipts. It does not stage or flash an image, load a module, open a camera,
run inference, change gains, or access a phone during development/tests. The
default command is a host-only plan. A request requires both a successful,
exact terminal flash guard receipt and a direction-specific explicit operator
acknowledgement; that acknowledgement is an assertion, not independent
authorization or security evidence.

No reboot or observer execution is recorded by this change. The independent
rescue path and unattended camera acceptance remain unproven. Only USB is
used (`transport_scope: usb_only`); Wi-Fi was not tested/exhausted, and this
observer does not establish hardware rescue. Root/coordinator review owns any
future device request.

## What the request path proves before one attempt

Before remote invocation it revalidates the selected direction's candidate
and rollback artifacts with `deploy-camera-recovery.py`, checks the private
pre-write/raw-readback/final flash receipt chain, and requires the matching
global flash marker to be `complete/success` with the exact final-receipt path
and SHA-256. The current device identity must still match the bound pre-write
boot ID, pinned kernel, full RECOVERY image hash, exact unmounted target, and
power/native-shell gates. Forward additionally requires the existing full
readiness/model-idle check and resolved baseline liveness. The installed
`s22-reboot` and `s22-restart2` files must match their pinned SHA-256 values,
root ownership, regular-file type, and `0755` mode; reads are no-follow and
bounded to 2 MiB.

The single remote request runs a short `python3 -I -B` prelude. Immediately
before `execve`, it rechecks the captured boot ID, native PID 1, both helper
hashes and permissions, and then execs exactly
`/usr/local/sbin/s22-reboot recovery`. A boot-ID mismatch exits before opening
either helper. The helper then retains its own native-session check and
executes the existing restart2 implementation. A durable local attempt marker
is written before this one invocation. Disconnect, timeout, nonzero return,
or an acknowledged return never causes a retry. An ACK is not reboot proof;
the host-global reboot marker remains unresolved until the coordinator
explicitly reviews and reconciles evidence.

The deployer's one-use stage and flash IDs remain distinct as documented in
[the camera deployment note](CAMERA_DEPLOYMENT_2026-09-27.md). This observer
consumes the matching terminal flash ID only; it does not stage or flash.

`--request-recovery` proceeds directly into bounded observation in the same
process even when the one request returns `UNKNOWN`. `--observe-only` resumes
observation without any reboot/request path, but only while no complete
observation receipt exists for that fixed one-shot reboot ID. A complete
`not_accepted` result is final for that ID; a later fresh ring-buffer window
cannot replace it with a greener result. An interrupted run with no final
receipt remains resumable. A malformed, incomplete, unexpected, or unreadable
prior receipt blocks a new window. The observer never resets or terminalizes
the global marker; a private observation receipt is evidence for explicit
coordinator review, not an automatic reconciliation.

## Observation and acceptance fields

Observation has a hard 600-second ceiling, timed USB reads, an end-of-window
identity re-read and a fresh final snapshot. It requires a changed actual boot
ID, a *final* new BORE `RECOVERY` record, the pinned running GNU kernel ID,
the exact complete RECOVERY partition hash, matching no-follow helper
identities, and at least 180 seconds of contiguous qualifying samples.
Transport gaps reset continuity and are counted separately; they are not
misreported as a kernel fault. A reported serious fault, missing/unknown
diagnostic status, or unresolved liveness is sticky and cannot be erased by a
later green sample. A timeout or scheduling overrun is retained in the private
receipt, forces acceptance false, and causes no further query after the bound.

Forward stability does not start until a post-boot identity read proves that
`fimc_is` is loaded with candidate GNU build ID
`59e54c032c545fff3ba52156f226fb6d69aadf64`. An absent module stays read-only
pending and may be rechecked; a loaded wrong ID is sticky failure. The tool
never runs `modprobe` or opens a camera node. Reverse restoration is reported
separately from full desktop/model health: exact baseline image/native
restoration may be proven while full health remains unaccepted. Expected and
observed module/helper identities are distinct fields.

Raw boot IDs are retained in the private deploy and observer receipts; the
baseline BORE record is also persisted in the private request-started receipt.
The complete diagnostic snapshot is not copied into the result. Observer
receipts live under `~/.local/state/s22-camera-trial-20260927/observer/`, are
owner-private mode `0600`, and must not be published. Standard output uses an
explicit summary allowlist and omits raw IDs, records, and receipt paths. An
inconclusive/failed observer exits nonzero; a zero exit from an accepted
observer is still not independent rescue or authorization evidence.

## Host-only operator outline (not executed here)

Inspect a plan without phone access:

```sh
python3 -I -B tools/hardware/camera-recovery-reboot-once.py --profile camera-forward
```

Only after separate owner/coordinator authorization and rescue review, the
direction-specific one-shot acknowledgement can be supplied to
`--request-recovery`; that invocation includes the observer and never retries.
If the host process is interrupted after the durable attempt marker, use
`--observe-only` to resume; do not issue another reboot. Keep any user service
and inhibitor arrangement under coordinator control. For an unattended run,
the coordinator may use a user transient unit with `Restart=no` and no runtime
limit that could kill an active operation. Place
`/usr/bin/systemd-inhibit --what=sleep:idle --mode=block ... <observer-command>`
inside that unit; the tested host-only launch used this shape with
`/usr/bin/true`, not the observer. Do not wrap the outer `systemd-run --wait`
process with an inhibitor from an interactive terminal, since that inhibitor
may end with the terminal. No service or real observer was started by this
work, and this outline is not authorization to request a reboot.

## Regressions

The fake-transport suite exercises private receipt and terminal-marker links,
authorization/artifact/boot/image refusal, exactly-one request and unknown
outcome handling, immediate boot-guard refusal before helper execution,
transport gaps, delayed/wrong module IDs, sticky fault/liveness handling,
final BORE freshness, final-snapshot failure, forward/reverse stability,
reverse image-versus-health distinction, filtered stdout, and same-process
request-then-observe order. These are host contracts, not kernel-C execution
or phone functionality.

```sh
python3 -I -B tools/hardware/test-camera-recovery-reboot.py
python3 -O -I -B tools/hardware/test-camera-recovery-reboot.py
PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-camera-recovery-reboot.py
```
