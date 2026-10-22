# NPU acceptance: k9 -> ACCEPTED

- model sha256 `172d1f0b088b5031`, graph rules on, run 2026-10-22T16:51:56Z

## 1 Graph
ops: CONV_2D x1

## 2 Compile
ok=True partitions=1 nnc=74360 bytes sections={'DSP_BINARY': False, 'NPU_BINARY': True} time=1.5s

## 3 NPU vs CPU reference (5 inputs: 5 random, 0 edge, 0 real)
- `output` tol 1: max |delta| 1, 47138/47360 exact, histogram 0:47138, 1:222 -> ok

## 4 Determinism and latency
determinism: ok - 5 inputs x (cold vs 20-iteration run) and a repeat process: all bit-identical
latency over 100 iterations: p50 0.64 ms, p99 1.33 ms, max 1.33 ms, mean 0.643 ms (first call median 0.99 ms)

## 5 Memory and recovery
peak RSS of the ENN process 16932 kB; MemAvailable before/after 5939448/5939776 kB; new kernel-log alarms: 0
canary conv before/after: same output (canary 81a2bde73ce3dded -> 81a2bde73ce3dded; candidate re-run identical)
