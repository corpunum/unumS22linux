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
- `03eba0700f65e0ef1576a50a1ab43dd2b325d5bd`, integrated as
  `9dc83b3` — RECOVERY deployment/build hardening and optimization-mode
  negative tests.
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
| `python3 tools/hardware/test-recovery-deployment-hardening.py` | Pass, 28 tests in normal, `python3 -O`, and `PYTHONOPTIMIZE=1` modes | Host mocks plus synthetic AVB footer/corruption test; no operational deploy path |
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

Independent Luna review completed against exact commit
`03eba0700f65e0ef1576a50a1ab43dd2b325d5bd`. It found three issues before
deployment hardening can be considered complete: the AVB builder accepts an
arbitrary verifier executable, the primary deployer accepts a symlinked SSH
wrapper, and remote staging does not anchor filesystem operations against a
writable-parent path-replacement race. The deployment author has a follow-up
to add fixes and negative tests. The deployment entrypoints were not invoked;
only isolated mocked tests and read-only AVB verification were run. The code
is WIP, unmerged and undeployed.

The Bluetooth independent Luna review also completed. It passed H4 12/12 and
the exact-pinned-source HCI validator, found no defect in the changed C
cleanup-reporting path, and requested two P2 test-hardening changes: isolate
test binaries under a per-run temporary directory and prove capability denial
control flow with a negative mutation. A P3 naming clarification was also
requested. The author follow-up is committed as
`d3f50668ecf19c117a503f6cafaca19cc72df4e0` and coordinator host reruns pass;
an independent review of that follow-up is active. This does not verify the
HCI kernel patch by compilation or runtime; device deployment remains blocked.

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
