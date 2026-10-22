# NPU acceptance: own_int8_conv3x3 -> ACCEPTED

- model sha256 `c16e51ae2fdfde8b`, graph rules on, run 2026-10-22T16:35:02Z

## 1 Graph
ops: CONV_2D x1

## 2 Compile
ok=True partitions=1 nnc=24176 bytes sections={'DSP_BINARY': False, 'NPU_BINARY': True} time=1.5s

## 3 NPU vs CPU reference (20 inputs: 20 random, 0 edge, 0 real)
- `output` tol 1: max |delta| 1, 325185/327680 exact, histogram 0:325185, 1:2495 -> ok

## 4 Determinism and latency
determinism: ok - 20 inputs x (cold vs 200-iteration run) and a repeat process: all bit-identical
latency over 4000 iterations: p50 0.32 ms, p99 1.30 ms, max 1.38 ms, mean 0.409 ms (first call median 0.72 ms)

## 5 Memory and recovery
peak RSS of the ENN process 17120 kB; MemAvailable before/after 5942376/5940244 kB; new kernel-log alarms: 0
canary conv before/after: same output (canary 81a2bde73ce3dded -> 81a2bde73ce3dded; candidate re-run identical)
