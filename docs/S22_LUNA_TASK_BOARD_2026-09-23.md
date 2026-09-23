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

Installed client: `codex-cli 0.156.1`. Persistent Codex config selects
`gpt-6-luna` with primary reasoning effort `xhigh`; the installed model catalog
lists `max` as supported, but this running session does not expose a native
in-turn model/effort switch or runtime/session metadata. Coordinator evidence
is therefore `explicitly_configured`, not `runtime_reported`, and the primary
effort is not claimed to be `max`. Each worker below was launched through the
native collaboration interface with explicit `gpt-6-luna` and `max`; the tool
returned task handles but no opaque session IDs or runtime identity
attestation. No override/fallback is known for those explicit worker calls.

| Actual task handle | Worktree / branch | Assignment and current result |
|---|---|---|
| `/root/recovery_deploy_impl` | `/tmp/s22-luna-wave1-deploy-20260923`, `codex/s22-wave1-deploy-20260923` | Hardening chain `03eba0700f65e0ef1576a50a1ab43dd2b325d5bd`, `0187e3c6e07a01b368bb558a95ddec96dfaceded`, `027c6cd49f56e77ecd85b9b61aa3d2f6e98cb28c`, and final `fb479c9f28c294048a02ec19faf68363666400ba` are integrated (final as `dfb9ce3`). AVB verifier and SSH wrapper are content-pinned; execution is from a sealed snapshot; SSH PATH is trusted; staging fsync gates success. Final independent review approved. Coordinator reran 38 tests in normal, `-O`, and `PYTHONOPTIMIZE=1` with the trusted local AVB tool. No device actions. |
| `/root/bluetooth_impl` | `/tmp/s22-luna-wave1-bt-20260923`, `codex/s22-wave1-bt-20260923` | Initial bridge/test commit `7bade3f376bb0e810baca799e4dc5ec8f17b2443`, hardening `d3f50668ecf19c117a503f6cafaca19cc72df4e0`, and queue-overflow follow-up `f7f0d161e7bd9b82d30cb4d8b75e1ca3ca1a8a39` are integrated. Final attribution/cleanup-label fix `2af4d23f66d9bc857f3de45bc3f550dd1a886491` is integrated as `4163b89`. Tests assert the full 36-byte write and explicit 8-slot overflow; cleanup label is not an N_HCI-detach claim. Coordinator H4 12/12 and exact-pinned HCI source validation passed; independent final review approved. No kernel build/runtime HCI. |
| `/root/audio_diag_impl` | `/tmp/s22-luna-wave1-audio-20260923`, `codex/s22-wave1-audio-20260923` | Completed synchronized snapshot and cleanup classification; worker commit `2d6d9a40c3d97064ef4a5ccc5ceb90071970b715`, integrated as `2b55003`. Coordinator reran snapshot 13, route 18, wrapper cleanup 4, bind-node 4, PCM prepare 5, and sync-nodes 3 tests; all passed. Firmware-stage suite had 3 passes and 1 error because public worktree lacks `calliope_sram.bin`. No live audio. |
| `/root/recovery_hardening` | `/tmp/s22-luna-wave1-npu-20260923`, `codex/s22-wave1-npu-20260923`; kernel worktree `/tmp/s22-kernel-npu-lifecycle-20260923` | Readiness fix `d2852765b2b58bba434ba8ae1b28c078615ff13f` is integrated as `4397d61`. Lifecycle patch is kernel commit `f264b971c6917598347fc14823e7d41d1e4a54e0`, based on `4e5c5ad7d950e4de0688b5663965f2075654b2ad`; exported patch, source-check model and test are repo commit `07cc061226b400090e46ec4669d709803cd2255f`, integrated as `d07628f`. The draft now uses NULL session plus an opaque cookie in unused POWER_CTL params, request-ID matching and bounded close/error handling. Coordinator reran lifecycle/preflight normal and optimized tests; independent Luna review is running. Tests do not compile kernel C. No full build, BOOTUP, or device action. |
| `/root/userspace_close_range_impl` | `/tmp/s22-luna-wave2-userspace-20260923`, `codex/s22-wave2-userspace-20260923` | Commit `6b6432e507e5fa12579d8e0d8531519447d11e17` is integrated as `b3483e4`; only `test_clone3_compat.py` changed. Adds captured-output fallback, concurrent/repeated child cleanup, normal close range, CLOEXEC inheritance, invalid inputs and unshare behavior. Normal, `-O`, and `PYTHONOPTIMIZE=1` each passed 14 tests. Independent review found no behavior bug but requested portability/optimized-assert test improvements; follow-up is running. No device or system change. |

### Independent review lane

| Actual task handle | Worktree / exact patch | Status |
|---|---|---|
| `/root/deployment_independent_review` | `/tmp/s22-luna-review-deploy-worker-20260923` (`03eba07`), `/tmp/s22-luna-review-deploy-fix-20260923` (`0187e3c6e07a01b368bb558a95ddec96dfaceded`), and `/tmp/s22-luna-review-deploy-final-20260923` (`027c6cd49f56e77ecd85b9b61aa3d2f6e98cb28c`) | Explicit Luna Max. Independent final review confirmed post-open path pinning and parent fsync, but found a P2: wrapper snapshot is not authenticated against the canonical digest, and inherited PATH can replace bare `ssh`. The exact canonical wrapper digest and tests are assigned to the author; no deployment/device commands. |
| `/root/bluetooth_independent_review` | `/tmp/s22-luna-review-bt-worker-20260923`, `/tmp/s22-luna-review-bt-followup-20260923`, `/tmp/s22-luna-review-bt-overflow-20260923` (`f7f0d161e7bd9b82d30cb4d8b75e1ca3ca1a8a39`), and `/tmp/s22-luna-review-bt-final-20260923` (`2af4d23f66d9bc857f3de45bc3f550dd1a886491`) | Initial, second and final independent Luna Max reviews completed. Final review approved the byte-count, explicit overflow receipt, and precise cleanup label; H4 passed 12/12 and pinned-source validation passed. HCI patch remains unbuilt; no device/runtime proof. |
| `/root/npu_lifecycle_independent_review` | `/tmp/s22-luna-review-npu-repo-20260923` (`07cc061226b400090e46ec4669d709803cd2255f`) and `/tmp/s22-luna-review-npu-kernel-20260923` (`f264b971c6917598347fc14823e7d41d1e4a54e0`) | Explicit Luna Max independent review of ownership, timeout, callback/close and boot unwind is running; host model/source tests only, no kernel build or BOOTUP. |
| `/root/userspace_close_range_review` | `/tmp/s22-luna-review-userspace-20260923` (`6b6432e507e5fa12579d8e0d8531519447d11e17`) | Explicit Luna Max review completed: 14/14 passed in normal, `-O`, and `PYTHONOPTIMIZE=1`; no functional bug. It found three test-quality gaps: syscall availability tied to libc export, invalid-range ENOSYS handling, and child asserts optimized away under `PYTHONOPTIMIZE=1`. Author follow-up is active; host-only, no phone access. |

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
- The initial optimized-mode NPU CLI exposed a subgate false-green when
  lifecycle source was missing. The NPU worker fixed it in
  `d2852765b2b58bba434ba8ae1b28c078615ff13f`; coordinator reran normal and
  optimized synthetic tests and confirmed the public-worktree CLI reports
  `power_notify_wait_resolved=false`, `bootup_ready=false`,
  `bootup_authorized=false`, exit 2. BOOTUP remains unauthorized and disabled;
  kernel request ownership/unwind is still under implementation.
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

## Continuation checkpoint — 2026-09-23 19:59 UTC

The fetched execution correction at `origin/master` is applied. Its model
evidence policy and WIP-publication rule supersede only the corresponding
orchestration/publication wording above; hardware authorization, recovery,
privacy and architecture boundaries remain unchanged.

- Bluetooth test follow-up commit `2af4d23f66d9bc857f3de45bc3f550dd1a886491`
  is integrated as `4163b89`. It asserts the full 36-byte burst write,
  reports `bridge_queue_overflow=1 queued=8` only when the ninth frame meets a
  full queue, and calls cleanup `pty_cleanup_ioctl_result` (the N_TTY ioctl,
  not proof of production N_HCI detach). Independent Luna review
  `/root/bt_final_review`, explicitly selected as `gpt-6-luna` / `max`,
  approved this delta. Model metadata is not exposed by the collaboration
  runtime; evidence level remains explicit selection, not runtime attestation.
- Coordinator reran the H4 suite (12/12), exact-pinned HCI source validator,
  `py_compile`, and `git diff --check`; all passed. These remain host/source
  checks, not a kernel build or live Bluetooth test.
- Deployment independent review of `0187e3c6e07a01b368bb558a95ddec96dfaceded`
  confirms the AVB verifier snapshot and dirfd staging fixes, and the 35-case
  normal/optimized suites pass. Follow-up commit
  `027c6cd49f56e77ecd85b9b61aa3d2f6e98cb28c` integrates as `ac52713`: the
  approved SSH wrapper is copied to a sealed memfd and sourced via its passed
  descriptor, with a pathname-replacement regression; the stage parent is
  fsynced after mkdir, with ordering and fsync-failure regressions. Coordinator
  reran 37/37 tests in normal, `-O`, and `PYTHONOPTIMIZE=1` using the trusted
  local AVB tool (`S22_AVBTOOL`); independent final review confirmed these two
  fixes but found a remaining P2: the wrapper bytes are not pinned to the
  canonical digest, and inherited PATH can replace bare `ssh`. Those are now
  assigned for a follow-up; no deployment mode has run. Without
  `S22_AVBTOOL`, three AVB-specific test cases skip in this public worktree.
  The builder `--help` probe reads its required cpio before parsing help and
  cannot complete here because that build input is absent; no build was
  attempted.
- NPU lifecycle ownership/unwind edits remain in the existing exact-pinned
  kernel worktree. No BOOTUP, build, or live test has occurred; the next
  useful deliverable is the actual patch plus executable regressions, followed
  by independent review. Source review caught that POWER_CTL request state
  must not retain a session pointer across timeout/close; the worker is
  updating it to pass NULL (supported by `msgid_issue`) and carry the opaque
  completion cookie in unused POWER_CTL parameters.

### Refreshed phone and storage evidence — read-only

At `2026-09-23T19:59:58Z`, strict-host-key USB SSH succeeded. Kernel remains
`5.10.260-g4e5c5ad7d950`, PID 1 remains `native-guardian`, uptime was 71,420 s,
and PID 1 and the SSH probe have the same mount namespace. The resident
assistant health endpoint returned HTTP 200. No reboot or phone mutation was
performed.

The actual `/` overlay reports 610,861,056 bytes total, 563,433,472 used and
34,844,672 available (94%); 8,620/38,400 inodes are used. Persistent `/srv/s22`
userdata reports 112,233,304,064 bytes total, 9,834,246,144 used and
102,382,280,704 available; 1,652,508 inodes remain free. Mount metadata names
`/cache/s22-linux/upper` as the overlay upperdir, but `/cache` is not visible
inside this PID 1/SSH root view. Visible-root `du` totals only about 4.7 MiB,
so it does not explain the overlay's used blocks. The underlying upper-layer
consumers therefore remain unidentified; no cleanup or package installation
was attempted.

The strict USB SSH path is currently usable, but it depends on this running
kernel and is not an independent rescue route if that kernel fails. A
Tailscale ping observed over the same USB path does not change that. No new
recovery experiment is queued until a genuinely independent rescue method and
candidate-specific rollback are verified; this is the exact outstanding
device gate, not a host-work blocker.

## Follow-on host implementation — 2026-09-23 20:12 UTC

- Independent deployment re-review of `027c6cd49f56e77ecd85b9b61aa3d2f6e98cb28c`
  closed the post-open pathname race and parent-fsync findings, but found a
  remaining P2: the opened wrapper snapshot is not authenticated against the
  canonical helper digest, and inherited `PATH` can redirect its bare `ssh`
  command. The author is implementing canonical SHA-256 pinning and a trusted
  `ssh` lookup with negative tests. The current 37-test results do not close
  this finding; deployment remains blocked.
- A later-wave Luna worker `/root/userspace_close_range_impl` is implementing
  the missing host behavior matrix in isolated worktree
  `/tmp/s22-luna-wave2-userspace-20260923`, limited to the close_range filter
  and tests. Coordinator baseline: `test_clone3_compat.py` passed 8 tests and
  the separately compiled close_range filter self-test reported ENOSYS with
  seccomp/NoNewPrivs; no device or package operation.
- NPU source-lifetime review now requires POWER_CTL to carry no session pointer
  across timeout/close: `msgid_issue` supports NULL and the POWER_CTL callback
  ignores that argument, while the opaque waiter cookie travels in unused
  `param0/param1`. The implementation worker is correcting and testing this;
  no patch has yet been accepted or built.

The native phone baseline remains the read-only `5.10.260-g4e5c5ad7d950`
kernel with `native-guardian` and HTTP 200 assistant health from the 19:59 UTC
probe. No phone state changed during this follow-on work.

## Implementation results — 2026-09-23 20:18 UTC

- Deployment follow-up `fb479c9f28c294048a02ec19faf68363666400ba` is
  integrated as `dfb9ce3`. It pins canonical `tools/s22-ssh` SHA-256,
  rejects regular-file impostors, replaces inherited PATH with the verified
  root-owned `/usr/bin`, and retains sealed-FD race and staging-parent fsync
  checks. Independent Luna review approved the exact commit. Coordinator and
  reviewer each passed 38/38 in normal, `-O`, and `PYTHONOPTIMIZE=1` modes
  with trusted local AVB tool; no deploy mode ran.
- NPU lifecycle patch `f264b971c6917598347fc14823e7d41d1e4a54e0` is based on
  pinned kernel `4e5c5ad7d950e4de0688b5663965f2075654b2ad`. Its sanitized
  exported patch and host regressions are integrated as `d07628f`. Normal and
  optimized lifecycle model/source tests plus preflight tests pass. Independent
  Luna review is running. These tests do not compile/execute kernel C; no full
  kernel build or BOOTUP occurred. `checkpatch.pl` reported no errors but four
  warnings (two extern and two CamelCase uses of existing APIs).
- Close-range userspace reliability suite `6b6432e507e5fa12579d8e0d8531519447d11e17`
  is integrated as `b3483e4`. Coordinator and independent review each passed
  14/14 in normal, `-O`, and `PYTHONOPTIMIZE=1`. Review found no functional
  issue, but follow-up is improving older-libc/ENOSYS portability and replacing
  child `assert`s removed by optimized Python. These tests establish host
  syscall behavior only, not the phone kernel.
- Coordinator reran deployment (38/38 × three modes), NPU four normal/
  optimized invocations, Bluetooth H4 (12/12) and pinned-source validation,
  plus audio route/snapshot/cleanup suites (18/13/4/4/5/3). These remain
  separate from builds and physical driver acceptance.
- The original dirty checkout and both preserved commits remain untouched.
  All changes are WIP on the unmerged review branch; none has been merged or
  deployed. Current phone state is unchanged from the read-only probe; no
  coordinator device operation occurred.

## Continued implementation and review — 2026-09-23 20:27 UTC

- The userspace close-range author follow-up `59deecd67f502b7c3a81c2bccebea22da9639179`
  was inspected and integrated as `314085f`. It replaces libc-symbol probing
  with a temporary `__NR_close_range` syscall wrapper, establishes a suite-wide
  ENOSYS gate, and makes subprocess output checks survive optimized Python.
  Independent reviewer `/root/userspace_close_range_review` found no remaining
  issue in the requested scope. Coordinator reran 14/14 in normal,
  `python3 -O`, and `PYTHONOPTIMIZE=1`, plus `py_compile` and `git diff --check`.
  This remains host behavior, not proof of the S22 kernel syscall.
- Independent reviewer `/root/npu_lifecycle_independent_review` inspected the
  exact repo snapshot `07cc061226b400090e46ec4669d709803cd2255f` and kernel
  candidate `f264b971c6917598347fc14823e7d41d1e4a54e0`. The patch matches the
  pinned base-to-candidate diff and reverse-applies, but review found a P2
  timeout/publication race: protocol work can validate an active POWER_CTL
  waiter, pause, then publish after timeout has removed the waiter and reported
  failure. The candidate is not ready for build/deploy/BOOTUP. A follow-up is
  assigned to `/root/recovery_hardening` in its existing isolated kernel
  worktree, with an adversarial cancel-vs-publish test; BOOTUP stays gated.
  Reviewer noted mailbox `msgid` reuse is not exercised by the Python model.
- A new host implementation worker `/root/input_power_test_impl` was launched
  on branch `codex/s22-wave2-input-power-20260923`, worktree
  `/tmp/s22-luna-wave2-input-power-20260923`, to add synthetic-root tests for
  read-only input/power/thermal/camera inventory and event observation. The
  request explicitly selected `gpt-6-luna` / `max`; collaboration does not
  expose independent runtime/session attestation, so this records selection,
  not attestation. No phone access is assigned.

### Refreshed live state and storage — read-only, 2026-09-23 20:27 UTC

- Strict-host-key USB SSH succeeded. Running kernel is
  `5.10.260-g4e5c5ad7d950`, PID 1 is `/system/bin/native-guardian`, uptime was
  73,089.83 s, and the assistant health endpoint returned `{"status":"ok"}`.
  PID 1 and SSH report the same mount namespace ID; their mountinfo paths are
  rooted differently (`/newroot` for PID 1 and `/` for SSH), so both views were
  inspected rather than inferred from the namespace ID alone.
- SSH `/` is the live overlay with `lowerdir=/native-lower`,
  `upperdir=/cache/s22-linux/upper`, `workdir=/cache/s22-linux/work`. The
  overlay has 34,844,672 bytes available (94% used) and 29,780 free inodes.
  Persistent `/srv/s22` has 102,382,280,704 bytes and 1,652,508 inodes free.
- The upperdir was measured through `/proc/1/root/cache/s22-linux/upper`:
  521,476 KiB total; `/usr` is 502,788 KiB, of which `/usr/lib` is 411,952
  KiB. Largest inspected entries include `libLLVM.so.22.1` (171,856 KiB),
  `python3.14` (59,408 KiB), `libgallium-26.1.6.so` (36,312 KiB), and
  `libvulkan_radeon.so` (16,948 KiB). These are library/runtime-sized upper
  objects, but their active-vs-shadowed status is unproven. No files were
  deleted and no packages were installed. The direct merged `/usr` view is
  only 4,148 KiB, so upperdir/merged-view accounting still needs explanation
  before any cleanup.
- Current USB SSH is usable but runs over the kernel being tested. No
  independent recovery path was verified; Tailscale must not be counted as
  independent rescue while routed over this same USB/kernel path. No device
  write, build, deployment, reboot, or driver trial occurred.

### Next device gate

The next NPU live experiment remains prohibited until the timeout/publication
race is corrected and independently reviewed, the patch builds against the
exact pinned kernel, `npu-boot-preflight.py` passes with the required firmware
and configuration evidence, and the original independent-rescue, rollback,
and authorization gates are satisfied. BOOTUP remains false/unauthorized in
the absence of those gates. Before any installation or cleanup, explain the
4,148 KiB merged `/usr` versus 502,788 KiB upper `/usr` discrepancy and confirm
destination-specific free space; current 34.8 MB overlay headroom is not
installation clearance.
