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
| `/root/hci_audio_trial_impl` | `/tmp/s22-hci-audio-prep-20260924`, `codex/s22-hci-audio-prep-20260924` | Commit `9d87080b25322f22ddcf56ddec3eed62c60a6b4b`, integrated as `cb20954`: host HCI artifact/module compatibility gate and synchronized audio snapshot regression. |

The storage worker was reassigned as a read-only independent reviewer of
deployment, NPU status, and HCI preflight; it did not author these changes.
Its initial review found a P2 deployment race and a partial-stage coverage gap.
Coordinator commit `2c7c60a` rechecks the baseline hash on the opened RECOVERY
fd, current mountinfo and fd identity/capacity immediately before `pwrite`;
the new injected regressions simulate a post-validation mount and a changed
partition baseline. The partial-stage test now attempts a flash from the
partial directory and confirms refusal with no receipt or partition write.
Independent review of this corrective commit is still pending.

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
