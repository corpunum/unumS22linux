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

- `/root/input_power_test_impl` implemented the host-only collector tests in
  `/tmp/s22-luna-wave2-input-power-20260923`, commits `e2163e9` and `77a78c9`.
  Independent review of `e2163e9` found a deadline overrun when an event FD
  remained continuously readable; the follow-up adds an inner drain deadline
  check and a deterministic regression. Coordinator passes five tests in each
  normal, `-O`, and `PYTHONOPTIMIZE=1` run. Independent re-review by
  `/root/input_power_independent_review` approved `77a78c9`; its deterministic
  test also failed against the pre-fix parent as intended. Requested selection
  was `gpt-6-luna` / `max`, but runtime/session metadata is unavailable. No
  device authorization was given.
- `/root/recovery_hardening` is implementing the reviewed NPU
  timeout/publication handshake in its existing exact-pinned kernel worktree.
  No BOOTUP, build, or live experiment is authorized by that assignment.

### Read-only input/power/camera inventory

At approximately 2026-09-23 20:36 UTC, the documented command
`python3 /usr/local/bin/input-power-readiness.py --camera` failed because the
script is absent from that installed path. No staging or installation was
attempted. A direct read-only sysfs/dev-node fallback found `sec_touchscreen`
at input event7 and power keys at event0/event1; battery reported 100%, Full,
Good, 27.6 C; sampled CPU zones were 31 C, G3D 32 C, and NPU 31 C. Exynos
ISP/MFC/JPEG/scaler video nodes were present. A `max77705-fuelgauge/online`
read returned EINVAL. No physical finger/key event was observed, and no camera
was opened or streamed. This is node enumeration and telemetry only, not
physical acceptance.

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

## NPU publication-race follow-up receipt — 2026-09-23 20:45 UTC

Kernel candidate commit `4c20670269e800454a5daacb9a01856ad1a792ae` is based on
the prior lifecycle candidate `f264b971c6917598347fc14823e7d41d1e4a54e0`
and pinned base `4e5c5ad7d950e4de0688b5663965f2075654b2ad`. The repository
patch/test commit `1908c624bc4a2066476488d9b196216b2a242820` integrates as
`0548764`. A lease plus final authorization prevents an uncommitted publisher
from sending after cancellation; once authorization wins, the waiter drains
the synchronous mailbox post before its stack storage is removed. Late
callbacks are ignored after cancellation.

Coordinator checks on the integrated branch passed:

| Check | Result |
|---|---|
| Lifecycle source/model suite, normal and `python3 -O` | Pass both |
| NPU preflight tests, normal and `python3 -O` | Pass both |
| Python syntax checks | Pass |
| Exact exported patch reverse-apply against candidate | Pass |
| Pinned kernel base-to-candidate `git diff --check` | Pass |

Independent review by `/root/npu_lifecycle_independent_review` is active on
separate exact repo/kernel worktrees. It must resolve whether synchronous
mailbox publication is sufficiently bounded to make draining safe; the
caller wait may exceed its nominal 12 seconds if the post stalls. The
`CONFIG_NPU_USE_BOOT_IOCTL` variant has not been compiled. `checkpatch.pl
--no-tree --strict` reports a missing `Signed-off-by` attestation, missing
commit description, and three `extern`-in-C warnings. No DCO signoff was
invented. This remains internal WIP, not an upstream submission.

All evidence is source plus host model/test. No kernel C compilation, full
kernel build, BOOTUP, device deployment, or live NPU functionality was tested.

## Current WLAN acceptance receipt — 2026-09-23 20:48 UTC

The installed sanitized WLAN acceptance script passed all 13 checks over the
existing strict-host-key USB session. Coverage includes CNSS driver readiness,
WPA2/CCMP association, native/Arch DNS, DNS and TLS-verified HTTPS explicitly
bound to `wlan0`, preserved USB carrier/default routing, and resident-model
health. The script did not emit SSID, MAC, address, gateway, or DNS IP. It
reports reboot/autostart, USB-disconnected rescue, suspend/resume, roaming and
sustained throughput as not tested. This confirms WLAN works now; it does not
prove Tailscale/remote rescue independent of USB or survival of a kernel fault.

## Execution-correction and final NPU review receipt — 2026-09-23 21:00 UTC

Fetched origin without integrating over active work. Read the complete
`origin/master:docs/S22_LUNA_EXECUTION_CORRECTION_2026-09-23.md` and original
mission. Original local commits `5928e980ea32355c43ca5240eb41c8aa862533f3`
and `295b8696b20ef2c342df1ea6031ac41d9bc52fe8` remain unchanged, reachable
on `codex/s22-luna-coordinator-20260923`, and included as ancestors of the
review branch. No reset, stash, discard, or original-checkout edit occurred.

Final independent NPU re-review approved the race fix at kernel commit
`4c20670269e800454a5daacb9a01856ad1a792ae` and repository patch/test commit
`1908c624bc4a2066476488d9b196216b2a242820`. Tests passed normally and under
`python3 -O`; exact patch reverse-application and base-diff checks passed.
The 12-second response wait is not a strict API wall-clock bound because a
publisher already authorized under the lease must drain its synchronous
mailbox post. The exact changed C files remain uncompiled at this receipt.

Current-turn reruns: recovery-deployment suite 38/38 in normal, `-O`, and
`PYTHONOPTIMIZE=1` modes with the trusted local AVB tool;
NPU lifecycle source/model suite passed in normal and `-O`. The NPU preflight
CLI exited 2 with `bootup_ready=false` and `bootup_authorized=false`; the
public review snapshot lacks the exact config, AIE firmware artifacts, and
private/pinned lifecycle source closure. This is the intended fail-closed
result, not a reason to stage firmware or attempt BOOTUP.

Object-compile setup is in progress in the existing isolated NPU worker
context. Its first Kconfig-prepare invocation omitted `LLVM_IAS=1`, which
would have normalized ThinLTO off; the worker caught this before compiling a
candidate translation unit and will not use those normalized configs. The
worker is restoring copies from the untouched reference and rerunning prepare
with the required flag. The planned check remains limited to candidate
translation units and does not authorize deployment, a full image build,
BOOTUP, or phone operation.
Model evidence remains explicit selection only: local Codex CLI config is
Luna/xhigh, the catalog supports Luna/max, worker calls were explicitly
Luna/max, and this execution interface exposes no runtime/session attestation.

At the latest read-only phone checkpoint, WLAN passed all 13 acceptance
checks while USB SSH and the resident assistant were healthy. The native
overlay had 34,844,672 bytes free and 29,780 free inodes, while persistent
`/srv/s22` had 102,382,280,704 bytes free. The overlay upper/merged `/usr`
size discrepancy remains unresolved and no independent recovery path was
proven. No hardware change, install, cleanup, deployment, reboot, or driver
acceptance occurred.

## NPU CONFIG variant compile and review receipt — 2026-09-23 21:19 UTC

Kernel commit `40b5c72cedfb87facca7391c3efb3871497f5393` (parent `4c206702`)
is based on exact running source `4e5c5ad7d950e4de0688b5663965f2075654b2ad`.
The exported patch/test commit is `07ac389c2718e05b2087e3c5b728aba053473bf8`,
integrated as `d237dee`. It fixes BOOT_IOCTL-only waiter names leaking into
the generic POWER_CTL path, narrows the legacy `is_session_ref_exist()` helper
to mailbox versions `<8`, and scopes `hids` to its BOOT_IOCTL shutdown path.

The untouched/reference and BOOT_IOCTL=y config hash is
`a147841a53f5b10c366a759d0e83525996a0ec5d8227a103b020cf2111400f9e`; the
BOOT_IOCTL=n config hash is
`8d74d53d6a9ceba28521fc814a4da1d684a364dd0d436602bbb9c495c5eb8141` and
differs only at that symbol. Both preserve SCS, ThinLTO, CFI, MODVERSIONS, NPU
hardware-device and DSP settings. The pinned Android Clang 21.0.0 r563880c
binary hash is `af0f25ca6818aed54c1cab03dc591acd549f385b8447148326a413e3e59c22b7`;
`ld.lld` hash is
`784146955ed87545385bf5c89b3b920ca7fe3ac83e034c3e6c53783ce544adf1`.
With `ARCH=arm64 LLVM=1 LLVM_IAS=1 -j1`, the explicit targets
`npu-session.o`, `npu-protodrv.o`, and `npu-vertex.o` compiled in each
configuration. The output object hashes are recorded in the task board. The
outputs are ThinLTO LLVM bitcode; no linked kernel/module/image was produced.

Coordinator checks passed: lifecycle model/source suite and preflight
synthetic suite, each normal and `python3 -O`; exact patch byte-equivalence to
the pinned-base diff and reverse-apply also passed. Independent reviewer
`/root/npu_lifecycle_independent_review` found no correctness issue in the
conditional fixes and confirmed the source/Kconfig relationships. Reviewer's
coverage caveat: source-string guard regression is not a substitute for the
reported dual-config compile, which the reviewer did not independently rerun.
No runtime model identity was exposed.

This advances NPU evidence from source/model-tested to targeted translation
units built under both relevant option values. It does not establish a full
kernel build, firmware execution, safe NPU BOOTUP, inference, or a working
driver on the phone. No firmware was staged; no device state changed.

## Execution correction continuation — 2026-09-23 21:55 UTC

### Hardware-free host CI and Luna review

Implementation worker `/root/host_ci_runner_impl`, explicitly selected as
`gpt-6-luna/max` by the native collaboration interface, delivered commits
`5d029ffcf7a6a946e34666b0123052335b64ccd4`,
`583e553a78a223e48857a9ea5ade2e24e9137616`, and
`0b13812f8fa4a8e4374f4ea75ead20dc8dc16213` in its isolated worktree. The
patch adds a fixed allowlist runner for seven hardware-free host scripts,
policy tests, and `.github/workflows/host-regressions.yml`. The follow-up
pins the exact path sequence and rejects final, internal-ancestor and
external-ancestor symlinks. No caller-supplied test paths or discovery are
used.

Independent Luna reviewer `/root/host_ci_runner_independent_review` reviewed
`583e553` and then exact commit `0b13812` in fresh detached worktrees. The
first review found the internal-ancestor symlink gap; the final review
confirmed the new negative regression and approved the bounded WIP patch.
Both worker requests explicitly selected `gpt-6-luna/max`; the collaboration
interface exposes no runtime/session metadata, so model identity is
`explicitly_configured`, not `runtime_reported`. The current coordinator
configuration is `gpt-6-luna/xhigh`; current-turn runtime metadata and a
Max-effort coordinator selection are not exposed.

The coordinator integrated those commits as `e1af7f2`, `daeb0b5`, and
`38f5f12`. Reruns on the exact final implementation commit:

| Command | Result | Evidence limit |
|---|---|---|
| `python3 -I -B tools/hardware/test-host-regression-runner.py` | 7/7 pass | Runner policy only |
| `python3 -I -B -O tools/hardware/test-host-regression-runner.py` | 7/7 pass | Optimized-mode policy only |
| `python3 -I -B tools/hardware/run-host-regressions.py --mode both` | Exit 0; 7 normal runs, 5 optimized runs, 2 documented `-O` skips, 0 failures | Host-only models, fixtures, PTYs and synthetic HCI; no phone/controller trial |
| `python3 -m py_compile` on runner, policy test and seven allowlisted scripts | Pass | Syntax only |
| `git diff --check 7b683219..HEAD` | Pass | Patch whitespace only |

The first push-triggered GitHub Actions run, `35926456431`, completed with
conclusion `success` for branch SHA
`29bdfa5a13edf7bdd8e871d28af432711cb6d6ad`. It grants `contents: read`, disables
checkout credential persistence and uses no secrets or device step. The test
suite clears inherited device overrides/credentials and makes no IP/HTTP
requests. Workflow runner egress itself is not blocked. A separate rerun of
`test-recovery-deployment-hardening.py` with the trusted local AVB tool passed
38 tests in normal, `python3 -O`, and `PYTHONOPTIMIZE=1` modes; this remains
mocked host validation, not an operational deployment.

### Full linked NPU candidate build and fail-closed preflight

The coordinator completed a full `Image modules` link in the isolated kernel
worktree at source commit `40b5c72cedfb87facca7391c3efb3871497f5393` (parent
`4c20670269e800454a5daacb9a01856ad1a792ae`), based on running source
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`. It used a fresh output directory,
the unchanged `CONFIG_NPU_USE_BOOT_IOCTL=y` config with SHA-256
`a147841a53f5b10c366a759d0e83525996a0ec5d8227a103b020cf2111400f9e`, Android
Clang 21.0.0 r563880c, `ARCH=arm64 LLVM=1 LLVM_IAS=1
CROSS_COMPILE=aarch64-linux-gnu- -j2`; `olddefconfig` reported no changes.
The build exited 0, linked `vmlinux`, an ARM64 Image and 329 modules, release
`5.10.260-g40b5c72cedfb`. Hashes: `vmlinux`
`dce21b81d73d8f8e4467c6be50b4a0e9473827f8ec3f985cfae5279a0a1a4045`, Image
`ae089169bfabf8459162ca1df78704f27d04d303e71b08520547e4cf13b8fe52`, and
`drivers/vision/npu.ko`
`0fbe47b939971be05bb0e3696122d192d12400c21bb75eabe0280230b54343f0`.
Build artifacts remain outside the public repository; none were packaged or
deployed.

The exact-source/config/local-firmware preflight exited 2. Artifact and
source-route checks passed, but `bootup_ready=false`, `bootup_authorized=false`,
device access/staging false. Remaining gates include firmware boot/shutdown,
live probe, independent rescue and explicit owner authorization. No NPU
BOOTUP or firmware staging was attempted. This is a successful host kernel
build, not a working NPU driver.

### Current bounded read-only device evidence

The latest USB SSH read-only snapshot reports kernel
`5.10.260-g4e5c5ad7d950`, PID 1 `native-guardian`, 325 loaded modules and
resident assistant HTTP 200. The most recent Wi-Fi acceptance remains 13/13;
it is not a new acceptance from this checkpoint. USB SSH depends on the
running kernel and does not establish independent rescue. Current boot mode
is left unclaimed because `bootmode=2` and the tail of `/proc/boot_reset` do
not yield an unambiguous current BORE mode.

The actual root overlay reports 610,861,056 total bytes, 563,433,472 used,
34,844,672 available (94% used), and 8,620/38,400 inodes used. Its mount
metadata names `/cache/s22-linux/upper`, which is outside the visible PID 1/
SSH namespace; visible-root `du` accounts for only about 4.7 MiB. Upper-layer
consumers therefore remain unidentified. Persistent `/srv/s22` has
102,382,280,704 bytes and 1,652,508 inodes free. No cleanup, install, package
operation, staging, deployment, reboot, or driver trial occurred; unrelated
existing host processes were left untouched.

The original dirty checkout remains untouched at `fb60a2c1...`, ahead 44 and
behind 57 against fetched `origin/master`. Commits
`5928e980ea32355c43ca5240eb41c8aa862533f3` and
`295b8696b20ef2c342df1ea6031ac41d9bc52fe8` were inspected, remain unchanged
on durable branch `codex/s22-luna-coordinator-20260923`, and are ancestors of
the WIP branch. Only intended sanitized patches and receipts were selected;
unrelated local history was not merged or cherry-picked wholesale.

No physical or software driver functionality changed. Before any next device
experiment, independently reachable rescue must be demonstrated from the
actual recovery host without the running kernel, candidate-specific
rollback/readback and the exact authorization must be ready. Root-overlay
staging additionally requires resolving the upper-layer accounting and
measuring destination-specific free space. NPU BOOTUP remains denied until
the runtime lifecycle gates pass; the host build does not relax them.

### Final pre-publication audit

The fetched public baseline is `20605dbe623e0909cf219c3cae9ef7bb597b15a6`.
The selected review branch has 43 commits beyond it across 29 changed tracked
paths. No reachable blob exceeds 20 MiB; no firmware package, image, model
weight, credential, host key, or private trace was found in newly reachable
paths. The large unrelated local commits
`1ccf3395303d62e2c31aff8bb88d46155e68dcb6` and
`a52151f24eb2c3ae4fdd750020cdaeb9c9468c1f` are not ancestors. The preserved
commits `5928e980ea32355c43ca5240eb41c8aa862533f3` and
`295b8696b20ef2c342df1ea6031ac41d9bc52fe8` are ancestors as intended.

Whole-branch `git diff --check` reports 416 whitespace diagnostics confined
to `tools/hardware/npu-session-lifecycle-fix.patch`; that exported patch keeps
the exact formatting from the downstream kernel source. The remaining branch
diff check is clean. The patch reverse-applies and byte-compares exactly to
the kernel diff from source `4e5c5ad7d950e4de0688b5663965f2075654b2ad` to
candidate `40b5c72cedfb87facca7391c3efb3871497f5393`. This publication audit
does not promote or deploy the candidate.
