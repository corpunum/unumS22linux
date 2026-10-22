# NPU acceptance: kws_micronet_l_edge -> REJECTED

- model sha256 `15723bcc7d735995`, graph rules on, run 2026-10-22T16:56:01Z
- reasons:
  - output Identity: 1 of 276 values differ by more than 1 (max |delta| 2)

## 1 Graph
ops: AVERAGE_POOL_2D x1, CONV_2D x9, DEPTHWISE_CONV_2D x7, RESHAPE x1

## 2 Compile
ok=True partitions=1 nnc=721704 bytes sections={'DSP_BINARY': False, 'NPU_BINARY': True} time=2.4s

## 3 NPU vs CPU reference (23 inputs: 20 random, 3 edge, 0 real)
- `Identity` tol 1: max |delta| 2, 186/276 exact, histogram 0:186, 1:89, 2:1 -> FAIL
- `Identity` worst inputs: edge_max (2), random00 (1), random01 (1)
- top-1 agreement: 23/23

## 4 Determinism and latency
determinism: ok - 23 inputs x (cold vs 200-iteration run) and a repeat process: all bit-identical
latency over 4600 iterations: p50 0.51 ms, p99 1.39 ms, max 2.25 ms, mean 0.625 ms (first call median 1.03 ms)

## 5 Memory and recovery
peak RSS of the ENN process 17196 kB; MemAvailable before/after 5938684/5940028 kB; new kernel-log alarms: 0
canary conv before/after: same output (canary 81a2bde73ce3dded -> 81a2bde73ce3dded; candidate re-run identical)
