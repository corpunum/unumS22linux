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

This is source/host evidence only. No phone, SSH, ADB, stage, flash, reboot, or
package operation was performed. Independent rescue and candidate-specific
unattended acceptance remain unproven; this review does not authorize a live
trial. The separate reboot/observation adapter remains outside this approval.
