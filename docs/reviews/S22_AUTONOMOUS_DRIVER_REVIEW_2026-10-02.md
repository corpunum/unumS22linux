# Independent S22 driver source review — 2026-10-02

## Scope and reviewed identities

Reviewed the integrated source state at `87096f827d78090236767fc0953ac0fcbbce0e72` in the isolated review worktree. The audio change is commit `87096f827d78090236767fc0953ac0fcbbce0e72` (author worktree commit `5651aeeb10c249fc2a173523574496a2f107c22e`); its patch SHA-256 is `36adc3adec4a787cce522cbb768d628c23623bd409f8291a15af20117d3b6999`. The HCI test/documentation change is commit `0f643f74bd1fec357b8ff732be9c88417e076c40`.

Audio fixtures identify ABOX base `4e5c5ad7d950e4de0688b5663965f2075654b2ad` and derived tree `3fca50941422439b2019db2e4a3dc1016b2138a1`. HCI and IDA fixtures identify the same Lineage source base. No phone, SSH, ADB, kernel build, module load, or hardware state was accessed.

## Audio queue-error repair: sequential fix confirmed, review remains open

The patch moves `data->enabled = start` after `abox_rdma_request_ipc()` and commits only for a nonnegative result. This repairs the reproduced sequential case: a rejected START/STOP leaves the last accepted local state unchanged, so a later request is not suppressed. The code is minimal and applies after the existing observation patch to the pinned source.

The actual pinned ABOX queue path resolves the immediate return meaning. `abox_ipc_queue_put()` copies the message and returns 0 on insertion, `-EBUSY` when full, or `-EINVAL` for an oversized message. `abox_schedule_ipc()` calls `queue_work()` and, for this `atomic=true, sync=false` call, returns the final queue insertion result after bounded retries. That result does not represent later sender success or firmware acknowledgement. No ambiguous successful-insert/negative-return path was found; asynchronous sender failure remains outside this patch's state model.

The helper has two production entry paths. The PCM component `.trigger` caller returns the helper result. The backend DAI `.mute_stream` caller also returns it, but ASoC consumes errors at important call sites: `soc_pcm_prepare()` and `soc_pcm_hw_free()` ignore `snd_soc_dai_digital_mute()` results, and the DAPM DAI-link event warns then clears the error. The existing harness extracts only the PCM trigger caller, so BE failure handling and a subsequent BE callback retry are untested.

The main outstanding issue is concurrency. `data->enabled` remains a plain unsynchronized boolean. The helper can be entered through PCM trigger and backend mute paths; those paths use different PCM/DAPM locking domains, and the review did not establish a common exclusion guarantee for accesses to the same DMA data. An overlapping START can observe `enabled == false`, then a STOP can observe the same old value and return as a duplicate; if START is accepted and commits afterward, the STOP was never queued and the final cached state is true. The current single-thread shim cannot detect this interleaving. Same-state overlaps can also publish duplicate messages.

**Verdict:** the narrow sequential queue-rejection repair is correct. This review does not clear the audio change for integrated driver acceptance or deployment while same-data caller serialization and backend error/retry behavior remain unproven. A focused concurrent two-caller regression or source-backed serialization proof is required. If overlap is rejected instead of serialized, verify how a BE transition rejected with `-EBUSY` is retried, because the pinned upper call sites can swallow that error. Queue insertion errors must remain distinct from later asynchronous send failures.

## HCI lifecycle host regression: source scope passes

The test pins `net/bluetooth/hci_sock.c`, `lib/idr.c`, and `include/linux/idr.h` by commit and exact SHA-256, uses 128 KiB per-file caps, rejects redirects, bounds public fetches to five seconds, and requires exact blobs from an explicitly supplied local tree. It also hashes the existing restoration patch before applying it. The extracted C includes the actual create, release, destructor, cookie allocation/free, init/cleanup functions and actual `ida_free()` body; kernel socket, lock, XArray, SKB and registration operations are documented host shims.

The baseline behavior is represented accurately: `hci_sock_create()` returns success without allocating because its body is under `#if 0`; `hci_sock_init()` remains live and registers the protocol, socket family, and proc entry. The cookie sentinel concern is disproved by the pinned `ida_free()` guard `(int)id < 0`, which returns before taking the XArray lock. No cookie patch is warranted. The test covers the stated sequential lifecycle, cleanup, registration-unwind and sentinel cases. It explicitly does not claim real bind/ioctl, privilege, concurrent kernel locking, controller, radio or device coverage.

The HCI test path is present in `REVIEWED_HOST_TEST_PATHS`, `HOST_TESTS`, and the runner policy's expected path list. It is optimization-safe and the workflow runs the policy test followed by the fixed suite in normal and `-O` modes. The test fetches pinned public source when no local tree is supplied; environmental fetch failure exits 77, which the suite runner currently reports as a failure. The existing ABOX observation test has the same public-fetch dependency. This review environment could not resolve DNS, so public-fixture runs were unavailable; local pinned-blob runs passed. The workflow wiring itself is consistent. The repository README statement that the host suite makes no network request should be revisited because these fixture tests do fetch source.

**Verdict:** no source-scope blocker found in the HCI lifecycle test/documentation. Its host-shim limits are stated correctly; this is not live socket or device acceptance.

## Verification performed

- `python3 tools/hardware/test-audio-ipc-error-path.py --source-tree /home/corpunum/s22-workers/camera-kernel-build-20260927`, plus `python3 -O` and `PYTHONOPTIMIZE=1 python3`: all passed. Each run confirmed the expected baseline START/STOP failures and the patched extracted C at compiler `-O0` and `-O2`; ordered patch application checks passed.
- The same three Python modes for `tools/hardware/test-bt-hci-lifecycle-c.py --source-tree /home/corpunum/s22-workers/camera-kernel-build-20260927`: all passed. Each compiled and ran the extracted lifecycle and IDA code at C `-O0` and `-O2`.
- All six no-`--source-tree` public-fixture invocations exited 77 due to `Temporary failure in name resolution` / network environmental failure. These are unavailable, not passed.
- `python3 tools/hardware/test-host-regression-runner.py`: 7 policy tests passed. The HCI entry matches the runner allowlist. The full host suite was not run here.

These results establish bounded host-source behavior only. They establish neither a kernel build nor audio, HCI, or hardware acceptance.
