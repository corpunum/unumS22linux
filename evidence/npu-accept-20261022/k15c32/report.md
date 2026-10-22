# NPU acceptance: k15c32 -> REJECTED

- model sha256 `e2c4f505d9a3e43c`, graph rules on, run 2026-10-22T16:53:54Z
- reasons:
  - output output: 23567 of 23680 values differ by more than 1 (max |delta| 128)
  - NPU output is constant while the CPU reference is not (5 cases, e.g. random00/output)
  - execution stall: max 5009 ms (> 1000 ms; the known failure mode is ~4300 ms with all-zero output)

## 1 Graph
ops: CONV_2D x1

## 2 Compile
ok=True partitions=1 nnc=24184 bytes sections={'DSP_BINARY': False, 'NPU_BINARY': True} time=1.5s

## 3 NPU vs CPU reference (5 inputs: 5 random, 0 edge, 0 real)
- `output` tol 1: max |delta| 128, 42/23680 exact, histogram 0:42, 1:71, 2:75, 3:76, 4:78, 5:99, 6:84, 7:83 -> FAIL
- `output` worst inputs: random00 (128), random01 (128), random02 (128)

## 4 Determinism and latency
determinism: ok - 3 inputs x (cold vs 5-iteration run) and a repeat process: all bit-identical [stalled: only 3 inputs x 5 iterations run]
latency over 15 iterations: p50 5007.73 ms, p99 5008.98 ms, max 5008.98 ms, mean 5005.982 ms (first call median 4997.74 ms)

## 5 Memory and recovery
peak RSS of the ENN process 16932 kB; MemAvailable before/after 5940024/5939700 kB; new kernel-log alarms: 0
canary conv before/after: same output (canary 81a2bde73ce3dded -> 81a2bde73ce3dded; candidate re-run identical)
