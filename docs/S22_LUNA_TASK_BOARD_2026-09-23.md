# S22 Luna driver mission task board — 2026-09-23

Coordinator checkout: `/tmp/s22-luna-coordinator-20260923`, branch
`codex/s22-luna-coordinator-20260923`, based on fetched `origin/master`
`790cb0ab4af121e8ed79346723b845124eb3ece4`.

## Execution constraint

The collaboration interface accepted explicit worker requests for
`gpt-6-luna` with `max` reasoning, but it returned only canonical task names;
it does not expose an independently verifiable runtime model ID or opaque
session ID. Spawned workers report that their runtime label is GPT-6 and that
they cannot see an exact Luna ID. The primary coordinator runtime is identified
as Codex/GPT-5, so the required same-family Luna Max coordinator condition is
also unmet. These attempts are recorded honestly and are **not counted as
verified Luna implementation/review workers**. No worker was allowed to edit
or commit after this became clear.

## Wave 1

| Worker ID | Requested model / reasoning | Runtime model verification | Assignment / worktree | Touched files | Tests / result | Commit |
|---|---|---|---|---|---|---|
| `/root/recovery_hardening` | `gpt-6-luna` / `max` | Unverified; worker sees GPT-6 only; no session ID exposed | Recovery deploy/build/AVB hardening; `/tmp/s22-luna-deploy-20260923` | None; read-only inventory | No tests run. Identified `assert` safety gates, string-replacement embedder, and missing explicit AVB `verify_image` path/tests. | None |
| `/root/bluetooth_lifecycle` | `gpt-6-luna` / `max` | Unverified; worker sees GPT-6 only; no session ID exposed | Bluetooth lifecycle/bridge; `/tmp/s22-luna-bt-20260923` | None; read-only inventory | Worker ran 3 PTY tests; coordinator independently reran the bridge suite (3 passed), applied-patch HCI source contract (passed against existing candidate worktree), and `git apply --check` (passed against clean pinned source). No dynamic/device HCI test. | None |
| `/root/audio_dma_diag` | `gpt-6-luna` / `max` | Unverified; exact ID/session not exposed | Audio DMA diagnostics; `/tmp/s22-luna-audio-20260923` | None; read-only inventory | Coordinator added a fail-closed `hw_ptr` progress assessment; route tests 12 and snapshot planner tests 8 passed. No live stream or control write. | None |
| `/root/npu_ownership` | `gpt-6-luna` / `max` | Unverified; worker sees GPT-6 only; no session ID exposed | NPU ownership/unwind; `/tmp/s22-luna-npu-20260923` | None; read-only inventory | No worker tests. Fetched worktree lacks pinned NPU source; preserved local kernel checkout is clean at exact `4e5c5ad7d950e4de0688b5663965f2075654b2ad`. Coordinator verified the reported raw-session callback, POWER exclusion from session linking, unbounded wait, and BOOTUP error path directly. Preflight gate changed; 2 host test scripts plus optimized-mode CLI check passed. | None |

All four worker worktrees are separate branches based on the fetched mission
commit. No phone access, deploy, reboot, flash, or live driver experiment was
performed by any worker.

## Coordinator work

| Task | Worktree | Status | Tests / commit |
|---|---|---|---|
| Reconcile checkout, mission, history, jobs, and live phone state | `/tmp/s22-luna-coordinator-20260923` plus read-only phone SSH from the preserved main checkout | Live baseline reconciled; full matrix/provenance closure pending | Host image hashes and live RECOVERY readback match; no write/device mutation |
| NPU BOOTUP readiness gate | Coordinator worktree | Implemented and committed locally; independent Luna review unavailable; not pushed | `test-npu-boot-preflight.py`, `test-npu-boot-probe.sh`, optimized CLI gate passed. Commit `5928e980ea32355c43ca5240eb41c8aa862533f3`. CLI cannot authorize BOOTUP; lifecycle/rescue/owner gates remain closed. |
| Audio DMA evidence classifier | Coordinator worktree | Implemented and committed locally; no hardware experiment | `test-audio-route-assessment.py` (12), `test-audio-progress-snapshot.py` (8) passed. Commit `5928e980ea32355c43ca5240eb41c8aa862533f3`. Classifies ALSA pointer progress separately from physical playback. |
| Bluetooth patch host validation | Coordinator worktree + existing read-only kernel worktrees | Source/apply host-validated only | H4/IBS bridge suite (3), HCI contract test and `git apply --check` passed; full build is historical evidence, no live HCI claim |
| Recovery candidate AVB structure/hash verification | Coordinator worktree, read-only | Both local recovery candidates pass footer/hash verification; Samsung authentication and bootability not established | `avbtool.py verify_image` passed for the live audio-extra baseline and unflashed HCI candidate. Both report algorithm `NONE`; this does not prove Samsung signature acceptance or boot success. |

The coordinator commit was local only at the time of this historical entry.
The original dirty checkout was not merged, reset, stashed, or edited by the
coordinator changes.

## Live phone reconciliation — read-only, 2026-09-23 18:14 UTC

- Verified USB SSH through the existing strict-host-key helper. Native kernel
  `5.10.260-g4e5c5ad7d950`, PID 1 `native-guardian`; PID 1 and probe mount
  namespaces match. The boot ID matches the private checkpoint; uptime was
  about 18.1 hours (identifier intentionally not copied into this board).
- A bounded 32 KiB `/proc/boot_reset` read contains BORE 767 at
  `2026-09-23 00:09:25`, selected RECOVERY; this reconciles the earlier
  4 KiB probe, which stopped before that record. No reboot was requested.
- Host rollback image exists and hashes to the recorded Lineage recovery
  SHA-256 `b5bf01c4...`; staged HCI candidate hashes to
  `6d7e2a4a...` and remains unflashed. Read-only hash of live `/dev/sda16`
  is `758fc9d3...` at exactly 100,663,296 bytes, matching the audio-extras
  RECOVERY baseline.
- `/proc/modules` currently has 325 entries (sample includes WLAN, ASoC,
  charger, NFC and touchscreen drivers), contradicting the older report of
  zero loaded modules. Kernel config reports `CONFIG_MODULES=y`,
  `CONFIG_MODVERSIONS=y`, and 4 KiB pages. This confirms live loading, not yet
  the complete boot-time module provenance/closure.
- `wlan0` is up and a WLAN-bound HTTPS check returned 200. Hyprland is
  present, the DSI connector is connected, and 11 input event nodes exist;
  physical touch remains unverified. `tailscaled` runs and `tailscale0` exists,
  but its `operstate=unknown`, so this probe does not establish tailnet peer
  reachability. Local model port 8089 returned health HTTP 200; its process
  command line has `-ngl 0`, so the resident 4B model is CPU-only. Bluetooth
  class is empty.
- Battery reports 100%/Full at 27.8 C; sampled thermal zones were 32 C.
  `MemAvailable` was about 2.74 GiB. CACHE-backed `/` has only about 34 MiB
  free (94% used); userdata has about 99.98 GiB free. No writes were made.
- Read-only `sha256sum /dev/block/by-name/RECOVERY` failed because the live
  device exposes the lowercase link `/dev/block/by-name/recovery`; resolving
  that verified link identified `/dev/sda16` for the successful hash. This
  is a path-case correction, not a device change.

## Execution correction follow-up — 2026-09-23

This section supersedes the earlier statements above that no worker had edited
code, that Luna selection was unmet, that Tailscale reachability was unknown,
and that publication must wait for review. The earlier entries remain the
historical 18:14 UTC checkpoint; the execution correction at
`origin/master:docs/S22_LUNA_EXECUTION_CORRECTION_2026-09-23.md` governs the
current procedure. The original two local commits remain intact and reachable
on `s22/luna-driver-completion-20260923`; no reset, stash, discard, or edit to
the dirty original checkout was performed.

### Model and worker receipts

Installed client: `codex-cli 0.156.1`. Effective persistent Codex config selects
`gpt-6-luna`; this client/model catalog exposes `max` reasoning. The primary
session does not expose runtime model/session metadata here, so coordinator
evidence is `explicitly_configured`, not `runtime_reported`. Each worker below
was launched through the native collaboration interface with explicit
`gpt-6-luna` and `max`; the tool returned task handles but no opaque session
IDs or runtime identity attestation. There is no known worker override/fallback.

| Actual task handle | Worktree / branch | Assignment and current result |
|---|---|---|
| `/root/recovery_deploy_impl` | `/tmp/s22-luna-wave1-deploy-20260923`, `codex/s22-wave1-deploy-20260923` | First deployment/build-hardening commit `03eba0700f65e0ef1576a50a1ab43dd2b325d5bd`, integrated as `9dc83b3`; 28 tests pass in normal, `-O`, and `PYTHONOPTIMIZE=1` modes. Independent review found AVB verifier substitution, primary SSH-wrapper symlink, and remote writable-parent race gaps. Luna author follow-up is implementing fixes/tests in the same isolated worktree. No device actions. |
| `/root/bluetooth_impl` | `/tmp/s22-luna-wave1-bt-20260923`, `codex/s22-wave1-bt-20260923` | Completed host-only bridge/test improvements; commit `7bade3f376bb0e810baca799e4dc5ec8f17b2443`. Changed H4 bridge cleanup reporting and expanded bridge/HCI negative tests. H4 suite: 12 passed. Candidate HCI patch applied in a temporary directory to the clean pinned kernel source, lifecycle contract plus three mutation negatives passed. No kernel build/runtime HCI. Independent review is pending next available slot. |
| `/root/audio_diag_impl` | `/tmp/s22-luna-wave1-audio-20260923`, `codex/s22-wave1-audio-20260923` | Completed synchronized snapshot and cleanup classification; worker commit `2d6d9a40c3d97064ef4a5ccc5ceb90071970b715`, integrated as `2b55003`. Coordinator reran snapshot 13, route 18, wrapper cleanup 4, bind-node 4, PCM prepare 5, and sync-nodes 3 tests; all passed. Firmware-stage suite had 3 passes and 1 error because public worktree lacks `calliope_sram.bin`. No live audio. |
| `/root/recovery_hardening` | `/tmp/s22-luna-wave1-npu-20260923`, `codex/s22-wave1-npu-20260923`; exact pinned kernel worktree `/tmp/s22-kernel-npu-lifecycle-20260923` at `4e5c5ad7d950e4de0688b5663965f2075654b2ad` | Reassigned from recovery scouting to NPU implementation. Found missing-source fail-open in `power_notify_wait_resolved`, unbounded POWER_NOTIFY wait, ignored enqueue/result errors, and incomplete boot failure unwind. Implementing in the bounded kernel/preflight/test ownership. No BOOTUP, build, or device actions. |

### Independent review lane

| Actual task handle | Worktree / exact patch | Status |
|---|---|---|
| `/root/deployment_independent_review` | `/tmp/s22-luna-review-deploy-worker-20260923`, commit `03eba0700f65e0ef1576a50a1ab43dd2b325d5bd` | Explicit Luna Max, independent read-only review complete. Found three findings listed above; recommended fixing AVB verifier substitution and parent-path race before considering deployment hardening complete. Reviewer reran the 28-test suite and diff check. No operational deployment/device commands. |
| `/root/bluetooth_independent_review` | `/tmp/s22-luna-review-bt-worker-20260923`, commit `7bade3f376bb0e810baca799e4dc5ec8f17b2443` | Explicit Luna Max, read-only review in progress. Inspecting the bridge changes, existing HCI lifecycle candidate, and host negative tests. Host-only tests are permitted; no build or device access. |

The branches/worktrees are isolated from one another. The original checkout
remains dirty and divergent (`master` at `fb60a2c1...`, 44 ahead of
`origin/master`, no merge base); it was not used as the publication baseline.
The two preserved commits are `5928e980ea32355c43ca5240eb41c8aa862533f3`
(NPU gate/audio classifier and tests) and `295b8696b20ef2c342df1ea6031ac41d9bc52fe8`
(this task board). They are intentionally retained, not recreated. The
review branch merges fetched `origin/master` correction commit
`20605dbe623e0909cf219c3cae9ef7bb597b15a6` without rewriting those commits.

### Host receipts at this checkpoint

- `git show --check` on both preserved commits and Bluetooth commit: passed.
- `python3 tools/hardware/test-npu-boot-preflight.py`: passed synthetic
  pass/mismatch cases. `sh tools/hardware/test-npu-boot-probe.sh`: passed
  three ABI assertions and refusal checks.
- `python3 tools/hardware/test-audio-route-assessment.py`: 12 passed;
  `python3 tools/hardware/test-audio-progress-snapshot.py`: 8 passed.
- Bluetooth H4/IBS PTY/unit suite from the worker branch: 12 passed.
- `python3 tools/hardware/test-bt-hci-socket-restore.py --base-source
  "$PINNED_KERNEL/net/bluetooth/hci_sock.c" --patch
  tools/hardware/bt-hci-socket-restore.patch`: passed source application,
  lifecycle checks, and three mutation negatives using the exact clean kernel
  pin. Scope is source validation only.
- The optimized-mode NPU CLI still exits nonzero, but its diagnostic currently
  reports `power_notify_wait_resolved=true` when the pinned source is missing.
  This is a confirmed subgate false-green, not a passing readiness receipt;
  the NPU worker has the fix and missing-source regression assigned. BOOTUP
  remains unauthorized and disabled.
- AVB `verify_image` on the two existing recovery artifacts passed footer/hash
  checks; both use algorithm `NONE`. This does not establish Samsung
  authentication or bootability. No image was built or flashed here.
- Deployment hardening commit `03eba0700f65e0ef1576a50a1ab43dd2b325d5bd`
  adds explicit target/artifact/manifest/rollback checks, embedded fail-closed
  write handling and builder AVB verification. The coordinator reran
  `test-recovery-deployment-hardening.py` in normal, `python3 -O`, and
  `PYTHONOPTIMIZE=1` modes: 28/28 passed in each, including optimization-mode
  target/hash refusal checks. Syntax and patch checks passed. This
  is host-only; independent review is ongoing and no deployment command ran.
- Audio diagnostics commit `2d6d9a40c3d97064ef4a5ccc5ceb90071970b715`
  adds synchronized read-only source snapshots and child/PCM cleanup
  classification. The six suites listed above all passed under coordinator
  rerun. `test_audio_firmware_stage.py` remains 3/4 with one error because the
  public checkout lacks `calliope_sram.bin`; no private firmware was copied.
  No snapshot execute mode or live audio trial ran.
- Bluetooth HCI validator invoked once with a stale temp source path failed
  due to that path being absent; rerunning with `--base-source` against the
  exact pinned source above passed. No test artifact or source was modified by
  that initial path error.

### Refreshed live state — read-only

USB rescue SSH succeeded through the existing strict-host-key helper at
`2026-09-23 19:19 UTC`; live kernel is `5.10.260-g4e5c5ad7d950`, PID 1 is
`native-guardian`, and PID 1/SSH mount namespaces match.
Uptime was `69018.93` seconds. The resident assistant health endpoint returned
HTTP 200. A host-originated Tailscale ping reached the phone peer over USB in
5 ms; private peer identifiers and addresses are omitted. This proves current
peer reachability, not independent rescue if the kernel fails.

PID 1 mounts CACHE at `/cache` and the native overlay at `/newroot`, with
`/cache/s22-linux/upper` as upperdir. Overlay `/` had 34,844,672 bytes
available (94% used), 29,780 free inodes (22% used). The upperdir used
521,476 KiB; `/usr` accounted for 502,788 KiB, including `/usr/lib` at
411,952 KiB and `/usr/lib/python3.14` at 59,408 KiB. No disposable cache was
identified or removed. Arch `/srv/s22` is on persistent userdata with
102,382,280,704 bytes available and 1,652,508 free inodes. Do not install
packages or stage files into the native overlay without a destination-specific
space check; no phone files were written.

No device state changed; no hardware test, kernel build, deploy or reboot was
performed. The current phone remains on the known-running kernel/userspace.
The next experiment remains gated on completing and independently reviewing
the relevant host patch, validating exact candidate/artifact provenance, and
preserving USB rescue plus the existing rollback. In particular, do not retry
raw HCI on this kernel or submit NPU BOOTUP while ownership/unwind checks are
incomplete.
