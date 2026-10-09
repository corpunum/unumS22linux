# Independent review: audio receipt JSON type correction — 2026-10-03

## Verdict

**PASS_LIMITED for the narrow host-side correction.** The previous receipt
classifier defect is fixed: required receipt fields now require their exact
JSON primitive types before value equality, and the audio profile identity
fields require strings. I reproduced the old acceptance cases, then confirmed
the corrected source rejects them; valid forward/reverse stage and flash
receipts and the wider requested regressions pass.

This clears only the bool/int and numeric-equality blocker in receipt
validation. It does not clear, rewrite, or replace the historical blocking
review, and grants no deployment or hardware-rescue authority. The audio
profile remains a host-only planning/validation tool with both authority
flags false.

The selected configuration remains `gpt-6-luna` / `max` per assignment. That
is selection evidence, not backend attestation. Review was host-only; no
phone, device, build, repack, RECOVERY operation, or network SSH transport was
used.

## Frozen scope and seven-file delta

The prepared worktree was clean at source commit
`eb35ac641b2dabf5f83283787fdfbfa0c3d2be67`, based on the frozen source and
the prior reviewer report. The corrected author commit is
`f2d1b41f3e3f92df9872aa150a55a78074467a45`; its five source/test files match
the imported patch byte-for-byte. Its parent correction delta changes exactly
these seven paths:

- `tools/hardware/deploy-audio-recovery.py`
- `tools/hardware/audio-coherent-recovery-profile.py`
- `tools/hardware/run-bt-hci-bridge-once.py`
- `tools/hardware/test-audio-coherent-recovery-profile.py`
- `tools/hardware/test-recovery-deployment-hardening.py`
- new `docs/research/AUDIO_RECEIPT_JSON_TYPE_CORRECTION_2026-10-03.md`
- new `evidence/s22-audio-receipt-json-types-host-20261003.json`

No old marker, receipt, authorization record, or previous review file is in
that delta. The earlier independent blocking review was preserved as commit
`fdcc817f7972d37ddfecd4a6a6eea97a721ef33a` (imported as
`214e9fd68414af122984df7dfefc44b5e360f142`); its review note and evidence
compare unchanged through the correction commit. The author's new research
note/evidence are additive. The old helper digest remains in historical
records only; no executable code pin still expects it.

## Independent pre-fix and post-fix reproduction

I loaded the prior frozen profile/helper from the preserved review worktree
and ran the new exact-primitive unittest against that old `PROFILE`. The one
test produced exactly ten expected failures (and no errors):

| Direction | Invalid receipt values accepted before correction |
| --- | --- |
| `audio-forward` | Stage `partition_written: 0` and `0.0`; flash `bytes` as equal float; `reboot_performed: 0` and `0.0` |
| `audio-reverse` | Same five values |

The same direct cases using the production partition size (`100663296.0` for
flash `bytes`) were accepted by the old module and rejected by the corrected
module. After the correction, the expanded receipt test passed for both
directions, both modes, and every required field.

In `deploy-audio-recovery.py:722-739`, `mode` is now required to be a string;
each mode-specific required field must have the exact type of its expected
value before equality is compared. Thus `False` cannot alias `0`/`0.0`, and
integer `bytes` cannot alias an equal float. The audio wrapper preserves its
exact key-set check and requires `profile` and `trial_identity` to be strings
(`audio-coherent-recovery-profile.py:269-296`). The positive stage/flash
receipts remain accepted in both directions; missing keys, extra audio
wrapper fields, wrong mode/profile/trial, bad before/candidate/readback
hashes, and primitive-type substitutions reject.

The low-level shared validator still checks its mode-specific required fields
rather than imposing an exact full key set on every consumer; the audio
profile wrapper separately enforces its exact schema. That is pre-existing
interface behavior, unchanged by this correction.

## Shared helper and consumer impact

The only behavioral change in the shared helper is inside
`validate_remote_receipt`. Independent imports of the old and corrected
helpers produced byte-identical `REMOTE_GUARDS`, `REMOTE_TEMPLATE`, and
rendered `REMOTE` values:

| Constant | SHA-256 (same before/after) |
| --- | --- |
| `REMOTE_GUARDS` | `8a435c6226a4478d1887739ffeccb1eaed329e04c7ad8e49fd3ee41c6d14e437` |
| `REMOTE_TEMPLATE` | `426fc74c05ef69ba5493a87460221fe2e2ceb7f691da2263a4752ed33f5f624b` |
| `REMOTE` | `2141ca511e1a6e34b82596b6b5fd3d5bf00e90521bfea69f2785465565ada982` |

The approved SSH wrapper validation, invocation construction, and transport
function source hashes also match the pre-fix source. Therefore the existing
RECOVERY target/lock checks, stage and flash branches, write/fsync behavior,
post-write readback checks, and no-reboot behavior are unchanged. The remote
template still emits JSON booleans for `partition_written` on stage and
`reboot_performed` on flash, and an integer byte count. No change enables a
reboot.

The profile change pins the corrected helper SHA-256
`4138413c16b9c90c70a46da6ed3b4f60b0f41f1c6c69b4b2047421895903a47f` and adds
string checks for its profile/trial identities. Its plan remains
`execution: false`, `stage_flash_cli_available: false`,
`current_deployment_authorized: false`, and
`independent_hardware_rescue_demonstrated: false`
(`audio-coherent-recovery-profile.py:319-348`). Its CLI still only builds and
prints a host plan or validates a local receipt (`:351-370`).

The BT HCI runner's only source change is the expected helper digest at
`run-bt-hci-bridge-once.py:36`; the actual helper lookup remains rooted at
`observer.ROOT` (`:390-405`). The focused regression
`test_bt_hci_runner_checks_the_helper_under_observer_root` passed in isolation
using temporary trusted/observer roots and a mocked host-key-alias callback
(`test-recovery-deployment-hardening.py:128-158`). It does not call SSH. The
updated pin equals the current helper's independently recomputed SHA-256.

The shared hardening suite executes the unchanged rendered stage/flash body
only with its temporary filesystem and syscall shims. The audio profile fake
filesystem exercises both staging directions with zero modeled partition
writes. These remain host fixtures, not provider or device evidence. The
shared suite also contains a local `ssh -V` executable-selection probe; it
does not connect to a host or invoke the approved remote transport. All
operation transports in the relevant tests are mocked.

## Test matrix

Python 3.12.3. The harness printed and checked `sys.flags.optimize` for each
run: normal `0`, `python3 -O` `1`, and `PYTHONOPTIMIZE=1 python3 -B` `1`.
`S22_AUDIO_PROFILE_ARTIFACT_ROOT` was unset so the private fixture followed
its public portable skip path.

| Mode | Audio profile | Shared recovery hardening | HCI profile |
| --- | --- | --- | --- |
| Normal (`optimize=0`) | 14 tests: 13 passed, 1 explicit private-artifact skip | 47: 44 passed, 3 explicit AVB-tool skips | 22 passed |
| `-O` (`optimize=1`) | 14: 13 passed, 1 explicit private-artifact skip | 47: 44 passed, 3 explicit AVB-tool skips | 22 passed |
| `PYTHONOPTIMIZE=1` (`optimize=1`) | 14: 13 passed, 1 explicit private-artifact skip | 47: 44 passed, 3 explicit AVB-tool skips | 22 passed |

The profile skip says private 100,663,296-byte package inputs are not part of
portable CI. The three AVB tests skipped because this isolated worktree does
not contain its pinned `tools/avb/avbtool.py`; I did not substitute an
external tool. The focused observer-root helper-pin test also passed alone.

## Limits and historical state

The old profile review's `BLOCKED` finding remains historical and unchanged;
this follow-up resolves only the bool/int and equal-float receipt issue in the
new source. The earlier stripped-artifact review and its separate later
capture reconciliation remain independent records; neither is rewritten by
this correction. Nothing here proves Samsung authentication, bootability,
module loading, audio operation, device installation, deployment permission,
or independent rescue. Both deployment authority and independent hardware
rescue remain false. No live remote transport, phone access, build, repack,
stage, flash, marker creation, reboot, or hardware test occurred.
