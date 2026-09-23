# S22 driver mission host receipts — 2026-09-23

Status: WIP host/source checks only. This receipt does not establish that any
new driver works on the phone. No kernel build, device write, hardware trial,
or reboot was performed for this receipt.

## Preserved implementation commits

- `5928e980ea32355c43ca5240eb41c8aa862533f3` — NPU BOOTUP readiness gate,
  audio DMA progress classifier, and host tests.
- `295b8696b20ef2c342df1ea6031ac41d9bc52fe8` — task board at the prior
  checkpoint.
- `7bade3f376bb0e810baca799e4dc5ec8f17b2443`, integrated as
  `c7c2080` — Bluetooth bridge cleanup reporting and expanded executable
  host regressions.
- `d3f50668ecf19c117a503f6cafaca19cc72df4e0`, integrated as
  `82542fe` — Bluetooth test temp isolation and capability-denial mutations.
- `f7f0d161e7bd9b82d30cb4d8b75e1ca3ca1a8a39`, integrated as
  `a5ca9d9` — exact queue-overflow integration receipt assertions.
- `03eba0700f65e0ef1576a50a1ab43dd2b325d5bd`, integrated as
  `9dc83b3` — RECOVERY deployment/build hardening and optimization-mode
  negative tests.
- `0187e3c6e07a01b368bb558a95ddec96dfaceded`, integrated as
  `9dfec4b` — reviewed deployment fixes for verifier identity, wrapper
  symlinks, and remote staging path races.
- `2d6d9a40c3d97064ef4a5ccc5ceb90071970b715`, integrated as
  `2b55003` — synchronized audio snapshots and cleanup classification.
- `d2852765b2b58bba434ba8ae1b28c078615ff13f`, integrated as
  `4397d61` — fail-closed NPU readiness when lifecycle source is absent.

The first two commits were kept intact and remain ancestors of the review
branch. The Bluetooth worker commit was based on the review branch's exact
pre-correction implementation parent and was cherry-picked without changing
its patch content.

## Commands and outcomes

| Check | Result | Evidence level / limit |
|---|---|---|
| `git show --check` for the two preserved commits and Bluetooth worker commit | Pass | Patch whitespace/integrity only |
| `python3 tools/hardware/test-npu-boot-preflight.py` | Pass | Synthetic pass/mismatch cases; no firmware or device use |
| `python3 -O tools/hardware/test-npu-boot-preflight.py` after missing-source fix | Pass | Optimization-mode synthetic cases; missing/unreadable lifecycle source cannot resolve a gate |
| `sh tools/hardware/test-npu-boot-probe.sh` | Pass, 3 assertions plus refusal checks | Host ABI and safety refusal only |
| `python3 tools/hardware/test-audio-route-assessment.py` | Pass, 12 tests | Classifier logic only |
| `python3 tools/hardware/test-audio-progress-snapshot.py` | Pass, 8 tests | Snapshot planning/classification only; no stream capture |
| `python3 tools/hardware/test-bt-h4-ibs-bridge.py` after Bluetooth test-hardening follow-up | Pass, 12 tests | Host PTY/unit tests; binaries are isolated in a per-run temporary directory; no UART/controller/HCI runtime |
| `python3 tools/hardware/test-bt-hci-socket-restore.py --base-source "$PINNED_KERNEL/net/bluetooth/hci_sock.c" --patch tools/hardware/bt-hci-socket-restore.patch` after follow-up | Pass | Applied candidate in a temporary tree, validated source contracts and capability-denial branches, and rejected mutated candidates; not a kernel build/runtime test |
| `python3 tools/hardware/test-recovery-deployment-hardening.py` after hardening follow-up | Pass, 35 tests in normal, `python3 -O`, and `PYTHONOPTIMIZE=1` modes | Host mocks, optimization-mode negative cases, and synthetic AVB footer/corruption test; no operational deploy path |
| Audio snapshot / route / cleanup / bind-node / PCM-prepare / sync-node suites | Pass, respectively 13 / 18 / 4 / 4 / 5 / 3 tests | Host-only diagnostics and fake child/PCM cleanup; no live audio |
| `python3 -O tools/hardware/npu-boot-preflight.py --repo "$REPO"` after fix | Exit 2; `bootup_ready=false`, `bootup_authorized=false`, and missing-source lifecycle gates false | Fail-closed source/artifact audit only; NPU request ownership/unwind and runtime remain unproven |
| `avbtool.py verify_image` against the existing audio-extra and HCI candidate recovery artifacts | Pass, footer/hash checks | Both artifacts use AVB algorithm `NONE`; this is not Samsung authentication or proof of bootability. No image is included here. |

An earlier HCI test invocation used a stale temporary source path and failed
because the file was absent. It was replaced with the `--base-source` form
above against the exact pinned kernel checkout; that invocation passed. This
path error did not modify source or artifacts.

The firmware-stage audio test suite ran 3/4 tests and exited with one error:
the public worktree does not contain `calliope_sram.bin`. This missing
firmware fixture was not copied into the branch; the error is an environment
coverage gap, not evidence of an audio-stage runtime failure.

Independent Luna review of initial commit
`03eba0700f65e0ef1576a50a1ab43dd2b325d5bd` found three issues: arbitrary AVB
verifier substitution, a symlinked SSH wrapper, and remote staging parent-path
replacement. Fixes landed in `0187e3c6e07a01b368bb558a95ddec96dfaceded`
(integrated as `9dfec4b`): verifier SHA-256 is pinned and run from a sealed
snapshot, both deployers reject symlinked wrappers, and remote staging uses
validated directory fds with ownership/mode/replacement checks. The
coordinator reran 35 tests in normal, `-O`, and `PYTHONOPTIMIZE=1` modes;
py_compile, help, and diff checks passed. The second independent review is
active. The pinned AVB tool bytes are currently available only from the local
ignored tool file; a fresh clone must supply that exact trusted tool. The
deployment entrypoints were not invoked; no live staging or flash occurred.

The Bluetooth independent Luna review also completed. It passed H4 12/12 and
the exact-pinned-source HCI validator, found no defect in the changed C
cleanup-reporting path, and requested two P2 test-hardening changes: isolate
test binaries under a per-run temporary directory and prove capability denial
control flow with a negative mutation. A P3 naming clarification was also
requested. The author follow-up is committed as
`d3f50668ecf19c117a503f6cafaca19cc72df4e0` and coordinator host reruns pass;
independent re-review confirmed those fixes. It found one remaining P3: the
integrated queue-overflow test does not assert that the ninth command caused
termination rather than an unrelated early bridge failure. The author is
follow-up `f7f0d161e7bd9b82d30cb4d8b75e1ca3ca1a8a39` adds exactly eight
accepted-command records, ninth-command nonzero termination, and detach-result
assertions; coordinator reruns pass. A final independent review of that small
follow-up is active. This does not verify the HCI kernel patch by compilation
or runtime; device deployment remains blocked.

The earlier NPU missing-source subgate false-green is fixed in commit
`d2852765b2b58bba434ba8ae1b28c078615ff13f` (integrated as `4397d61`). Normal
and optimized preflight tests pass; an optimized CLI probe on the public
checkout still exits 2 and now reports missing-source lifecycle readiness
gates false. This does not repair actual kernel callback ownership or boot
error unwind; that implementation remains in progress, and BOOTUP stays
disabled.

## Live read-only snapshot

At approximately 2026-09-23 19:19 UTC, the existing strict-host-key USB SSH
path succeeded. The phone reported kernel `5.10.260-g4e5c5ad7d950`, PID 1
`native-guardian`, and uptime `69018.93` seconds. The resident assistant health
endpoint returned HTTP 200. The phone's Tailscale peer answered a host ping in
5 ms over the USB route; identifiers and addresses are intentionally omitted.
This is present-tense reachability, not proof that remote rescue survives a
kernel failure. No boot/reset log or private trace is reproduced here.

The native root is an overlay with CACHE-backed upperdir. It had 34,844,672
bytes available (94% used) and 29,780 free inodes (22% used). The measured
upperdir usage was 521,476 KiB, of which `/usr` was 502,788 KiB and `/usr/lib`
411,952 KiB. No disposable cache was identified or deleted. Persistent Arch
userdata had 102,382,280,704 bytes and 1,652,508 inodes available. No package
installation or phone file staging was attempted.

## Not established

- No new kernel build, RECOVERY image build, deploy, reboot, or live driver
  experiment.
- No physical Bluetooth discovery/pairing, audio DMA progress/playback,
  NPU firmware boot/inference, touch acceptance, or GPU presentation result.
- Current storage headroom does not justify installing packages into the
  native overlay; destination-specific space and cleanup evidence are still
  required.
- NPU lifecycle ownership/unwind, independent review, image authentication,
  and the relevant recovery/rollback gates remain open.

## Follow-up implementation/review receipt — 2026-09-23 20:27 UTC

### Close-range test hardening

Author commit `59deecd67f502b7c3a81c2bcbea22da9639179` was reviewed at its
exact commit and integrated on this unmerged WIP branch as `314085f`. The
change is limited to `tools/hardware/test_clone3_compat.py`. It compiles a
temporary wrapper around `syscall(__NR_close_range)`, explicitly skips only
when headers or the running host kernel lack the syscall, and replaces child
`assert` statements with explicit errors so optimized Python still checks
child output.

Independent review by `/root/userspace_close_range_review` found no remaining
issue in scope. Coordinator rerun on the integrated branch:

| Command | Result |
|---|---|
| `python3 tools/hardware/test_clone3_compat.py -v` | 14/14 pass |
| `python3 -O tools/hardware/test_clone3_compat.py -v` | 14/14 pass |
| `PYTHONOPTIMIZE=1 python3 tools/hardware/test_clone3_compat.py -v` | 14/14 pass |
| `python3 -m py_compile tools/hardware/test_clone3_compat.py` | Pass |
| `git diff --check HEAD^ HEAD` | Pass |

Review evidence is host-only. The reviewer did not claim runtime/session Luna
metadata, so its model evidence is not independently attested by that report.
No phone or system state was changed.

### NPU lifecycle independent review

Independent review covered repository/test commit
`07cc061226b400090e46ec4669d709803cd2255f` and kernel commit
`f264b971c6917598347fc14823e7d41d1e4a54e0` based on pinned kernel
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`. The exported patch matches the
kernel candidate and reverse-applies; source/model tests pass normally and
under `python3 -O`; preflight correctly exits 2 without required config/AIE
inputs and leaves BOOTUP unauthorized. No kernel compile or device test ran.

Review found a P2 race: the protocol worker can check an active POWER_CTL
waiter, be descheduled, then publish after the timeout path removes the waiter
and reports failure. Passing a NULL session prevents a session UAF but does
not prevent that stale power transition. The candidate is not device-ready.
The fix is assigned to `/root/recovery_hardening` with an adversarial
cancel-versus-publish regression, followed by another independent review.
Mailbox `msgid` reuse remains outside the existing Python model coverage.

### Current read-only phone and storage receipt

Strict-host-key USB SSH at approximately 2026-09-23 20:27 UTC reported kernel
`5.10.260-g4e5c5ad7d950`, PID 1 `native-guardian`, uptime 73,089.83 s, and
assistant health `{"status":"ok"}`. PID 1 and the SSH command report the same
mount namespace ID, but their mountinfo roots differ (`/newroot` and `/`),
which is why the upperdir was inspected through `/proc/1/root`.

The SSH root is an overlay with upperdir `/cache/s22-linux/upper`; `/` has
34,844,672 bytes available (94% used) and 29,780 free inodes. Persistent
`/srv/s22` has 102,382,280,704 bytes and 1,652,508 inodes free. The measured
upperdir totals 521,476 KiB, including `/usr` at 502,788 KiB and `/usr/lib` at
411,952 KiB. Large entries include LLVM (171,856 KiB), Python 3.14 (59,408
KiB), Mesa Gallium (36,312 KiB), and RADV Vulkan (16,948 KiB). These are
library/runtime-sized upper-layer objects, but their active-versus-shadowed
status is not established. Direct merged `/usr` measured only 4,148 KiB; that
discrepancy is unresolved. No cleanup, package install, staging, reboot, or
other phone mutation was performed.

USB SSH is current access but depends on the running kernel. Independent
recovery/rescue connectivity is not established. No newly verified hardware
functionality resulted from this read-only probe.

### Current worker queue

- `/root/input_power_test_impl` was launched on
  `/tmp/s22-luna-wave2-input-power-20260923` for host-only executable tests of
  read-only input/power/thermal/camera inventory and bounded event observation.
  The requested selection was `gpt-6-luna` / `max`; runtime/session metadata is
  unavailable. It has no device authorization.
- `/root/recovery_hardening` is implementing the reviewed NPU
  timeout/publication handshake in its existing exact-pinned kernel worktree.
  No BOOTUP, build, or live experiment is authorized by that assignment.

## Continuation update — 2026-09-23

The later coordinator checkpoint supersedes the older pending-review and
storage statements above:

- Bluetooth follow-up `2af4d23f66d9bc857f3de45bc3f550dd1a886491`, integrated
  as `4163b89`, closes the final independent test-quality findings. It checks
  all 36 input bytes were written, requires the exact eight-slot overflow
  receipt and uses `pty_cleanup_ioctl_result` without claiming N_HCI detach.
  Independent Luna review approved the delta; coordinator reran H4 12/12 and
  pinned-source HCI validation. No kernel build or runtime test occurred.
- Deployment follow-up `027c6cd49f56e77ecd85b9b61aa3d2f6e98cb28c`, integrated
  as `ac52713`, closes the post-open wrapper pathname race by executing a
  sealed memfd snapshot and closes the staging-parent durability gap with
  parent fsync after mkdir. The new race, ordering and fsync-failure cases
  passed. Coordinator and independent review each ran 37/37 tests in normal,
  `-O`, and `PYTHONOPTIMIZE=1` modes with `S22_AVBTOOL` set to the trusted
  local avbtool; no cases skipped. Without that local tool, three
  AVB-specific tests skip. Independent
  review found one remaining P2: the snapshot is not pinned to the canonical
  `tools/s22-ssh` digest, so a regular executable Bash replacement present
  before validation is accepted. The reviewer also found inherited PATH can
  substitute the wrapper's bare `ssh`; both fixes are now assigned. No deploy
  operation ran. The image-builder `--help` probe reads a required build cpio
  before argument parsing and fails in the public worktree because that input
  is absent; no build was attempted.
- NPU kernel ownership/unwind implementation is still in progress. Source
  review caught that a cookie placed in `nw.session` would be dereferenced as
  a real session by `msgid_issue_save_ref()` with `CONFIG_DSP_USE_VS4L`. A
  subsequent lifetime review established that POWER_CTL must pass a NULL
  session because the request may outlive timeout/close; the pinned
  `msgid_issue` path accepts NULL, and the POWER_CTL callback ignores that
  argument. The worker is moving the opaque cookie to unused `param0/param1`.
  These draft flaws were caught before commit; no BOOTUP or device action
  occurred.
- Current read-only USB SSH still reaches the running `5.10.260-g4e5c5ad7d950`
  kernel and resident assistant (health HTTP 200). `/` has 34,844,672 bytes
  free (94% used), 29,780 free inodes; `/srv/s22` has 102,382,280,704 bytes
  and 1,652,508 inodes free. Overlay metadata points at `/cache/s22-linux/upper`,
  but `/cache` is outside the visible PID 1/SSH root view; visible-root `du`
  accounts for only about 4.7 MiB and does not identify the backing usage.
  No cleanup/install was attempted. USB SSH and a peer ping over that same
  transport are not independent rescue from a failed kernel.
- No new hardware functionality was verified. The next device experiment
  remains blocked on independently usable rescue plus a candidate-specific
  rollback gate; source and host tests continue independently.

## Follow-on execution — 2026-09-23 20:12 UTC

- Independent review of deployment follow-up `027c6cd49f56e77ecd85b9b61aa3d2f6e98cb28c`
  confirms the post-open pathname replacement and staging-parent fsync fixes.
  It found a remaining P2: a regular but malicious Bash wrapper present before
  validation is accepted because the sealed snapshot is not compared to the
  canonical `tools/s22-ssh` SHA-256; inherited `PATH` can also substitute bare
  `ssh`. The worker is implementing the reviewed canonical digest pin and
  trusted lookup with negative tests. No deployment or device access occurred.
- The full 37-test deployment suite passed in normal, `python3 -O`, and
  `PYTHONOPTIMIZE=1` modes with the trusted local AVB tool selected through
  `S22_AVBTOOL`; no skips. `py_compile`, deployer `--help`, and diff checks
  passed. Without that tool, three AVB-specific cases skip. The image builder
  help attempt read its required `builds/audio-early-20260922/ramdisk.cpio`
  before parsing arguments; that fixture is absent in the public worktree, so
  the probe failed before build execution. No build was attempted.
- The next-wave `/root/userspace_close_range_impl` Luna worker is implementing
  a bounded host-only semantics/regression suite in an isolated worktree.
  Before its changes, the coordinator ran `python3
  tools/hardware/test_clone3_compat.py` (8 passed) and compiled/ran the filter's
  `--self-test` (ENOSYS, seccomp filter, NoNewPrivs verified). No system or
  phone state was changed.
- NPU lifetime review now rejects retaining a session pointer across a
  POWER_CTL wait that can outlive timeout/close; the draft uses a NULL session
  (accepted by the pinned `msgid_issue`) and stores its opaque cookie in unused
  POWER_CTL `param0/param1`. This is under implementation and still needs
  executable tests and independent review. NPU BOOTUP remains disabled.
- Current phone state remains as in the read-only 19:59 UTC probe; no new
  hardware test, build, deployment, reboot, or filesystem cleanup occurred.

## Implementation and review checkpoint — 2026-09-23 20:18 UTC

- Deployment final follow-up `fb479c9f28c294048a02ec19faf68363666400ba`,
  integrated as `dfb9ce3`, adds the canonical SSH-wrapper digest pin, rejects
  a pre-validation regular Bash shim, and forces the verified root-owned
  `/usr/bin` for `ssh`. Independent Luna review approved the exact commit.
  Coordinator reran 38/38 tests in normal, `-O`, and `PYTHONOPTIMIZE=1` modes
  with the trusted local AVB tool selected; no skips. Pycompile and diff checks
  pass. This closes the review findings but does not mean deployment was run.
- NPU lifecycle kernel commit `f264b971c6917598347fc14823e7d41d1e4a54e0`
  targets exact base `4e5c5ad7d950e4de0688b5663965f2075654b2ad`. Its
  sanitized patch and host tests are in repo commit
  `07cc061226b400090e46ec4669d709803cd2255f`, integrated as `d07628f`.
  Coordinator reran the lifecycle model/source test normal and `-O` with the
  exact kernel worktree, and preflight tests normal and `-O`; all passed. The
  patch reverse-applies to the committed candidate. `checkpatch.pl` reported
  zero errors but two extern warnings and two CamelCase warnings against
  existing APIs. Independent Luna review is active. These tests are a host
  reference model and static source contracts only; no kernel C compilation,
  full build, BOOTUP or device runtime was performed. Firmware work is not
  cancelled on timeout; this remains a runtime concern.
- The close_range follow-up `6b6432e507e5fa12579d8e0d8531519447d11e17`,
  integrated as `b3483e4`, expands `test_clone3_compat.py` without changing the
  C filter. Coordinator and independent reviewer each passed 14/14 tests in
  normal, `python3 -O`, and `PYTHONOPTIMIZE=1` modes. Review found no
  functional defect, but requested three quality fixes: use the syscall when
  libc lacks an exported wrapper, handle ENOSYS consistently in unsupported
  kernels, and avoid child `assert`s that disappear under `PYTHONOPTIMIZE=1`.
  The author is implementing these. This validates host behavior, not the
  phone's downstream close_range implementation.
- Coordinator reran Bluetooth H4 12/12 and pinned-source HCI checks, plus
  audio host suites with 18/13/4/4/5/3 passing tests. No kernel build,
  deployment, live Bluetooth/audio, or NPU hardware trial occurred.
- Last verified phone state remains the read-only 19:59 UTC snapshot:
  `5.10.260-g4e5c5ad7d950`, `native-guardian`, healthy resident assistant,
  root overlay 94% used and userdata with ample headroom. The upperdir path is
  not visible from that root view; no cleanup/install was attempted. USB SSH
  remains a current access path, not rescue for a failed kernel. No coordinator
  device operation occurred after that probe.
