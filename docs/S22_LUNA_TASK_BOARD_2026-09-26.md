# S22 remaining-driver implementation — 2026-09-26

Continuation of `s22/luna-driver-completion-20260923` at `2a4f3d171627d41cb703af880a33521c4c964245`. Fetch found no newer remote commits. The original dirty checkout and all historical worktrees are preserved; master is not the integration target.

## Live starting point

Coordinator read-only USB SSH inspection identifies the r0s HCI candidate by GNU build ID `b2dda820b18d410d9bf12f1bd2584567d545991d` and newest RECOVERY boot record. Uptime was 183030 seconds. Native guardian, desktop, desktop Pi, model health/idleness, browser terminal, networking, and 325 loaded modules were healthy. Dedicated Pi tmux is absent in the documented on-demand state. Battery was Full/100%, 27.1 C, maximum thermal reading 42 C. The complete available dmesg ring contained zero fatal indicators and zero hung-task warnings; full-boot log coverage and TrustZone progress remain unproven.

The completed second trial journal records one candidate RECOVERY write, one recovery reboot request, one successful raw-HCI create/close, and no rollback. Its operation lock was released. Those operations are consumed and are not reissued in this wave. Independent hardware rescue remains unproven. No new phone mutation has occurred in this wave.

A further read-only inventory found no registered `hci*` controller, modem state `INIT`, and PCM2 playback `closed` with ABOX runtime `suspended`. Camera pipeline/codec nodes are enumerated but no camera was opened or frame captured. The root overlay has 34,844,672 free bytes and 29,780 free inodes. `/srv/s22` and the Arch mount share the persistent filesystem, with 101,778,141,184 free bytes and 1,652,499 free inodes. Root-overlay scarcity does not prevent appropriately scoped staging on that persistent filesystem; no cleanup or package installation was performed.

## Actual workers

The supported collaboration interface accepted explicit `model=gpt-6-luna`, `reasoning_effort=max` selections. Evidence level is **explicitly configured**; the spawn response provides task handles, not independent backend model attestation. There is no known override/fallback. Three workers plus the coordinator fill the four available concurrent slots. Only the coordinator accesses the phone, integrates, and publishes.

| Actual task handle | Worktree / branch suffix | Scope and deliverable | State / tests / commit |
| --- | --- | --- | --- |
| `/root/bt_next_20260926` | `/home/corpunum/s22-workers/bt-next-20260926`, `codex/s22-bt-next-20260926` | Bluetooth bridge/runner lifecycle correctness and executable host regressions; next controller-registration prerequisites | Running |
| `/root/audio_next_20260926` | `/home/corpunum/s22-workers/audio-next-20260926`, `codex/s22-audio-next-20260926` | Source-backed synchronized audio diagnostic improvement to locate stalled DMA/backend activation; regression tests | Running |
| `/root/npu_next_20260926` | `/home/corpunum/s22-workers/npu-next-20260926`, `codex/s22-npu-next-20260926` | NPU publication/callback ownership and actual-C regression coverage, preserving BOOTUP refusal | Running |

Workers own disjoint driver files and one research note each. They may edit and test host source, but may not access the phone, publish, change shared deployment/observer/CI files, or start heavy kernel builds. Independent review and CI integration use the next available worker slot. Subsequent work will address cellular/camera, GPU/presentation, input/power, and other hardware using their existing source and evidence.

Host tests, source/build evidence, live driver operation, and physical acceptance are reported separately. Firmware, images, weights, credentials, raw device traces, and network identifiers stay private.
