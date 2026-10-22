# NPU acceptance: k11 -> REJECTED

- model sha256 `793a220b2dc6999c`, graph rules on, run 2026-10-22T16:52:00Z
- reasons:
  - output output: 47116 of 47360 values differ by more than 1 (max |delta| 128)
  - NPU output is constant while the CPU reference is not (5 cases, e.g. random00/output)
  - execution stall: max 5013 ms (> 1000 ms; the known failure mode is ~4300 ms with all-zero output)

## 1 Graph
ops: CONV_2D x1

## 2 Compile
ok=True partitions=1 nnc=90744 bytes sections={'DSP_BINARY': False, 'NPU_BINARY': True} time=1.5s

## 3 NPU vs CPU reference (5 inputs: 5 random, 0 edge, 0 real)
- `output` tol 1: max |delta| 128, 81/47360 exact, histogram 0:81, 1:163, 2:194, 3:159, 4:180, 5:172, 6:196, 7:189 -> FAIL
- `output` worst inputs: random00 (128), random01 (128), random02 (128)

## 4 Determinism and latency
determinism: ok - 3 inputs x (cold vs 5-iteration run) and a repeat process: all bit-identical [stalled: only 3 inputs x 5 iterations run]
latency over 15 iterations: p50 5010.44 ms, p99 5012.66 ms, max 5012.66 ms, mean 5009.232 ms (first call median 5004.05 ms)

## 5 Memory and recovery
peak RSS of the ENN process 17012 kB; MemAvailable before/after 5940452/5939632 kB; new kernel-log alarms: 0
canary conv before/after: same output (canary 81a2bde73ce3dded -> 81a2bde73ce3dded; candidate re-run identical)
