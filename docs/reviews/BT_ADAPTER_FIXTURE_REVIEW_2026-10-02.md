# Independent Bluetooth adapter-fixture review — 2026-10-02

## Scope and verdict

Reviewed the test-only fixture change at `69f8733dc9f88f9be99919f5152e5d0bb82e0abd`
and its repair note. The worktree was clean at that commit. The diff changes
only `tools/hardware/test-run-bt-hci-bridge-once.py` and adds
`docs/research/BT_ADAPTER_FIXTURE_REPAIR_2026-10-02.md`; the production adapter
and bridge C source are unchanged from the parent commit.

Verdict: approve the fixture repair. It restores coverage of the production
source-fingerprint refusal while giving artifact-corruption and staging-TOCTOU
tests an isolated, explicit host fixture manifest. The production adapter,
source/artifact pins, and authorization gates were not modified. This review
does not replace or revise the independent bridge-source review
`f663717de6ecdd8d1a433575bda99f1294fe8441`.

The configured review selection was `gpt-6-luna`, reasoning `max`; this records
configuration, not model self-identification. No phone, SSH, private build
header, firmware, host service, network transport, physical UART, or kernel
build was used. “Remote” calls in the TOCTOU test are local test doubles.

## Fixture and gate review

The baseline failures were reproduced by executing the parent commit's test
module bytes in memory, with `__file__` mapped to this worktree so the same
unchanged production adapter was loaded. Both `python3 -` and `python3 -O -`
reported 31 tests and the same two failures:

- Artifact-corruption expected `artifact SHA-256 mismatch` but stopped earlier
  at `build source fingerprint changed: tools/hardware/bt-h4-ibs-bridge.c`.
- The post-preflight replacement case expected
  `staged bridge artifact SHA-256 mismatch` but stopped at that same production
  source-pin refusal before reaching its intended board-stage assertion.

The repair uses the literal test-only source pin
`32bfebfc96864b6f5150195518fc7da996f62b7b21311d79c9bfea2725f9b1a1` for the
changed bridge. It matches the reviewed source bytes, but is not calculated
from the current file at test time. The production map still pins the bridge
to `476f148246dfac8330f7ade2790627b64a38ee7b1cb4f713934b6d438864a7a4`.
The fixture helpers copy the source inputs and runner files into a temporary
tree, then use scoped `mock.patch.multiple` contexts to substitute test
manifests. I independently forced exceptions out of both fixture context
managers and verified the original production map objects and artifact
SHA/size/build-ID values were restored. The fixture source manifest also
rejects a tampered copied bridge through the actual
`validate_local_provenance()` implementation.

The production artifact pins remain unchanged:

| Pin | Value |
|---|---|
| SHA-256 | `f4ba76613e1339314898ebbf067338d846ed9ee231907a44306b82f54f2f1684` |
| Size | `1042008` bytes |
| GNU build ID | `a5be9451d95335ae2a5d292d721a766208f55a87` |

With the production map active, I called
`validate_local_provenance(root=<review worktree>, artifact_path=/nonexistent,
private_headers=())`. It refused before opening the missing artifact or any
private header with the exact error:

```text
build source fingerprint changed: tools/hardware/bt-h4-ibs-bridge.c
```

The default CLI command
`python3 tools/hardware/run-bt-hci-bridge-once.py bt-hci-plain-h4-20260927`
also exited 1 with the existing live-trial authorization gate. No
fingerprint, artifact, or authorization was refreshed.

The repaired corruption and TOCTOU tests pass through the actual validator;
the replacement case also invokes the actual board `main()` with local fake
transport dependencies. After the synthetic artifact is replaced, the board
stage-input digest rejects it and the fake SSH staging wrapper records zero
stage calls; the destination remains absent. The local doubles record only
their preflight metadata/dmesg commands. No real remote command was run.

## Verification

The repaired suite passed in all requested modes:

| Command | Result |
|---|---|
| `python3 tools/hardware/test-run-bt-hci-bridge-once.py` | 32 tests, `OK` |
| `python3 -O tools/hardware/test-run-bt-hci-bridge-once.py` | 32 tests, `OK` |
| `PYTHONOPTIMIZE=1 python3 tools/hardware/test-run-bt-hci-bridge-once.py` | 32 tests, `OK` |

The last command was run without `-I`; a separate assertion printed
`sys.flags.optimize=1`. In optimized modes the suite exercised the adapter's
existing optimized-Python refusal as well as the isolated board metadata
runner behavior.

I also ran these three focused tests directly; each passed:

- `test_default_production_provenance_rejects_modified_bridge_before_remote`
- `test_actual_provenance_gate_rejects_fixture_source_tampering_and_artifact_binary`
- `test_replaced_temp_artifact_after_preflight_never_reaches_remote_stage`

`git diff --check` passed. All fixture sources and synthetic artifacts were
temporary host files. These results establish fixture/gate behavior only, not
controller registration, radio operation, Bluetooth audio, or device
acceptance.
