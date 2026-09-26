# S22 hardware continuation — 2026-09-27 (Europe/Athens)

Starting integration/public branch: `s22/luna-driver-completion-20260923`, commit `a94f118c448097e637111e4e196ef3fa587be18d`. Fetch found no newer remote commits. The original checkout remains on local master `fb60a2c1fd69544f525f48de19e1af266880b2ba` with its five tracked modifications preserved. No reset, stash, overwrite, or worktree move was performed.

## Authorization and current baseline

The owner now approves proceeding with Bluetooth, cameras and the remaining hardware work. This covers the next reviewed controller-registration/automatic-initialization trial and bounded muted audio diagnostic; the earlier raw-HCI socket authorization stays consumed. Hardware acceptance is not inferred from approval. Independent hardware rescue remains unproven. No arbitrary pairing, protected EFS/NV writes, NPU BOOTUP, or unreviewed kernel deployment is inferred from this broad continuation.

Coordinator-only read-only checks at 2026-09-26 22:35 UTC identified the same r0s candidate GNU build ID `b2dda820b18d410d9bf12f1bd2584567d545991d` and full RECOVERY SHA `42da267f3dd9f94f30f62a95fb2ac13f91d4cf98f1a2307f7cc14e45d9c49be5`. Uptime was 198742.1 seconds, with 325 modules, native guardian, desktop Pi, model health/idleness, browser service and network readiness passing. Dedicated Pi tmux remains absent on demand. Battery Full/100%, 27.3 C; maximum thermal reading 41 C. The available 255803-byte kernel ring had no fatal or hung-task indicators; full-boot coverage and TrustZone progress remain unproven.

The exact host rollback rehashed to `758fc9d30491e17b7c829a89d338ba69476efa15a1280deb8a1b9b8009687f4b`; the preserved private AArch64 Bluetooth probe rehashed to `c28307985bdad6404f0fecc82860eaa92a2c150a5d870d9297ce0301f44bac0a`. Neither artifact is rebuilt or published.

## Actual Luna assignments

These are resumed workers previously explicitly selected through the supported interface as `gpt-6-luna` with `max` reasoning. Selection evidence is explicit configuration with no known fallback, not independent backend attestation. Work is in new isolated worktrees based on the current published head; all older worktrees/commits remain intact.

| Worker | Worktree | Narrow ownership and deliverable | State |
| --- | --- | --- | --- |
| `/root/bt_next_20260926` | `/home/corpunum/s22-workers/bt-trial-20260927` | Minimal shared trial lock/pending journal, BT adapter and safe persistent staging/trace reservation, hardware-free regressions | Implementing; no phone access |
| `/root/audio_next_20260926` | `/home/corpunum/s22-workers/audio-trial-20260927` | Integrate shared guard, exclusive audio trace reservation, verified cleanup/unknown outcome tests | Implementing; no phone access |
| `/root/driver_review_20260926` | `/home/corpunum/s22-workers/camera-20260927` | Camera-only read-only readiness inventory/tests and exact source-backed next operation; independent review of BT/audio afterward | Implementing; no phone access |

Only the coordinator may access or mutate the phone. Live experiments and heavy builds remain serialized. New tools must preserve old markers and use `/srv/s22` for phone-side staging; significant receipts stay private on the rig. A timeout or dropped connection is UNKNOWN, never permission to retry. Each implementation receives independent review before a live trial; the camera author's own changes require review by another worker.

## Planned bounded sequence

1. Finish/test/review shared trial serialization and persistent unknown-state handling; protect both BT and audio against stale remote traces and insufficient destination space.
2. Revalidate live identity, health, power, destination filesystem and absence of existing hardware clients. Run one reviewed controller-registration attempt, preserving WLAN's power vote; no explicit scan/pairing and no repeated raw-HCI test. Kernel automatic initialization is expected and teardown is not guaranteed bounded by the userspace interval.
3. Only after Bluetooth outcome and cleanup are reconciled, run one reviewed muted digital-zero audio capture with exact selector restoration. Use the synchronized source-path diagnostics to select the next fix, not infer sound from idle metadata.
4. Collect the reviewed camera inventory and advance to the smallest source-safe query/capture step if its prerequisites hold. Keep camera content/private traces off GitHub.
5. Continue independent NPU/cellular/GPU/input source work when device gates block; do not weaken unresolved ownership, protected-NV, or physical-acceptance constraints.

Source checks, executable host tests, builds, live operations, physical acceptance and sustained reliability are separate evidence levels. Current new hardware trial count: zero.
