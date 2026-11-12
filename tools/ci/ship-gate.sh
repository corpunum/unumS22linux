#!/bin/sh
# Local ship gate for unumship (run from the repo root of the integration checkout). CI repeats all of this
# and adds what is left out here: host regressions in optimized mode, release cross-builds, screenshots.
# Cargo steps run only for crates the chain touches (the same path filter as the workflows).
set -e
export CARGO_TARGET_DIR=$HOME/.cache/unumship/target-unumS22linux; C=$HOME/.cargo/bin/cargo
touched() { ! git diff --quiet origin/main..HEAD -- "$1"; }
node --test --test-timeout=120000 tools/openunum-phone/plugins/s22-device/test/plugin.test.mjs tools/openunum-phone/plugins/s22-phone/test/plugin.test.mjs
python3 -I -B tools/hardware/test-host-regression-runner.py
python3 -I -B tools/hardware/run-host-regressions.py --mode normal || { echo "host regressions failed once; one retry (known timing flake: test_s22_llama_ondemand)"; python3 -I -B tools/hardware/run-host-regressions.py --mode normal; }
if touched tools/s22d; then $C fmt --manifest-path tools/s22d/Cargo.toml --check; $C clippy --manifest-path tools/s22d/Cargo.toml --locked --all-targets -j4 -- -D warnings; $C test --manifest-path tools/s22d/Cargo.toml --locked -j4; fi
if touched tools/unum-shell; then $C fmt --manifest-path tools/unum-shell/Cargo.toml --check; $C test --manifest-path tools/unum-shell/Cargo.toml --locked -j4; fi
