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
