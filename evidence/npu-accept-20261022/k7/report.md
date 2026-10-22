# NPU acceptance: k7 -> ACCEPTED

- model sha256 `5a28727c87e0f700`, graph rules on, run 2026-10-22T16:35:32Z

## 1 Graph
ops: CONV_2D x1

## 2 Compile
ok=True partitions=1 nnc=57976 bytes sections={'DSP_BINARY': False, 'NPU_BINARY': True} time=1.5s

## 3 NPU vs CPU reference (20 inputs: 20 random, 0 edge, 0 real)
- `output` tol 1: max |delta| 1, 189206/189440 exact, histogram 0:189206, 1:234 -> ok

## 4 Determinism and latency
determinism: ok - 20 inputs x (cold vs 200-iteration run) and a repeat process: all bit-identical
latency over 4000 iterations: p50 0.32 ms, p99 1.31 ms, max 1.41 ms, mean 0.416 ms (first call median 0.96 ms)

## 5 Memory and recovery
peak RSS of the ENN process 17052 kB; MemAvailable before/after 5940500/5938848 kB; new kernel-log alarms: 0
canary conv before/after: same output (canary 81a2bde73ce3dded -> 81a2bde73ce3dded; candidate re-run identical)
