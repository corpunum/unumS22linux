# S22 Luna driver mission task board — 2026-09-24

This checkpoint continues the existing review branch. It does not replace the
historical board or claim that a new driver works. The original dirty checkout
and current phone installation were preserved. No device write, reboot,
package install, image build, or driver trial occurred in this wave.

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
| `/root/hci_audio_trial_impl` | `/tmp/s22-hci-audio-prep-20260924`, `codex/s22-hci-audio-prep-20260924` | Active: HCI artifact/module release preflight and audio trial preparation. Host ABI evidence received; validator/tests/commit pending. No build or device access. |

The storage worker was reassigned a read-only, independent review of deployment
commits `64a9ce3` and `98180d1`; it did not author that deployment work. Review
is pending. Do not treat it as approved until its result is recorded.

## Deployment regression and CI

The regression was first reproduced against the pre-fix deployment module by
executing its exact `render_remote()` body in the controlled sandbox. Stage
verified both full-size files, printed its stage receipt, then raised
`NameError: name 'actual' is not defined`. This occurred without SSH or device
access. Commit `64a9ce3` moves the final readback receipt into the flash branch.
Coordinator commit `98180d1` adds a partial staging-file write failure case;
it asserts no success receipt and zero partition writes.

The integration test runs the rendered body unchanged while redirecting
filesystem, sysfs and block-device operations to a temporary tree. It asserts:

- stage emits exactly one valid JSON receipt, verifies staged rollback and
  candidate bytes, and performs no partition write;
- successful flash emits exactly one receipt with the readback hash;
- invalid mode and failed target validation perform no partition write and emit
  no success receipt;
- partial stage and flash failures emit no success receipt.

The deployment suite is in the explicit host-regression allowlist and its
independently pinned path sequence. Three AVB-only cases explicitly skip in
this public worktree because the trusted public `avbtool.py` fixture is absent;
no private tool asset was published.

Coordinator verification on the integrated worktree:

| Check | Result |
|---|---|
| `test-recovery-deployment-hardening.py` normal, `python3 -O`, `PYTHONOPTIMIZE=1` | 40 passed in each mode; 3 explicit AVB skips per mode |
| `run-host-regressions.py --mode both` | Exit 0; zero failures; normal and eligible optimized suites passed, with two documented `-O` skips |
| `test-host-regression-runner.py` | 7/7 passed |
| `test-s22-capacity-rescue-audit.py` normal, `-O`, `PYTHONOPTIMIZE=1` | 7/7 in each mode |
| `git diff --check` for current source/docs | Passed |

Hosted Actions for the eventual new branch head remains to be checked against
the exact pushed SHA.

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
`/home/corpunum/s22-linux/builds/audio-extra-v2-20260922/recovery.img`,
SHA-256 `758fc9d30491e17b7c829a89d338ba69476efa15a1280deb8a1b9b8009687f4b`;
it matched the current RECOVERY partition in an earlier readback. It is not
published. Never restore Android userdata or write unrelated partitions.

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

Minimal build correction proposed by the HCI worker is an exact clean release
`5.10.260-g4e5c5ad7d950`, followed by a full module vermagic and Module.symvers
check. No corrected build was produced. The existing candidate is not ready.
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

1. Complete the HCI host-only validator/tests and record its commit.
2. Receive independent deployment review, then inspect the complete sanitized
   diff and publish the unmerged review branch; verify hosted Actions on its
   exact SHA.
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
