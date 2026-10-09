# NPU artifact/source audit and BOOTUP gate — 2026-09-22 (gate corrected 2026-09-23)

`tools/hardware/npu-boot-preflight.py` is a host-only source/config/artifact
audit. It never opens `/dev/vertex*`, invokes an ioctl, stages firmware,
reboots, or uses a phone. Its artifact result is separate from BOOTUP
readiness and authorization. The checker exits 2 unless all independent
lifecycle/runtime/rescue/authorization gates pass; no CLI input can set those
gates. Matching files alone can never authorize BOOTUP.

## Verified prerequisites

The captured exact configuration is:

```
CONFIG_NPU_USE_BOOT_IOCTL=y
CONFIG_NPU_USE_HW_DEVICE=y
CONFIG_DSP_USE_VS4L=y
CONFIG_EXYNOS_IMGLOADER=m
CONFIG_NPU_MAILBOX_VERSION=9
CONFIG_NPU_SECURE_MODE is unset
```

For this configuration, `npu-binary.h` selects `AIE.bin`. Because
`CONFIG_EXYNOS_IMGLOADER=m`, the active signature path in `npu-binary.c` calls
`imgloader_boot()` with `imgloader.fw_name = "AIE.bin"`; the direct
`request_firmware()` branch is not the active compiled path. Firmware search
therefore depends on the recovery kernel's imgloader/firmware-class setup and
its mounted root, not the host filesystem and not merely the existence of a
host `/vendor/firmware` directory.

The private host artifact checks pass:

* `AIE.bin`: SHA-256
  `a9843bbf520c08263c563f3200f0cbceb09c68fbdd1279d24236c3da704d9dc2`.
* `dsp_reloc_rules.bin`: SHA-256
  `468c0d2cc7c3a11fa80c3799385217bb4c36bb4cc369e754f73baae50e8b0a5d`.

`dsp_reloc_rules.bin` is loaded by `dsp_kernel_manager_dl_init()` during
`npu_hwdev_dsp_init(on=true)`, not by the NPU-only `hids=0x2` normal path. It
is retained as a module/combined-DSP prerequisite, not a normal NPU-only
BOOTUP blocker. `vectors.bin` has no caller in the pinned normal path.

## Early ordering and failure risk

The normal path is `npu_system_probe()` -> `npu_system_open()` -> platform
start/resume. On cold resume, `npu_system_resume()` allocates firmware log
memory, clears the mailbox area, and calls `npu_firmware_load()`; under the
active imgloader configuration this invokes `imgloader_boot()`. Any firmware
load error exits before the VS4L BOOTUP ioctl path.

For `NPU_NW_CMD_POWER_CTL`, `npu_hwdev_normal_bootup()` currently does:

1. `npu_hwdev_bootup()` (hardware refs),
2. `__vref_get()` (vertex boot ref),
3. `npu_sessionmgr_regHW()`,
4. `npu_session_NW_CMD_POWER_NOTIFY(true)`,
5. STM/HWACG enable and normal-count publication.

The current error label after step 4 only returns; it does not prove shutdown,
vertex-ref release, session-manager unregister, or lock release. A positive
POWER enqueue can also wait indefinitely on the raw session callback. Thus a
60-second recovery reboot scheduled before an unproven ioctl is not a safe
containment mechanism: a missing/invalid firmware failure can take a different
early path, while a POWER callback timeout/failure can leave refs and locks in
the kernel before the reboot, and panic/BUG paths are not guaranteed to honor
the schedule. Holding the vertex fd does not solve those kernel-owned refs.

## Safe host-only gate

Run:

```sh
python3 tools/hardware/npu-boot-preflight.py
```

Expected outcome on a private checkout may include
`artifact_preflight_pass: true`; a public checkout without the excluded
firmware/kernel inputs will report it false. In either case,
`bootup_ready: false`, `bootup_authorized: false`, and process exit code 2 are
required while lifecycle/runtime/rescue/owner gates remain unproven. The JSON
`readiness_gates` and `readiness_blockers` fields identify those independent
conditions; an artifact pass is not a device go/no-go. A public checkout does
not contain the private AIE/DSP firmware or pinned kernel source. The owner’s
separate local working copy may supply those inputs for private verification;
do not copy them into public Git to make CI green.

A future device probe requires, at minimum, an independently reviewed
request/session ownership repair, late-callback/close-race and error-unwind
regressions, confirmation of the recovery image's imgloader firmware search
root, tested firmware boot/shutdown, an independent recovery path, a live
validation receipt, and explicit owner authorization for the exact operation.
No probe C program is supplied or executed here; there is no safe “hold fd then
reboot” protocol until those kernel ownership conditions are met.

## 2026-09-24 POWER publication-liveness clarification

The lifecycle source audit separates four different claims; none authorize
BOOTUP:

| Status field | Evidence / current result |
| --- | --- |
| `power_response_timeout_bounded` | Source checks the 12,000 ms completion wait in the request waiter; true for the inspected candidate source. |
| `publication_drain_liveness_resolved` | Always false: cancellation can block in `wait_for_completion(&waiter->publish_done)` with no timeout. |
| `callback_lifetime_kernel_validated` | Always false: source-level cookie/request-id locking and the Python model are not execution or lifetime instrumentation of kernel C. |
| `firmware_boot_and_shutdown_device_tested` | Always false: no NPU boot/shutdown was performed on the phone. |

The publication path in the inspected lifecycle candidate is: session code
registers a stack-owned waiter under `npu_power_waiters_lock`, queues a
`POWER_CTL` request with `npu_ncp_mgmt_put()`, then waits up to 12 seconds for
the callback completion. The protocol worker recognizes this callback,
reserves and authorizes a publication lease under the same spinlock, then calls
`__mbox_nw_ops_put(entry)` synchronously. The worker finishes the lease only
after that call returns (or after an authorization/emergency branch). The
callback reconstructs the cookie, takes the spinlock, matches cookie plus
request ID, and ignores canceled waiters. If enqueue fails, the waiter is
removed under the lock and the caller returns the enqueue error. If the
response times out, cancellation marks the waiter canceled and drains any
already-authorized synchronous post before removing the stack waiter.

This ownership is why replacing the drain with a timeout and returning is not
safe: a publisher may still hold a lease referencing stack storage. The
unbounded drain preserves that storage but also means the whole operation is
not bounded if the synchronous mailbox post stalls. A post that returns
without a matching callback can still leave the response waiter to its
12-second timeout; that is distinct from a post that never returns. The
independent statuses in `npu-boot-preflight.py` intentionally keep those cases
separate.

`test_stalled_publication_retains_waiter_until_drain` is a deterministic
threaded **Python reference model**, not a test that executes kernel C. Source
contract checks inspect the candidate text, and the host tool labels the
publication-drain, callback-lifetime, firmware-runtime, and authorization
gates unresolved. No change here authorizes first BOOTUP; any future
owner-approved first experiment remains a separate operation requiring its
own reviewed containment and recovery procedure.
