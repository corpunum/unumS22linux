# S22 capacity and rescue audit — 2026-09-24

This is a read-only host audit. The accompanying tool measures this host's
shell/PID 1 roots and only the destinations explicitly listed in a local JSON
plan. It does not contact the phone, create files, reclaim space, or perform
deployment actions. Current phone figures below were captured by the
coordinator and relayed to this isolated host worker; they were not measured
by this tool.

## Capacity findings

The current host shell root is on ext4, with about 207 GB available and 101.6
million free inodes. This is host capacity, not phone capacity. In this worker
environment `/proc/1/root` could not be measured and the mount-namespace
identity was unavailable, so no host PID 1 equivalence is claimed. The tool
reports those unknowns explicitly.

The coordinator's 2026-09-24 phone capture reports:

| Phone destination/view | Free bytes | Free inodes | Interpretation |
| --- | ---: | ---: | --- |
| Native `/` overlay | 34,028 KiB (596,544 total; 550,228 used) | 29,780 | CACHE-backed rescue overlay; low headroom |
| `/run` rootfs tmpfs | 2,303,660 KiB available (2,433,448 total) | 607,115 | transient native rootfs; `df` could not resolve this bind-mounted path, so values come from `stat -f` and mountinfo |
| Persistent `/srv/s22` | 99,982,692 KiB | 1,652,508 | Persistent userdata filesystem |
| `/tmp` | 256 MiB | 830,421 | separate tmpfs; not used by the current deployment script |

The same capture reports that the SSH root cannot see `/cache`, while PID 1's
mount information names the CACHE-backed overlay and
`/proc/1/root/cache/s22-linux/upper` is readable. This reconciles the small
merged-root view with the backing upper layer: the SSH root's visible `/`
does not expose the upperdir path directly. Earlier coordinator evidence
reported 521,476 KiB in the upperdir, including a large upper `/usr`, without
establishing which entries were safe to remove. No cleanup or deletion is
authorized by these measurements. These namespaces and capacities are
separate from the host shell measured above.

Before any proposed build, download, package action, or image staging, use a
plan that lists every actual output and incidental destination: for example
the primary artifact directory, temporary spool, cache, sidecar/receipt
directory, and any copy destination. Each target has its own byte and inode
budget, and destinations on the same filesystem are conservatively summed
within an operation. Existing file size is context only; it is never treated
as reusable free space. For phone paths, this host-only tool is not a live
measurement source: the coordinator must first collect bounded read-only
capacity and mount evidence in the actual phone namespace for the exact
destination.

For the current recovery deployment script specifically, the host reads the
candidate image and records its receipt under the project rootfs tree; the
phone receives the candidate through SSH stdin and creates the rollback image
and staged candidate under `/srv/s22` (100,663,296 bytes each, plus directory
and filesystem overhead; two files plus one directory inode). Its serialization
lock is a small file under `/run`. No package install or phone `/tmp` output is
part of that script. The measured `/srv/s22` headroom is about 102.4 GB and
1,652,508 inodes; `/run` has about 2.36 GB and 607,115 inodes available. These
measurements exceed the known destinations' needs and do not imply the nearly
full overlay is safe to clean or suitable for arbitrary writes. Host tests use
temporary directories on the host's ext4 filesystem, not phone storage.

Example plan shape (replace the illustrative path with the exact local
destination before auditing):

```json
{
  "schema": 1,
  "operations": [{
    "id": "candidate-build",
    "targets": [
      {
        "id": "artifact-output",
        "path": "/repo/build-output",
        "path_type": "directory",
        "required_bytes": 1000000,
        "required_inodes": 3,
        "reserve_bytes": 0,
        "reserve_inodes": 0
      },
      {
        "id": "temporary-spool",
        "path": "/tmp/build-spool",
        "path_type": "directory",
        "required_bytes": 1000000,
        "required_inodes": 2
      }
    ]
  }]
}
```

Run `python3 tools/hardware/s22-capacity-rescue-audit.py --plan PLAN.json`.
The JSON output hides target paths and mount sources. A result of
`review_required` includes insufficient or unmeasurable targets; it is not a
cleanup recommendation or permission to proceed. This is a point-in-time
snapshot: rerun against the same declared destinations immediately before a
separately authorized operation.

## Rescue evidence and current gate

Strict-host-key USB SSH is a useful in-session route, but it runs through the
kernel under test. A Tailscale peer response routed over that same USB link
does not make recovery independent of the kernel. The latest coordinator
capture says the phone is currently exposing native USB SSH, so local
`samloader detect` does not detect a Download Mode device. The current
Download Mode path is therefore not presently confirmed by that check.

There is historical, not current, independent-route evidence. On 2026-09-20
the host detected Samsung Download Mode, used `samloader` for a BOOT rollback,
and the owner then physically selected RECOVERY; the subsequent boot record
confirmed RECOVERY and USB SSH returned. This demonstrates a past physical
bootloader/USB route, but it is not a fresh qualification of today's
connection or of a future candidate-specific rollback. Historical evidence
is summarized in [EXPERIMENTS.md](../EXPERIMENTS.md),
[PERSISTENCE.md](PERSISTENCE.md), and
[NEXT_STEPS_2026-09-19.md](NEXT_STEPS_2026-09-19.md).

The local `samloader --help` output exposes `detect`, `flash`, and
`reboot-download`. Its `flash --help` supports an explicit `-p PARTITION FILE`
target and `--no-reboot`; it also exposes `--repartition`. The
`reboot-download --help` command requests Download Mode. There is no
recovery-target reboot command. The separate `s22-reboot recovery` helper is
issued by the running native Linux system and remains dependent on that
kernel. A recovery artifact stored on the phone also cannot restore access if
the kernel fails before remote access. Only local help was inspected here; no
device-facing samloader subcommand was run by this worker.

The exact physical qualification still needed is an owner-assisted session
with the phone placed in Download Mode while attached to the actual recovery
host, followed by host-side USB enumeration and a successful read-only
`samloader detect`. No flash is part of that check. If the recovery-entry
transition is also being confirmed, the documented sequence is Volume Down +
Power, then immediately Volume Up + Power as the screen goes black; verify
the resulting mode from `/proc/boot_reset`/BORE rather than the display alone.
The host must also have the exact known-good rollback artifact and its
candidate-specific write/readback procedure ready before any separately
authorized experiment. No Download Mode physical check, flash, reboot, or
phone access occurred in this worker task.
