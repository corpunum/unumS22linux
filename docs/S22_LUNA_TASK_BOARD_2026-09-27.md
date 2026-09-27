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

## Executed checkpoint — 23:05 UTC

The initial count above is historical. Audio was ready first, so the coordinator
ran it before Bluetooth, preserving serialization. Independent Luna review by
`/root/driver_review_20260926` approved the shared guard (`0f366f3`, integrated
as `6816417`) and the corrected audio runner (`11c5270` plus `bb6a298`, integrated
through `3a59e6f`). Guard tests passed 10, audio route tests 39, cleanup tests 6,
and snapshot tests 21 in both normal and optimized Python. An ambiguous remote
trace-stage result now stays UNKNOWN; it is not mislabeled as no mutation.

`audio-zero-20260927` ran once through an independent user systemd unit. It
failed before PCM preparation: `aplay` could not open the missing
`/dev/snd/pcmC0D2p` (`ENOENT`). Both route selectors returned to zero, amplifiers
stayed off, PCM was closed, and the same native boot/model remained healthy.
The durable marker records `failed-cleanup-confirmed`; its identity is consumed.
This result does **not** show a DMA firmware failure or working playback.
The kernel class and char-device sysfs links agree on `116:3`; the matching
device node is absent in both shell and guardian views. The audio worker is
implementing exact-node provisioning/preflight with tests, not a kernel rebuild.

Camera implementation `0fd115e` (integrated `9408128`) and the live-name fix
`5befae6` received independent Luna review from `/root/audio_next_20260926`.
Camera tests passed 4/4 normally and optimized; CI now includes that suite.
Coordinator-only passive collection found 56 V4L2 nodes, no media nodes, and
sensor leaders bound to `exynos-is-sensor`/`fimc_is`. All seven fixed camera
firmware/setfile names were absent in the four checked namespace views. The
coordinator recovered those seven files privately from the existing verified,
read-only vendor image using the existing F2FS recovery tool. No camera was
opened/powered and no recovered file was staged on the phone. The native
guardian firmware directory has about 2.36 GB available, distinct from the
nearly-full shell overlay; persistent `/srv/s22` has about 102 GB available.

Bluetooth adapter `4491ee8` is integrated as `2cee3e9` but remains awaiting the
final independent review correction for kernel diagnostic classification.
Its local provenance and live read-only candidate/storage prerequisites passed.
No Bluetooth registration has been attempted in this wave yet.

The rig's user transient service mechanism was tested successfully and then
used for audio. A temporary sleep-inhibitor request was denied; it was not
bypassed. Existing AC automatic sleep is disabled (`nothing`), the rig is on
AC, and power policy was not changed. Independent hardware rescue is still
unproven. No new partition write or reboot was performed.

Sanitized live facts are recorded in
[`s22-hardware-continuation-20260927.json`](../evidence/s22-hardware-continuation-20260927.json).
Private firmware, complete traces and network identifiers are not published.

## Executed checkpoint — 23:45 UTC

Independent reviewer `/root/driver_review_20260926` approved the BT capture-window
corrections through `6a6bcda`, audio node repair `b77bce2` (integrated `983cbec`),
and BT metadata-contract correction `0319385`. Actual normal/optimized adapter
coverage increased to 30 tests; audio assessment coverage increased to 47 tests.
The integrated fixed suite at `983cbec` passed 20 normal and 17 optimized scripts
with three documented optimization skips. This is host-only evidence.

The first BT invocation (`bt-hci-registration-20260926`) staged its pinned binary
but refused before any controller operation: the board adapter expected printed
metadata success whereas the included actual C main returns success silently.
An extracted-C regression reproduced that contract; the corrected adapter
selects it explicitly and rejects nonzero status or unexpected output. The old
marker/artifacts are preserved and explicitly reconciled as staging-only failure,
not no mutation. A fresh identity was used after review, not a blind retry.

`audio-zero-node-20260927` created only the exact missing `116:3` node as
root:root/0600. It now reaches PCM PREPARE and seven accepted write ioctls.
Nineteen synchronized RUNNING samples show zero hw_ptr and RDMA position advance,
despite UAIF1 DPCM start and nonzero clock-framework enable counts. The child
hit its ten-second deadline and was reaped; both selectors restored to zero,
amplifiers stayed off, PCM closed, and the same native boot remained healthy.
The journal is terminal `failed-cleanup-confirmed`. No audible playback claim.

`bt-hci-registration-20260927` then ran once, serialized after audio. Firmware
transfer/reset/readback succeeded and `HCIUARTGETDEVICE` returned hci0. The
kernel's first command (1003) was queued, but the IBS wake handshake received
no acknowledgement; initialization failed. The single detach ioctl returned
zero after 9.892650 seconds, UART settings were restored, power-off ioctl
returned zero, and independent post-checks found no controller/device FDs with
WLAN's vote preserved. Fresh full RECOVERY hash/build/boot/native/model/network
checks passed; the retained kernel window has no new fatal/hung indicators.
The failed operation was explicitly reconciled with private receipt and trace
evidence; no automatic retry. Registration is verified, initialization/pairing/
Bluetooth audio are not. No partition writes, reboots, or raw-HCI repeat.

### Subsequent actual worker assignments

| Worker (same explicit Luna/Max selection) | Isolated worktree | Deliverable/state |
| --- | --- | --- |
| `/root/bt_next_20260926` | `s22-workers/npu-ownership-20260927` | `59791b9` partial hardware-reference unwind and extracted-C failure injection; integrated `1c41f09`, independent review pending, no deployment |
| `/root/bt_next_20260926` | `s22-workers/bt-ibs-20260927` | Diagnose actual private wake-handshake trace and implement a source-backed transport correction; host only |
| `/root/audio_next_20260926` | `s22-workers/camera-trial-20260927` | Camera first-resource LDO error unwind and executable tests; no camera power/capture |
| `/root/driver_review_20260926` | `s22-workers/camera-20260927` | Independent reviews plus analysis of the new audio receipt; host only |

Camera source review found partial PHY-LDO enables are not reversed on failure,
with resource counts still incremented; failed open leaves no fd to close.
Unchecked sensor runtime-PM failure is a separate remaining issue. The next
camera work is a targeted source repair/test, not an unsafe QUERYCAP claim.
NPU BOOTUP remains refused; host ownership tests are not hardware evidence.

Full integrated regressions at `697e165` passed: 20 normal scripts, 17 optimized
scripts, three explicit optimized skips, zero failures. This includes the new
NPU C harness but is not independent approval of that patch. The preceding run
at `a89b16b` exposed one stale BT fixture still naming the consumed old trial;
worker `80522dc` (integrated `697e165`) fixed the fixture without relaxing the
separate consumed-identity rejection. The WIP branch remains unmerged; NPU and
camera kernel candidates are not deployed and retain their review/runtime gates.

Independent review then **rejected** the new NPU unwind as currently written:
failed first init can invoke a generic final callback that disables STM before
this acquisition enabled it. The reference-only harness missed that callback
side effect. `1c41f09` is preserved as rejected WIP, not accepted code. Worker
`/root/driver_review_20260926` is preparing a narrow correction/removal of the
unsafe candidate hunk in an isolated worktree, with independent review to follow.
No NPU deployment or BOOTUP occurred. Passing host tests did not override review.

## Source corrections and build — 00:12 UTC

The rejected NPU addition was removed from the active candidate by worker
`db74bb7`, integrated `febb02f`; history and an honest baseline reproducer remain.
The underlying ignored-error/shared-STM ownership bug is **unfixed**. No BOOTUP
authorization/readiness was added. The source-fixture/reproducer is not actual
kernel callback execution or proof of an unwind.

Camera worker `afe3bd1` (integrated `cbac569`) added only first-resource PHY-LDO
failure unwind: reverse earlier successful votes, preserve the original error,
avoid false resource-count increments and release the wake reference. Four
tests pass normally/optimized; the patch applies to pinned vendor source.
Independent reviewer `/root/bt_next_20260926` found no scoped unwind issue.
Failed-provider physical state and unchecked runtime-PM remain unresolved;
there is no camera build, deployment, open or capture acceptance. CI includes
the extracted-C camera regressions (`34cbc88`).

BT worker `8cd4246`/`56cf572` (integrated `a5f1f65`/`63372b5`) corrected the
observed transport mismatch: this embedded runtime NVM has IBS disabled, so
the profile selects plain H4. Generic operation stays IBS; malformed profile
metadata refuses attachment. `/root/audio_next_20260926` independently approved
the source and all 24 normal/optimized bridge tests, including opcode 1003.
The small userspace probe was rebuilt with the unchanged verified toolchain and
private inputs; identity is in `BT_IBS_PROFILE_2026-09-27.md`. No kernel rebuild.

Coordinator adapter `217ee89` pins that exact new ELF and a fresh one-shot ID
`bt-hci-plain-h4-20260927`, with source-derived cleanup and nonzero command/event
progress checks. Its 31 tests pass in both modes. Independent adapter review is
pending; no plain-H4 trial has run at this checkpoint. Full integrated host
suite: 21 normal, 18 optimized, three documented optimized skips, zero failures.

## Executed checkpoint — plain-H4 transport passes

Independent adapter review by `/root/driver_review_20260926` approved `217ee89`
and its pinned ELF before execution. At 00:17 UTC, the coordinator ran
`bt-hci-plain-h4-20260927` once through the durable host service. It finished in
25.121 seconds with return zero: hci0 registration, 41 commands and 41 events,
no IBS wake/ack frames, no pending command, detach zero and
`baud_probe_result=0`. Full candidate RECOVERY/build identity, unchanged boot,
native/Pi/model/network/power checks passed. The retained kernel-log boundary
was present with no new fatal/hung-task indicators. Postflight found no remaining
controller or device FDs, and WLAN's vote remained intact. The global guard
records complete/success; the independent reviewer also executed the host-only
validator against the persisted receipt successfully.

This is actual bounded HCI transport evidence, not permanent Bluetooth service,
pairing, Bluetooth audio, RF range or sustained acceptance. The controller was
deliberately detached and powered down afterward. No new RECOVERY write,
reboot, NPU BOOTUP or repeat of the earlier raw-HCI socket test occurred.
Independent hardware rescue remains unproven. All earlier failed/reconciled
operation markers and private artifacts remain preserved.

Remote branch `24e1aa2c4f4948b6866e1633e675faeba25d736b` was verified;
GitHub Actions run `36281788663` passed. The source suite remains 21 normal and
18 optimized scripts, with three documented optimized skips and no failures.
The original dirty checkout and both local/remote master are unchanged.

### Next host-only Luna wave

| Worker (same explicit Luna/Max configuration) | Separate worktree | Assignment |
| --- | --- | --- |
| `/root/bt_next_20260926` | `s22-workers/bt-ibs-20260927` | Independently interpret persisted command/event evidence and delimit next Bluetooth acceptance; no second device trial |
| `/root/audio_next_20260926` | `s22-workers/camera-pm-20260927` | Repair unchecked camera runtime-PM failure handling with executable extracted-C regressions |
| `/root/driver_review_20260926` | `s22-workers/npu-callback-20260927` | Investigate/repair ownership at the actual failed first-init callback boundary, preserving shared STM state and BOOTUP refusal |

The NPU removal `febb02f` also received independent review from
`/root/bt_next_20260926`: the unsafe outer-unwind hunk is absent, and baseline
failure reproducers are not advertised as a working fix. No worker may access
the phone, mutate shared receipts or deploy a kernel. New implementations need
independent review before integration or any separately reviewed device step.

## Reviewed host follow-up

BT worker `71e45c8` (integrated `c4bfca8`) reconciled the private UART capture
against source transfer phases. All 41 attached-phase command completions have
zero status. The 806 patch segments intentionally use one final mode-3 ACK;
NVM's 29 segments each receive an ACK. The separate pre-attach baud command
`0xfc48` returned `0x01`, whose explanation remains unresolved despite later
communication succeeding. It is not silently treated as harmless. The full
sanitized accounting is in `research/BT_COMMAND_RECEIPTS_2026-09-27.md`.

Camera worker `/root/audio_next_20260926` delivered `a34ee68` plus `4302e45`,
integrated as `dfdcb2d` / `da4152c`. The additional ordered patch checks runtime
PM acquisition, balances a failed get, avoids false power-bit/count updates,
and unwinds first-resource votes without tearing down other active users.
Coordinator review caught a dependency on the local vendor checkout in the C
test; the follow-up provides a pinned GPL fixture so CI executes the C cases
even without that checkout. Source equality/application checks remain separate
and explicitly skipped when unavailable. Runtime-PM and earlier LDO suites each
pass 4/4 normal and optimized. Absent-source simulation runs the new suite with
two explicit provenance skips and two executable tests. Independent Luna
review by `/root/driver_review_20260926` approved the ordered patches, fixture
follow-up and exact CI allowlist additions. No kernel build or camera operation;
provider/clock physical-state uncertainty remains explicit.

NPU worker `/root/driver_review_20260926` delivered `16f18dd` plus `0dcc1e8`,
integrated as `d5d4825` / `d050d93`. Exact pinned C callback bodies now reproduce
ignored failed acquisition, shared-STM unsigned underflow and concurrent get
success before the first callback fails, using documented host shims. These
are bug reproductions, not an NPU fix. Independent Luna reviewer
`/root/bt_next_20260926` matched fixtures byte-for-byte to pinned source and
approved the final correction. Review first reproduced an inaccurate green
intermediate gate when required function bodies were absent; the follow-up
keeps such gaps unknown and requires explicit known-false gap values. Normal
and optimized lifecycle/preflight suites pass, including the exact formerly
failing scenario. BOOTUP remains refused; publication-drain liveness and
callback/STM ownership are unresolved. The active kernel patch adds no
hw-device callback fix.

A later coordinator read-only phone check passed at uptime 205647.3 seconds:
same expected GNU build ID, native guardian, 325 modules, desktop Pi and browser
ready, model healthy/idle, full battery at 27.3 C, maximum thermal reading 41 C.
The available kernel ring had no fault indicators; full-boot log coverage was
not claimed. Dedicated browser Pi remains intentionally on demand. No additional
device mutation accompanied this host wave. First and failed trial evidence,
original dirty checkout, rollback artifacts and master remain preserved.

Integrated verification passed: runner-policy suite 7/7; fixed host suite
22 normal scripts and 19 optimized scripts, three documented optimized skips,
zero failures. The deployment messages in these logs are fake-filesystem/SSH
fixtures, not new phone writes or reboots. A final output-only wording change
clarifies that extracted C ran with host shims, not a full kernel; the NPU
lifecycle suite was rerun in both modes afterward. Hosted provenance checks
that lack the pinned vendor checkout remain explicit skips; portable camera
and NPU C fixtures still execute. No private firmware, images, weights,
credentials or raw traces are part of the published changes.

## Owner continuation — 06:09 UTC

Fetch reconciled `3acf43b6714d88bd78c32490a734530be31896a9` with the remote
review branch (zero ahead/behind); the integration worktree was clean. Original
local master and its five tracked edits remain untouched. No active kernel
build or device trial was found. The resident Pi loop and native auto-ACK host
services remain running and unmodified. All five prior guard markers are
terminal complete/reconciled; the successful plain-H4 identity stays consumed.

Fresh coordinator-only read-only checks passed: exact RECOVERY SHA
`42da267f3dd9f94f30f62a95fb2ac13f91d4cf98f1a2307f7cc14e45d9c49be5`, expected
GNU build ID, native guardian, 325 modules, desktop/browser/model readiness,
uptime 225977.24 seconds, full battery at 26.9 C and maximum thermal 39 C.
The available kernel ring had no fault indicators; full-boot coverage is not
claimed. Independent rescue remains unproven. No flash, reboot or hardware
activation accompanies this baseline collection.

### Actual new Luna workers

All three launches explicitly selected `gpt-6-luna` and `max` reasoning through
the supported subagent interface. Selection was accepted without a known
override; this is configuration evidence, not an independent backend identity
attestation. Each starts from the reconciled published head in a new worktree.

| Actual worker ID | Worktree | Narrow implementation ownership |
| --- | --- | --- |
| `/root/bt_baud_20260927` | `s22-workers/bt-baud-20260927` | Exact baud-response interpretation, new classifier/tests and sanitized source note; no changes to consumed probe/profile |
| `/root/audio_ipc_20260927` | `s22-workers/audio-ipc-20260927` | Source-backed trigger/IPC diagnostic or narrow fix with executable tests; no repeated old zero-stream trial |
| `/root/camera_build_20260927` | `s22-workers/camera-build-20260927` | Real isolated ARM64 build of reviewed camera changes, provenance/compatibility and smallest packaging seam |

Only the camera worker may start a heavy host build, narrowed to `nice -n10`
and one job after reporting the exact identity/command. No worker may access
the phone or change its state. Camera is modular: the artifact is `fimc-is.ko`
and its loaded module name is `fimc_is`; do not rebuild an unchanged kernel Image merely to produce a
receipt or misuse the kernel-only RECOVERY packager. No NPU change is bundled.
Independent review remains required before device use or publication of fixes.

### Source reconciliation and independent review

Bluetooth worker `69a91c6` is integrated locally as `4226738`: an offline FC48
frame parser, nine normal/optimized regressions, and a bounded source note.
The captured return byte remains unresolved; the older Rome success value
belongs to a different vendor event. No new Bluetooth action is authorized
by the parser's output.

Independent reviewer `/root/review_wave2_20260927` was actually launched with
explicit `gpt-6-luna` / `max` selection, in
`s22-workers/review-wave2-20260927`. This worker owns no shared implementation
files and has no phone access; it reviews the Bluetooth, audio and camera
deliverables as they become available.

Coordinator read-only audio inspection found both `ABOX SIFS0 OUT Switch` and
`ABOX UAIF1 Switch` on. The two route selectors are zero as expected after the
previous trial's cleanup; this idle state is not the prior RUNNING state.
Amplifiers stayed off and PCM remained closed. Live graph nodes route SIFS0
through SIFS0 PGA and STMIX. The actual config selects ABOX `0x40001` and
`abox_cmpnt.c`, not the earlier cited `abox_cmpnt_3.c`; the audio worker is
correcting the source model. No controls were written, tracepoints enabled,
PCM opened, or audio trial repeated.

The audio implementation `7f15698` and sanitized-count follow-up `d082f88`
are integrated as `1c30912` and `1943c56`. Normal and optimized tests execute
five cases each plus one explicit optional source skip; both source-enabled
modes pass all six. Independent Luna review reproduced the saved receipt's
19 RUNNING samples and two RDMA2-prefixed trigger entries, without treating
missing debug messages as failed firmware delivery.

A second coordinator read-only audit found `abox-mem` enabled at log level 2
and `abox-file` disabled at level 2. The source's debug messages use level 5
and are filtered by that current policy. No log payload was opened or consumed,
no verbosity changed and no flush requested. Current settings do not prove
historical coverage. Existing logging must be reviewed before proposing a new
instrumentation kernel or another stream attempt.

Camera's first module artifact was rejected despite build exit zero because
its `__versions` section was empty. The normal in-tree single-module target,
using exact original symbol inputs with only old camera exports excluded,
then produced a module with 378 version records. Independent review matched
all 378 CRCs (323 vmlinux, 55 other-module exports), including `module_layout`.
The existing 75 camera exports, alias set and dependency set remain unchanged.
This is build/loader evidence only, not a loaded camera driver or capture test.

An attempted reactivation of the completed Bluetooth worker for packaging was
rejected by the runtime with `agent thread limit reached`; no such packaging
worker ran. The existing camera worker retains that follow-on host-only task.
The unused `s22-workers/camera-package-20260927` worktree remains preserved;
no active worker directory was moved or overwritten.

### Reviewed host checkpoint — 06:57 UTC

Stage-1 camera worker commit `11dac1a` is integrated as `facd342`.
`/root/review_wave2_20260927` approved its helper, tests, source/build receipt
and compatibility claims after independently reproducing all three set hashes,
378 imported CRC matches and unchanged 75 exports. The five symvers-helper
tests pass normally, under `-O` and with `PYTHONOPTIMIZE=1`. The first incomplete
artifact stays preserved and rejected. No module was loaded or image deployed.

The same reviewer approved Bluetooth `69a91c6` after the coordinator corrected
the older receipt note's overconfident status terminology, and approved audio
`7f15698` / `d082f88` after the source/reader-semantics follow-up. These are
separate host-evidence approvals, not hardware acceptance.

The integrated fixed suite passed 25 normal scripts and 22 optimized scripts,
with three documented optimized skips and zero failures; runner-policy tests
passed 7/7. Fetch still found no new remote review-branch work. Remote master
remains `20605dbe623e0909cf219c3cae9ef7bb597b15a6`; original local master and
all five tracked modifications remain untouched. Only sanitized code, source
notes and aggregate evidence are selected for publication. Camera packaging
continues separately; no image, firmware, module binary or raw trace is added.

Published checkpoint `6fb59ab6668e768ae3c09f0b28df3dda426bf090` was verified
on the remote review branch. [Hosted regression run 36301628572](https://github.com/corpunum/unumS22linux/actions/runs/36301628572)
completed successfully for that exact SHA. This hosted result does not add
device or camera acceptance; master remains unchanged.

### Completed camera host package — 07:32 UTC

The existing explicitly selected Luna/Max camera worker
`/root/camera_build_20260927` continued in its same isolated worktree. It
delivered the packager/strip provenance in `0391048`, then receipt-durability
fix `1756e84`. Independent Luna reviewer `/root/review_wave2_20260927` first
found a success manifest surviving final fsync failure; the follow-up removes
only the exact newly created receipt inode and preserves payloads and unrelated
files. These commits are integrated as `c5262c8` and `9df1885`.

The coordinator's first real host-only packaging attempt exited 2: the AVB
descriptor looked for `recovery.img`, whereas the temporary image was named
`candidate.recovery.img`. It created no durable output or success receipt.
The worker corrected the shared path selector in `36bd9f2` and added an
unconditional filename regression in `3012c7a`, integrated as `34e05e5` and
`3c3f16c`. Independent review checked the pinned AVB implementation and ran
the synthetic public-tool fixture: wrong basename refuses, correct basename
verifies, corrupted payload refuses. All 16 focused tests pass normally,
under `-O`, and with `PYTHONOPTIMIZE=1`. The public-tool case is an explicit
skip if unavailable; the basename guard still runs. No private fixture is
published. The three CI allowlist additions were independently reviewed.

After that review, the coordinator reran the same host packaging command
successfully. The saved private image is
`builds/camera-module-recovery-20260927/recovery.img`, SHA-256
`b10412715756da3cc8ee221368b49f179cc0c64ab7bd2802976480905e6d8d2f`,
100,663,296 bytes. The exact current HCI kernel, DTB and recovery DTBO are
preserved; only `lib/modules/fimc-is.ko` changes among 963 CPIO records.
The new module has 378 matching imported CRCs and unchanged aliases/dependencies
and 75 exports. AVB footer/hash verification passed again on the saved output.
Both original HCI and older native rollback images were independently rehashed
and remain unchanged. Full artifact details/limits are in the
[sanitized receipt](../evidence/s22-camera-package-20260927.json).

The final integrated host suite passed 26 normal and 23 optimized scripts,
with three documented optimized skips and zero failures; runner policy passed
7/7. This includes the real AVB synthetic fixture locally, not phone evidence.
No camera open, firmware staging, live module replacement, flash, reboot or
NPU BOOTUP occurred in this continuation. Latest coordinator read-only
postflight still identified HCI `42da267f…` / GNU `b2dda820…`, 325 modules,
native/Pi/model/network/power healthy at uptime 231149.2 seconds in a fresh
post-package read-only check; the model remained idle and the available kernel
ring had no hung-task warnings. Full-boot coverage is not claimed.
Dedicated browser Pi remains on demand; independent rescue is unproven.

Next: implement/review an exact camera-specific deployment and rollback
profile against the current HCI image, preserving consumed prior trial IDs.
Only subsequent actual boot/module-identity checks and resource-safe camera
operations can add hardware acceptance. Audio still needs a reviewed logging
capture; current debug filtering is not evidence of IPC failure. NPU ownership
and publication-drain liveness remain unresolved with BOOTUP refused. Bluetooth
retains its earlier bounded initialization pass, not pairing/audio acceptance.

Independent saved-artifact review subsequently passed: Luna reviewer
`/root/review_wave2_20260927` rehashed the image, module, private manifest,
current HCI baseline and older native rollback; ran pinned AVB verification;
and independently unpacked/compared all 963 ordered CPIO records and unchanged
boot payloads. It confirmed only the intended module record changed, with
metadata and module lookup files preserved. The sanitized receipt/privacy
review passed. This is separate from code review and still not a phone boot,
camera load/open/capture or physical regulator/clock acceptance.

## Camera deployment preparation continuation — 09:13 UTC

Owner said to proceed after the camera-package checkpoint. Fetch reconciled
`b623fbe479cbee772e1f63c4fc602b77a00775d5` with the review branch, with no
newer upstream work. Its hosted regression run `36303780196` succeeded.
The original checkout's five tracked modifications and all older worktrees
remain preserved; no active build or hardware experiment was found. Native
auto-ACK and resident Pi-loop services remain running and unchanged. Old
failed transient unit states correspond to retained historical trials; all
five shared device-guard markers are complete or explicitly reconciled.

Three existing workers with previously accepted explicit `gpt-6-luna` / `max`
selection were actually resumed. No new model selection or backend identity
attestation is claimed. They use new isolated worktrees at `b623fbe` and have
no phone access:

| Actual worker | Worktree | Narrow assignment |
| --- | --- | --- |
| `/root/camera_build_20260927` | `s22-workers/camera-deploy-20260927` | Exact camera forward/reverse deployment adapter and executable regressions; no reboot or kernel rebuild |
| `/root/audio_ipc_20260927` | `s22-workers/audio-log-20260927` | Host logging-coverage diagnostic, source/reader-ownership audit and tests; no payload consumption or verbosity change |
| `/root/review_wave2_20260927` | `s22-workers/camera-deploy-review-20260927` | Independent code/safety review of both implementations |

Fresh coordinator-only read-only baseline passed at uptime 236997.88 seconds:
full RECOVERY `42da267f…`, kernel GNU `b2dda820…`, 325 modules, native desktop,
browser/model/network/power readiness and idle model. The loaded camera module
is still the old `8286071582b5efedff0e0c6169ba1a23018fb814`, not the new
candidate module. The available kernel ring had no hung-task warnings;
full-boot coverage is not claimed. Host image `b1041271…`, private manifest
`dc697023…` and HCI rollback `42da267f…` rehashed exactly.

Actual SSH destination `/srv/s22` is writable ext4 on device 259:20 with
101772271616 free bytes and 1652485 free inodes. The root overlay has only
34844672 free bytes. A first combined read-only query encountered ENOENT for
`/proc/1/root/srv/s22`; a per-path query reconciled this without any filesystem
change. Guardian and shell have the same mount-namespace inode but different
root views. The missing guardian-relative path is not absence of the real SSH
staging mount and must not become an automatic blocker for `/srv/s22`.

This wave prepares the exact camera operation; it does not reuse the consumed
HCI unattended authorization. Independent hardware rescue is still unproven.
No camera-specific unattended-risk acceptance is recorded merely from the
broad continuation, and no flash/reboot/camera activation has occurred.

The camera adapter was independently approved at worker commits
`3cabe1546e5a920ddd9bad51bc190ea9af495d25` and
`c73bf1b4b4bd3e3d350eecae9bc207f69ed7eb0a`, integrated as `1c1550c` and
`b4243e4`. Its 16 executable tests passed normally, with `-O`, and with a
genuine `PYTHONOPTIMIZE=1` run (without `-I`, which ignores that variable).
The coordinator also reran the integrated 16-test suite and both default
host-only plans against the existing private artifacts. Forward is exactly
`42da267f…` to `b1041271…`; reverse restores `42da267f…`. Neither plan
contacted the phone. Review record `872717f4e499638f9f4faf223c69bf0c60aaad9a`
was integrated as `4c9bac6`; the reviewer's first manually copied full hash
was invalid and was corrected against actual Git output before integration.

The review verified that the unchanged CPIO `modules.load` explicitly lists
`fimc-is`. A future camera observer must check the already-loaded module's
GNU build ID, not assume absence is expected standby or infer module identity
from the unchanged kernel release/build ID. No forced module load or camera
node open is part of this deployment adapter.

A second read-only coordinator baseline passed at uptime 237995.45 seconds
and was saved in a private host preparation journal with its actual boot ID.
It is not a flash receipt, operation marker, authorization, or fresh gate for
a later write. The installed native reboot helper and restart2 binary match
the preserved reviewed host artifacts; they were hashed, not executed.
Installed host help confirms `systemd-run --user --unit --no-block` and
`systemd-inhibit --what=sleep:idle --mode=block` are available for a future
durable observer. No transient camera service or inhibitor was started.

Audio implementation `f8b756f`, ordered flag parser `d314540`, and naming
clarification `1348f05` were independently approved at exact final worker
HEAD `1348f05ad411e125e900aee4e45aeb1a1af0b31f`, then integrated as
`2fd886b`, `6a25dbc`, and `d265670`. The 12-test suite passed normally,
with `-O`, and with genuine `PYTHONOPTIMIZE=1`, including the retained source
and O-tree checks. Coordinator independently reran all 12 source/build tests.
Current Memlogger filtering is established from saved metadata; direct compiler
flags are parsed in order, but effective preprocessing and historical marker
coverage remain unproven. The tool never consumes shared Memlogger buffers or
changes policy. This is not an audio DMA repair or sound acceptance.

The integrated initial adapter/audio wave passed 28 normal and 25 optimized
allowlisted scripts, with three documented optimized-script skips and zero
failures. Runner policy passed 7/7. Optional source fixtures are tested
separately; three deployment AVB-specific tests explicitly skipped because
the trusted AVB tool was absent from this integration worktree. The retained
image verification from the preceding package wave remains separate evidence.
See [host receipts](../evidence/s22-camera-adapter-host-20260927.json).

Camera worker continues the minimal boot-bound flash receipt and separate
camera-only reboot observer; those forthcoming changes are not covered by
the initial adapter approval or these counts. They must undergo independent
review before any live use. No device mutation has occurred in this wave.

Published review HEAD `9289dc66d0a54d3db1d15448de60e15319b53e75` was verified
against `git ls-remote`. Hosted run `36310517049` succeeded at that exact SHA.
Remote master stayed `20605dbe623e0909cf219c3cae9ef7bb597b15a6`.

To overlap implementation and independent review, the completed audio worker
`/root/audio_ipc_20260927` was resumed for a second narrow assignment in a
new isolated worktree `s22-workers/camera-observer-20260927` at `9289dc6`.
It owns only the new camera reboot observer, its tests and its separate note.
`/root/camera_build_20260927` retains deployment adapter/test/doc ownership
and is finishing actual boot-bound write receipts. The reviewer remains
independent of both. Existing audio and all older worktrees remain intact.

Boot-binding worker commit `b45a2dc96aeacd994f7e869f2de6f212a62900fe`
passed independent review and 22/22 focused tests in all three Python modes,
and was integrated as `0f69033`. Missing, truncated or malformed procfs
evidence now fails closed. The actual boot-ID prelude is executed in tests;
changed post-write boot IDs retain the raw successful readback receipt,
omit a bound-success receipt and leave the operation unresolved.

After separate read-only source review, the coordinator executed only that
reader (not the stage/flash entrypoint) on the current phone. It validated
the actual full `42da267f…` image on `/dev/sda16`, block 259:0, 100663296
bytes/196608 sectors, PARTNAME=recovery and unmounted. Kernel GNU `b2dda820…`
and the already-loaded old camera module GNU `82860715…` matched, with the
same actual boot ID as the earlier private preparation journal. Full private
identity was saved on the rig; the sanitized result is in the preparation
receipt. This adds live validation of the diagnostic reader only, not
candidate installation, camera operation, or independent rescue.

The small follow-up `eb211f299d77a2a5f88bb6d40d98a085e0e1f42e` was
independently reviewed and integrated as `b06bcf9`: only the read-only camera
identity command gains Python `-I -B`, with an exact argv regression; shared
stage/flash execution is unchanged. All 22 focused cases passed in normal,
`-O`, and genuine `PYTHONOPTIMIZE=1` modes. Host `loginctl` also reported the
existing user manager active with `Linger=yes`; no service setting changed.
The new reboot observer is still under separate implementation/review and
has not been executed or approved for live use.

Host-service reconciliation was read-only. Existing auto-ACK still checks the
native guardian and writes its volatile readiness marker; it does not flash,
reboot or reset USB. The existing Pi source-research controller is active but
parked at round 18 in `needs_controller_review`, with no continuation file.
Its inspected code stages source proposals for review, not kernel deployment.
Neither service was stopped, restarted, reconfigured, or sent a model task.
These services are not independent hardware rescue.

At 10:19 UTC, the coordinator tested only the rig's installed persistent-launch
mechanism: a distinct transient user unit with `Restart=no` ran
`systemd-inhibit --what=sleep:idle --mode=block` around `/usr/bin/true`.
It exited zero in 42 ms and was collected. The user manager reports
`Linger=yes`. This proves present host launch/inhibitor permission, not
sustained observation, a phone reboot, or independent rescue. No phone
command was included and no existing service configuration changed.

### Completed observer host integration — 10:46 UTC

The earlier under-review status is superseded for **host integration only**.
Luna/Max worker `/root/audio_ipc_20260927` delivered the three-file observer
commit `931866f62176e1989774a80b0ca23e7ecbee69d9`, integrated as `1618038`.
Independent Luna reviewer `/root/review_wave2_20260927` approved that exact
freeze and recorded review in `57c3d43ca1317f4151e41711315b19c79316b094`,
integrated as `efd6c40`. Both independently passed 21/21 focused tests in
normal Python, `-O`, and genuine `PYTHONOPTIMIZE=1` without `-I`.
The coordinator repeated 21 normal cases and replayed the earlier saved
actual baseline through the integrated native/power/readiness checks.
`/root/camera_build_20260927` independently checked procedure/target mapping;
its corrections to receipt privacy and inside-unit inhibitor placement were
incorporated. All three workers used their existing accepted explicit
`gpt-6-luna`/`max` selection; no extra backend identity attestation is claimed.

The observer binds the exact terminal flash receipt and current boot before
one native RECOVERY request. An immediate same-boot/native/helper prelude
executes only the existing absolute helper. ACK/disconnect never triggers a
retry or clears the global marker. Observation is read-only and USB-only;
it requires the candidate's loaded module GNU identity, a new final RECOVERY
record, fresh final health and 180 seconds of qualifying sampled stability.
Clock-origin errors, stale final checks, wrong loaded modules and timeout
overruns have regressions. An overrun keeps its receipt but cannot pass.
Reverse image restoration remains separate from full application health.
The tool does not capture full raw dmesg, establish independent rescue, or
authorize deployment merely because its tests pass.

The CI allowlist now includes the observer test in all three explicit lists.
The integrated suite passed **29 normal and 26 optimized scripts**, three
documented optimized-script skips, zero failures; runner policy passed 7/7.
Optional source/tool skips remain explicit and are not hardware acceptance.
See [sanitized test and baseline receipt](../evidence/s22-camera-observer-host-20260927.json).

A fresh coordinator read-only check at 10:38 UTC confirmed the same boot,
242161.64 seconds uptime, full `42da267f…` RECOVERY, kernel GNU `b2dda820…`,
325 loaded modules, and native/desktop Pi/browser/model/network readiness.
Battery was full at 27 C; all nine readable thermal zones were at most 40 C.
Available diagnostics showed no serious fault or unresolved liveness; full
boot-log coverage is not claimed. The dedicated browser Pi session remains
expected on-demand standby, not a failed desktop service.

All five earlier global trial markers remain terminal/reconciled; no camera
marker exists. This continuation performed zero camera staging operations,
partition writes, reboot requests, camera activation or NPU BOOTUP. Current
phone image is still the working HCI image, not the camera candidate.
The exact remaining deployment condition is candidate-specific acceptance
of one unattended `b1041271…` camera boot with one conditional `42da267f…`
HCI restoration, or established independent rescue. The coordinator asked
that narrow question while host work continued; no answer is recorded yet.
Fresh operation-specific readiness must still be checked before execution.
No previous HCI single-attempt authorization or marker may be reused.
