# Independent camera deployment adapter review — 2026-09-27

Reviewed `3cabe1546e5a920ddd9bad51bc190ea9af495d25` and documentation-only
follow-up `c73bf1b4b4bd3e3d350eecae9bc207f69ed7eb0a` in the camera-deploy
worktree. The adapter is approved for host integration: forward pins HCI image
`42da267f…c49be5` → camera image `b1041271…d8d2f`; reverse pins the exact
opposite pair. Both use the shared RECOVERY renderer, unique direction/mode
identities, the global durable trial guard, and durable local receipts. Failed
or uncertain transport remains unresolved and is not retried. The adapter does
not reboot or implement observation.

Validation: 16 focused tests passed normally, with `-O`, and with a genuine
`PYTHONOPTIMIZE=1` environment run. Both actual artifact plans exited 0 in
host-only mode with rescue, candidate acceptance, and reboot explicitly false.
Root's six mirrored host-runner allowlist additions are consistent. The
candidate CPIO's `lib/modules/modules.load` includes `fimc-is`; a future
candidate observer must verify the loaded module's GNU build ID
`59e54c032c545fff3ba52156f226fb6d69aadf64`, without forcing a load or opening
a camera node.

This is source/host evidence only for the independent review. I did not
personally access the phone, invoke SSH/ADB, or stage, flash, reboot, or package
anything. Independent rescue and candidate-specific unattended acceptance
remain unproven; this review does not authorize a live trial. The separate
reboot/observation adapter remains outside this approval.

## Boot-bound amendment

The later exact freeze `b45a2dc96aeacd994f7e869f2de6f212a62900fe` is also
approved for host integration. It adds a read-only pre/post identity capture,
durable pre-write and raw write/readback receipts, boot-ID equality before the
shared flash body, and same-boot/new-image validation before a bound success
receipt can complete the global marker. Unreadable, oversized, incomplete, or
malformed mount/module procfs evidence fails closed. A failed post-query or
changed boot ID preserves raw readback evidence and leaves the marker
unresolved; the reverse profile does not require camera-module health.

Validation: the 22 focused tests passed in normal, optimized, and
`PYTHONOPTIMIZE=1` modes; syntax and diff checks passed. This includes executing
the boot prelude with matching and changed IDs, fail-closed procfs parser
fixtures, and a changed post-write boot ID case. Source review confirms the
exported identity helper only opens RECOVERY read-only, checks capacity, hashes
with `pread`, and reads procfs/sysfs/module notes; it has no partition write,
mount, module-control, camera-node, or reboot path. The coordinator separately
reported executing that exported helper through its trusted read-only path;
its sanitized result matched the private current-baseline identity and
read-only partition/kernel/module checks. I did not invoke it or perform any
device operation.

## Related audio logging review

The audio log-coverage freeze `1348f05ad411e125e900aee4e45aeb1a1af0b31f`
(following `f8b756f364ccca4b1b75dc150f09073cd7bb1692` and
`d3145406b7a3e978ef9220815fe45ab521716b8f`) is approved for host integration.
The direct macro-flag parser handles attached/split ordered `-D`/`-U` and
malformed input conservatively; its `direct_macro_flags_parseable` label does
not assert effective preprocessor state or that `dev_dbg` was compiled out.
The report keeps current metadata/policy separate from historical marker
capture (unknown) and refuses consuming payload readers. All 12 tests passed in
normal, `-O`, and `PYTHONOPTIMIZE=1` runs with the pinned source/O-tree set.
This is source/metadata analysis only, not runtime capture evidence; no payload
was read.
