# Audio recovery receipt JSON type correction — 2026-10-03

## Result

The shared recovery receipt validator now requires exact Python/JSON primitive
types as well as the expected values. This closes the demonstrated equality
alias where JSON integer `0` and float `0.0` compared equal to boolean `false`,
and where a JSON float equal to the configured byte count compared equal to its
integer value. The audio profile wrapper also requires its `profile` and
`trial_identity` values to be strings.

The pre-fix regression test failed in 10 cases across both audio directions:
stage `partition_written` accepted integer/float zero, flash `bytes` accepted
an equal float, and flash `reboot_performed` accepted integer/float zero.
Direct reproduction against the frozen code also accepted those stage/flash
integer-zero examples as `receipt_validated: true`. After the fix, the expanded
matrix rejects wrong null, boolean, integer, float, and string variants for
every required field in both modes and both directions.

## Scope and pins

The shared helper change is limited to `validate_remote_receipt` in
`tools/hardware/deploy-audio-recovery.py`: `mode` must be a string, and every
expected field must have the exact type of its expected value before equality
is considered. Its remote template, transport, locking, write, reboot, and
receipt-generation logic are unchanged. The audio profile validator pins the
updated helper and explicitly checks string types for its two identity fields.

`run-bt-hci-bridge-once.py` pins the same helper at
`validate_trusted_transport`: source reads `observer.ROOT/tools/hardware/
deploy-audio-recovery.py`, rather than the separate trusted-root tree. Its
helper digest was updated to the new exact SHA, and a focused test calls this
check with temporary roots and a mocked host-key-alias callback. No trial ID,
marker, path, authorization gate, or transport behavior changed. Historical
review notes and receipts are retained with their original helper digest;
they have not been rewritten to imply they reviewed this correction.

The frozen pre-correction author source was `bddb66ac717b3668898eb78c825d02aadbba254b`
(imported as `f16e39025e51958efd0b4b1492657d64e3b88cdd`); its independent
blocking review was `31c248e857ae0db60762532d96df8c696a3cea27`. The new helper
SHA-256 is
`4138413c16b9c90c70a46da6ed3b4f60b0f41f1c6c69b4b2047421895903a47f`.

## Verification

The audio profile suite, shared recovery hardening suite, and HCI profile suite
were run under normal Python, `python3 -O`, and effective
`PYTHONOPTIMIZE=1 python3 -B`. The invocation asserted `sys.flags.optimize` in
each process (0 for normal, 1 for both optimized modes). Results in every mode:

- Audio profile: 14 tests; 13 passed and the optional private-artifact fixture
  explicitly skipped because no artifact root was configured.
- Shared recovery hardening: 47 tests; 44 passed and 3 AVB-tool-dependent
  tests explicitly skipped because the pinned tool is absent in this worktree.
- HCI profile: 22 tests passed.

The suites retain their rendered-code fake-filesystem coverage: both audio
profile stage directions verify candidate/rollback hashes and zero modeled
partition writes; the shared hardening fixture executes its actual rendered
stage/flash code only against temporary filesystem/syscall shims. The focused
runner-pin test uses temporary files and does not call SSH.

No phone, SSH, ADB, build, repack, stage, flash, marker creation, reboot, or
hardware test was performed. `current_deployment_authorized` and
`independent_hardware_rescue_demonstrated` remain false; this correction is
limited to host-side receipt validation and does not authorize execution.
