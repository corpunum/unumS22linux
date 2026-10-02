# Bluetooth adapter provenance fixture repair — 2026-10-02

This change repairs host regression fixtures only. No phone, SSH, ADB,
physical UART, firmware, controller, private header, or kernel build was used.
The artifact fixture is a synthetic ELF; board and remote dependencies are
local test doubles.

## Reproduced regression

Before changing the test file, both full adapter-suite commands reproduced the
same two failures. Each run reported 31 tests with two failures:

```sh
python3 tools/hardware/test-run-bt-hci-bridge-once.py
python3 -O tools/hardware/test-run-bt-hci-bridge-once.py
```

The artifact-corruption test expected `artifact SHA-256 mismatch`, and the
TOCTOU test expected `staged bridge artifact SHA-256 mismatch`. Both stopped
earlier with `build source fingerprint changed:
tools/hardware/bt-h4-ibs-bridge.c`. That is correct for production: the bridge
source in this checkout has SHA-256
`32bfebfc96864b6f5150195518fc7da996f62b7b21311d79c9bfea2725f9b1a1`, while
the unchanged production provenance map retains its prior reviewed hash.

## Fixture repair

The test defines one explicit `TEST_FIXTURE_BUILD_INPUT_SHA256` override for
the current bridge C source. A scoped fixture helper copies the pinned build
inputs and reviewed runners to an independent temporary source tree, then
temporarily supplies test-only source manifests while the actual
`validate_local_provenance()` runs. `mock.patch` restores the production maps
on exit, including when validation raises. The production artifact hash, size,
build-ID pins and the production source map are unchanged.

Coverage now checks both sides of the boundary:

- The default production manifest still rejects the modified bridge inside
  `run_trial()` before the missing artifact path, transport callback, or later
  fake trial callbacks are reached.
- Tampering with the copied fixture bridge after its manifest is established
  is rejected by the actual provenance validator.
- Corrupting the synthetic artifact first passes all source fingerprints and
  artifact owner/mode/size checks, then fails at the actual digest check.
- The TOCTOU test uses the actual validator and board `main()`: a valid
  synthetic ELF passes local hash and GNU build-ID validation, then a
  post-preflight replacement fails the board staging digest check. The fake
  remote stage wrapper records zero staging calls.

All fixture source and artifact inputs live under temporary directories.
Private build headers are explicitly omitted from these host-only validator
calls with `private_headers=()`.

## Verification

All three modes passed with 32 tests each:

```sh
python3 tools/hardware/test-run-bt-hci-bridge-once.py
python3 -O tools/hardware/test-run-bt-hci-bridge-once.py
PYTHONOPTIMIZE=1 python3 tools/hardware/test-run-bt-hci-bridge-once.py
```

Each reported `Ran 32 tests` and `OK`. `git diff --check` passed. These are
host fixture results only; they do not establish any phone, controller, or
radio behavior and do not authorize a trial.
