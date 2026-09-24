# S22 Luna driver mission task board — 2026-09-24

This checkpoint continues the existing review branch. It does not replace the
historical board or claim that a new driver works. The original dirty checkout
and current phone installation were preserved. The initial checkpoint below
preceded candidate packaging; the HCI follow-up at the end records the host
image build/package completed afterward. No device write, reboot, package
install, or driver trial occurred.

## Luna workers

Workers were actually spawned through the collaboration runtime in isolated
worktrees. Each was explicitly configured as `gpt-6-luna` with `max` reasoning.
Runtime/session metadata is unavailable, so this is configured-selection
evidence, not runtime-reported identity.

| Worker | Worktree / branch | Assignment and result |
|---|---|---|
| `/root/deploy_stage_regression_impl` | `/tmp/s22-deploy-staging-20260924`, `codex/s22-stage-regression-20260924` | Commit `7f84aaf3770ebca3f5c7ad7e0b367ed99b199588`, integrated as `64a9ce3`: reproduce and fix staged receipt `NameError`; execute rendered deployment body in fake filesystem/sysfs/block-device sandbox; add fixed CI allowlist coverage. |
| `/root/npu_liveness_impl` | `/tmp/s22-npu-liveness-20260924`, `codex/s22-npu-liveness-20260924` | Commit `b3f7a1bb8b7cae49757186f68dd1d9f4f1434d3c`, integrated as `e4b3ce3`: split timeout/drain/callback/device readiness and add stalled-publication Python reference-model test. |
| `/root/storage_rescue_impl` | `/tmp/s22-storage-rescue-20260924`, `codex/s22-storage-rescue-20260924` | Commit `87d7ca8c91685268692c4035c195f71c7dd56535`, integrated as `7d05b61`: destination capacity auditor, seven sanitized tests, rescue/capacity note. Tests passed normal, `-O`, and `PYTHONOPTIMIZE=1`. |
| `/root/hci_audio_trial_impl` | `/tmp/s22-hci-audio-prep-20260924`, `codex/s22-hci-audio-prep-20260924` | Commit `9d87080b25322f22ddcf56ddec3eed62c60a6b4b`, integrated as `cb20954`: host HCI artifact/module compatibility gate and synchronized audio snapshot regression. |

The storage worker was reassigned as a read-only independent reviewer of
deployment, NPU status, and HCI preflight; it did not author these changes.
Its initial review found a P2 deployment race and a partial-stage coverage gap.
Coordinator commit `2c7c60a` rechecks the baseline hash on the opened RECOVERY
fd, current mountinfo and fd identity/capacity immediately before `pwrite`;
the new injected regressions simulate a post-validation mount and a changed
partition baseline. The partial-stage test now attempts a flash from the
partial directory and confirms refusal with no receipt or partition write.
Independent re-review of `2c7c60a` by `/root/storage_rescue_impl` passed at
branch head `c27474e`; the branch changed only documentation afterward. The
review confirms the long stale-state gap is closed to the requested
immediate-pre-write checks and the partial-stage-to-flash gap is closed. This
is not an atomic exclusion against a non-cooperating external actor changing
mount or partition state after the final checks. The HCI tests still cover the
pure vermagic/CRC decision helpers rather than the complete image/manifest/
cpio pipeline; coordinator separately ran the actual local candidate through
the checker and recorded its fail-closed result above.

## Deployment regression and CI

The regression was first reproduced against the pre-fix deployment module by
executing its exact `render_remote()` body in the controlled sandbox. Stage
verified both full-size files, printed its stage receipt, then raised
`NameError: name 'actual' is not defined`. This occurred without SSH or device
access. Commit `64a9ce3` moves the final readback receipt into the flash branch.
Coordinator commit `98180d1` adds a partial staging-file write failure case;
it asserts no success receipt and zero partition writes. After review found a
stale-check race, commit `2c7c60a` adds immediate pre-write opened-fd baseline,
mount and identity/capacity checks plus race-injection regressions.

The integration test runs the rendered body unchanged while redirecting
filesystem, sysfs and block-device operations to a temporary tree. It asserts:

- stage emits exactly one valid JSON receipt, verifies staged rollback and
  candidate bytes, and performs no partition write;
- successful flash emits exactly one receipt with the readback hash;
- invalid mode and failed target validation perform no partition write and emit
  no success receipt;
- partial stage and flash failures emit no success receipt.
- mount state changing after initial validation and a baseline changing before
  write both block partition writes.

The deployment suite is in the explicit host-regression allowlist and its
independently pinned path sequence. The HCI compatibility synthetic suite and
audio synchronization suite are also in the fixed CI allowlist (integration
commit `2572950`). Three AVB-only cases explicitly skip in
this public worktree because the trusted public `avbtool.py` fixture is absent;
no private tool asset was published.

Coordinator verification on the integrated worktree:

| Check | Result |
|---|---|
| `test-recovery-deployment-hardening.py` normal, `python3 -O`, `PYTHONOPTIMIZE=1` | 42 passed in each mode; 3 explicit AVB skips per mode |
| `run-host-regressions.py --mode both` after deployment/NPU/storage/HCI/audio integration | Exit 0; all ten allowlisted scripts passed in normal mode and all optimization-safe scripts passed under `-O`, with two documented skips |
| HCI synthetic preflight suite normal, `-O`, `PYTHONOPTIMIZE=1` | 5/5 each |
| Audio snapshot synchronization suite normal, `-O`, `PYTHONOPTIMIZE=1` | 2/2 each |
| `test-host-regression-runner.py` | 7/7 passed |
| `test-s22-capacity-rescue-audit.py` normal, `-O`, `PYTHONOPTIMIZE=1` | 7/7 in each mode |
| `git diff --check` for current source/docs | Passed |

Hosted GitHub Actions run `35960241159` completed successfully in 24 seconds
for review-branch SHA
`6caf701d027a7f1293343ef941df8dd55aa28ab7`. It ran the allowlist and fixed
host suite with no secrets or device steps. A documentation follow-up will
advance the branch; its exact head must be checked in a separate hosted run.

## NPU readiness and publication ownership

Preflight now separates `power_response_timeout_bounded`,
`publication_drain_liveness_resolved`,
`callback_lifetime_kernel_validated`, and
`firmware_boot_and_shutdown_device_tested`. The inspected candidate has a
12-second POWER response wait. On timeout it cancels the waiter and calls
`wait_for_completion(&waiter->publish_done)` with no timeout.

The protocol worker recognizes the callback, reserves then authorizes a
publication lease under `npu_power_waiters_lock`, performs synchronous
`__mbox_nw_ops_put(entry)`, and releases the lease only after that call returns
(including fail-closed branches). If enqueue fails, the waiter is removed under
the lock and the error is returned. The callback matches opaque cookie plus
request ID while holding the same lock. The indefinite drain prevents stack
waiter storage from being freed while a publisher still owns a lease, but it
means full operation liveness is not bounded if the mailbox call stalls. A
naive timeout and free would violate that ownership.

The stalled-publication test is a threaded **Python reference model**. It does
not execute or instrument kernel C. Callback lifetime, firmware boot/shutdown,
live probe and BOOTUP authorization remain false. No NPU boot ioctl was issued.

## Capacity and independent recovery

At approximately `2026-09-24T05:08Z`, read-only USB SSH reports SSH and PID 1
sharing mount namespace ID `mnt:[4026531840]` while exposing different root
paths in their mount tables. SSH cannot resolve `/cache`; the CACHE-backed
upper was measured through `/proc/1/root/cache/s22-linux/upper` without
changing it.

| Phone destination | Filesystem/view | Available space | Free inodes | Relevance |
|---|---|---:|---:|---|
| `/` | overlay backed by CACHE upper | 34,028 KiB to unprivileged statvfs | 29,780 | nearly full; no cleanup |
| `/run` | rootfs tmpfs bind | 2,303,656 KiB | 607,115 | tiny operation lock only |
| `/srv/s22` | userdata ext4 | 99,982,692 KiB | 1,652,508 | two 100,663,296-byte files plus a directory for staged candidate/rollback |
| `/tmp` | separate 256 MiB tmpfs | 256 MiB | 830,421 | deployment script does not use it |

Deployment streams the candidate from the host through SSH stdin, stages
candidate and rollback under `/srv/s22`, creates a small lock under `/run`,
and stores its receipt in the host project tree. No phone package install or
`/tmp` output is part of that script. Host regression temporary data is on the
host ext4 filesystem. No live storage cleanup or staging took place.

Historical 2026-09-20 physical Download Mode and rollback evidence shows that
the Odin-protocol transport worked at that time; it is not a fresh rescue
qualification. Current USB SSH and Tailscale reached over that same running
kernel are not independent rescue. The exact physical check still required is
an owner-assisted session entering Samsung Download Mode while connected to
the actual recovery host, then a read-only successful `samloader detect`.
There is no flash in that check.

Previously verified host-accessible native rollback:
`builds/audio-extra-v2-20260922/recovery.img`, SHA-256
`758fc9d30491e17b7c829a89d338ba69476efa15a1280deb8a1b9b8009687f4b`; it
was read back from `/dev/block/sda16` and matched the host artifact at
`2026-09-24T05:09Z`. The artifact remains in the owner checkout; the image
itself is not published. Never restore Android userdata or write unrelated
partitions.

## Smallest HCI trial and current phone

The installed release is `5.10.260-g4e5c5ad7d950`. Existing HCI image
`builds/bt-hci-socket-restore-image-20260923/recovery.img` has SHA-256
`6d7e2a4adefa32a87b4b47bf5eea59ba79b71e169dff8328acbcd3b68e06ae01`, based
on source `4e5c5ad7d950e4de0688b5663965f2075654b2ad`, config SHA-256
`a147841a53f5b10c366a759d0e83525996a0ec5d8227a103b020cf2111400f9e`. Its
kernel release is `5.10.260-g4e5c5ad7d950-dirty`, while all 324 unchanged
ramdisk modules and loaded `btpower`/`exynos_tty` modules report the clean
release. The O-tree comparison found all 16,547 imported module symbol
versions with zero missing exports and zero CRC mismatches; the `-dirty`
vermagic mismatch still blocks deployment. Do not force-load these modules.

The HCI host preflight executed against the existing candidate and independently
verified image SHA `6d7e2a4adefa32a87b4b47bf5eea59ba79b71e169dff8328acbcd3b68e06ae01`,
embedded kernel SHA
`9a694c093fd24031a7ece26741739ba52cfc6ff54651605533309ff9a5a9c1d6`, and
unchanged ramdisk SHA
`0dd9dda696c26ccf4c99d77f9d24f334f0c4baece5e19312bcc12f2963841e5d` (equal to
the pinned base ramdisk). The source HEAD is
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`; only `net/bluetooth/hci_sock.c`
was dirty in the inspected build source. `.config` SHA is
`a147841a53f5b10c366a759d0e83525996a0ec5d8227a103b020cf2111400f9e`, with
`CONFIG_LOCALVERSION_AUTO=y` and `CONFIG_LOCALVERSION=""`.

The coordinator ran the new read-only preflight against the actual local image,
manifest and O-tree; it exited 2 with the two expected blockers (dirty source
and full-vermagic mismatch), without device access. The committed synthetic
HCI tests exercise the pure module-vermagic and CRC verdict helpers; they do
not exercise full image/header/manifest/cpio inspection. No private image or
kernel artifacts are included to provide such a CI fixture.

The preflight confirms 16,547 imported symbol CRCs match the O-tree
`Module.symvers` with no missing symbols or mismatches, but all 324 ramdisk
module vermagics still differ from the candidate's `-dirty` release. This is
host artifact evidence, not a live query. Minimal build correction proposed by
the HCI worker is to commit the repair into a clean source tree, set
`CONFIG_LOCALVERSION_AUTO=n` and
`CONFIG_LOCALVERSION="-g4e5c5ad7d950"`, verify `make kernelrelease` equals
`5.10.260-g4e5c5ad7d950`, then rerun full module vermagic and Module.symvers
checks. No corrected build was produced. The existing candidate is not ready.
After release compatibility, independent Download Mode rescue qualification,
and the existing operation-specific authorization, the first HCI test should
be raw socket create/close only, before controller attach, discovery or
pairing. Do not retry on the current kernel; `hci_uart` is not loaded.

At `2026-09-24T05:08Z`, USB SSH reports `5.10.260-g4e5c5ad7d950`, PID 1
`native-guardian`, resident Hyprland and Qwen llama-server; the assistant
health endpoint returned `{"status":"ok"}`. `npu`, `btpower`, and
`exynos_tty` are loaded. These are presence/health observations, not driver
acceptance. No touch event, audio DMA/playback, NPU inference, Bluetooth
pairing, new Wi-Fi acceptance or independent rescue was established.

## Exact next gates

1. Finish independent review of deployment, NPU status, and HCI preflight;
   fix any findings and record reviewer evidence.
2. Inspect the complete sanitized diff and publish the unmerged review branch;
   verify hosted Actions on its exact SHA.
3. Do not build/deploy HCI until the clean-release candidate and every loaded
   module pass exact compatibility checks.
4. Do not start any device trial until the owner-assisted Download Mode check
   succeeds from the actual recovery host and the existing authorization and
   rollback procedure are ready.
5. Physical-touch acceptance still needs owner presence. NPU BOOTUP remains
   unauthorized. Continue audio diagnostics without opening streams until its
   experiment-specific approval gate is satisfied.

The original owner checkout remains dirty and untouched. `master` remains
unchanged; this document and code are for the unmerged review branch only.

## 2026-09-24 HCI candidate follow-up — supersedes the earlier HCI blocker

The request to build the HCI candidate was host-side and did not require
physical phone access. The earlier circular task-board instruction not to
build until a clean-release candidate passed checks is superseded: host build
and compatibility validation are complete; deployment still waits for the
independent Download Mode rescue check and operation-specific authorization.

An explicitly selected `gpt-6-luna` implementation worker with Max reasoning
completed the loader-compatible preflight and tests in isolated worktree
`/tmp/s22-hci-vermagic-20260924`, commit
`5a968184153bf3122a0a2785fceb350a29c1f245`, integrated as `3c21cf7`. Runtime
session metadata was unavailable, so the evidence is the explicit configured
selection, not runtime-reported identity. Its 21 focused tests passed in
normal, `python3 -O`, and `PYTHONOPTIMIZE=1` modes. Attempts to start additional
independent workers were refused by the collaboration runtime with
`agent thread limit reached`; no worker was fabricated or silently replaced.

Pinned `kernel/module.c` semantics now govern vermagic checks. All 324
ramdisk modules and the selected external Lineage WLAN module pass the loader
comparison and recorded CRC checks. The release prefix may differ when
`__versions` exists and remaining vermagic flags match; version-section
presence, `module_layout`, missing/mismatched CRCs and evidence completeness
are checked separately. Source/artifact provenance and operational exact-name
assumptions are separate verdicts. The inactive FYI3 WLAN alternate fails its
CRC checks and is not included. The known candidate source inventory is 325;
actual loaded membership and any further boot/runtime sources remain unknown
until a live query.

The coordinator reused the clean, committed HCI-only kernel output from source
commit `f52cbbd7e2783d529e1e5742d94e0fd64889bbdf`; no heavy rebuild was needed.
Source/config/toolchain/image hashes and reproducible packaging procedure are
in [the HCI trial record](S22_HCI_CANDIDATE_TRIAL_2026-09-24.md). The local
RECOVERY image SHA-256 is
`42da267f3dd9f94f30f62a95fb2ac13f91d4cf98f1a2307f7cc14e45d9c49be5`. The
preflight passed with 17,042 version records and 325 `module_layout` records.
This is host evidence only: no candidate boot, raw HCI socket test, radio
attachment, scanning or pairing has occurred.

The candidate keeps the exact release name currently required by the
initramfs and release-sensitive Wi-Fi/audio/deployment helpers. The source
commit and byte hashes expose the changed kernel; it was not binary-edited or
force-loaded. Rollback is the existing host-accessible native RECOVERY image
`builds/audio-extra-v2-20260922/recovery.img`; historical `/dev/block/sda16`
readback matched at 2026-09-24 05:09 UTC and the host copy was re-hashed.
Current phone state was not re-queried during this preparation.

Installed `samloader` help confirms read-only `detect --verbose` and explicit
`flash --verbose --no-reboot -p RECOVERY <image>`. The exact controlled
procedure, including Download-to-RECOVERY buttons, baseline gate, raw-socket
test and RECOVERY-only rollback, is in the trial record. The sole next owner
action is to attend with the phone connected to the actual recovery host, put
it in Download Mode and confirm the screen. The coordinator will first run
read-only detection and verify candidate/rollback hashes; the flash still
requires explicit authorization for that specific RECOVERY write. `master`
remains unchanged and no image/module/firmware artifact is published.

## 2026-09-24 independent HCI review and candidate validation

An explicitly selected `gpt-6-luna` independent reviewer with Max reasoning
used isolated worktree `/tmp/s22-hci-candidate-review-20260924` and committed
`9a3277cd174759d6272bc024fe06f4837cdd3db8`; it was reviewed and integrated
here as `8ece563`. Runtime/session metadata was unavailable, so this records
the explicit model configuration, not runtime-reported identity. The reviewer
found a real source-provenance gap: the builder could pair a supplied source
tree's HEAD with an O-tree linked to a different source. The builder now
requires the O-tree `source` link to resolve to the supplied tree, and
preflight compares manifest `source_commit` with that O-tree's HEAD. Matching
and mismatch tests cover both checks. For this candidate, the clean source,
manifest commit, and O-tree link all resolve to
`f52cbbd7e2783d529e1e5742d94e0fd64889bbdf`.

Post-integration validation at `8ece563`:

- `python3 -I -B tools/hardware/run-host-regressions.py --mode both`: all
  allowlisted host suites passed in normal and optimized modes. The deployment
  suite passed 45 tests each time, with three expected AVB-fixture skips
  because the public review worktree does not contain the private trusted
  `avbtool.py`; the actual candidate footer/hash was separately checked with
  the pinned owner-checkout tool.
- The HCI preflight suite passed 23 tests normally, under `python3 -O`, and
  with `PYTHONOPTIMIZE=1`. The deployment suite passed 45 tests in all three
  modes, with the same three AVB-fixture skips.
- The actual local candidate preflight exited 0 with no issues: 324 ramdisk
  modules plus the selected Lineage WLAN module, 17,042 version records, and
  325 `module_layout` records passed. The candidate image SHA-256 remains
  `42da267f3dd9f94f30f62a95fb2ac13f91d4cf98f1a2307f7cc14e45d9c49be5`.
  Actual `/proc/modules` membership and any other boot/runtime module sources
  remain unknown; no device evidence is implied.
- The reviewer confirmed pinned `kernel/module.c` behavior and the rollback
  image hash. It also found that the Recovery key handoff had been overstated:
  Samsung's support page documents Download-mode exit but not the complete
  RECOVERY transition. The trial note now labels the S22+ key sequence as a
  third-party-described, physically unverified procedure; mode must be
  confirmed from `/proc/boot_reset`/BORE after boot.
- A fresh read-only `samloader detect --verbose` on the recovery host returned
  `Failed to detect compatible download-mode device` (exit 1). The host still
  has no independent Download Mode rescue qualification. Candidate and native
  rollback files each remain exactly 100,663,296 bytes and their recorded
  SHA-256 values were freshly verified. No device write, reboot, or Bluetooth
  operation occurred.

Additional implementation-worker spawning was attempted for this follow-up,
but the collaboration runtime returned `agent thread limit reached`; no
replacement model or fictitious worker was used. Existing explicitly selected
Luna workers delivered loader/preflight implementation, candidate packaging,
recovery/storage work, and the independent review described above. At this
checkpoint the only owner action is one attended session: connect the phone to
the actual recovery host, put it in Download Mode, and confirm the screen.
The coordinator will first run read-only detection and verify both local
images. That action does not authorize flashing; the one RECOVERY write still
requires its own explicit authorization after rescue is proven. `master`
remains unchanged.

## 2026-09-24 final HCI provenance review and recovery-path gate

The independent Luna reviewer identified one further provenance bypass at
`bb62089`: arbitrary tool names/version strings could pass despite a valid
64-character hash shape. Commit `8c7cc329dffa53240fc23d50dd1b22117b5ad2ed`
closes that case by requiring recorded clang and ld.lld identities whose
version lines match the kernel O-tree's embedded `LINUX_COMPILER`; the exact
malicious-name/version case is now tested. A follow-up independent review
confirmed that bypass is closed and found no additional concrete defect.
Tool binary SHA-256 values are preserved in the build record, but the
preflight explicitly does not claim to re-hash executable binaries from the
kernel image; it validates the names and version strings against embedded
compiler metadata.

At this revision the focused HCI suite passed 27 tests in normal,
`python3 -O`, and `PYTHONOPTIMIZE=1` modes. The complete host runner passed in
normal and optimized modes; deployment passed 45 tests with three expected
AVB-fixture skips. Actual candidate preflight still exits 0 with loader and
build-provenance verdicts true. No image or other private build artifact is
tracked.

The recovery note now requires a no-write test of the Download-to-current-
RECOVERY handoff and BORE/ADB confirmation before any candidate write. From
that known-good native session, the owner-present session can test
`s22-reboot download` and re-run read-only Download detection; that target is
accepted by the installed helper but has not been exercised on the phone.
Samsung documents Download-mode exit, and an S22+ carrier guide documents the
Volume Up + Side Recovery keys, but the combined handoff remains an inference
until physically verified. This is now an explicit stop gate, not claimed as
proven.

Current host detection still reports no connected Download Mode device.
Nothing was flashed or rebooted. The single owner action requested is to be at
the phone, connect it to the actual recovery host, enter Download Mode, and
confirm the screen. The coordinator will first detect read-only and check both
image hashes; then it can guide the no-write Recovery-path verification. A
candidate RECOVERY write still needs separate explicit authorization after
rescue and return-to-RECOVERY are proven. `master` remains unchanged.
