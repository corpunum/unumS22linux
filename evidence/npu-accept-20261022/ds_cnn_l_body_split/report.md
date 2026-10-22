# NPU acceptance: ds_cnn_l_body_split -> ACCEPTED

- model sha256 `2a6f10a33ecf5db0`, graph rules on, run 2026-10-22T16:35:22Z

## 1 Graph
ops: AVERAGE_POOL_2D x1, CONV_2D x6, DEPTHWISE_CONV_2D x5

## 2 Compile
ok=True partitions=1 nnc=579184 bytes sections={'DSP_BINARY': False, 'NPU_BINARY': True} time=2.2s

## 3 NPU vs CPU reference (20 inputs: 20 random, 0 edge, 0 real)
- `model/average_pooling2d/AvgPool` tol 1: max |delta| 1, 4942/5520 exact, histogram 0:4942, 1:578 -> ok
- top-1 agreement: 19/20

## 4 Determinism and latency
determinism: ok - 20 inputs x (cold vs 200-iteration run) and a repeat process: all bit-identical
latency over 4000 iterations: p50 0.34 ms, p99 1.28 ms, max 1.33 ms, mean 0.425 ms (first call median 0.91 ms)

## 5 Memory and recovery
peak RSS of the ENN process 17180 kB; MemAvailable before/after 5941488/5939912 kB; new kernel-log alarms: 0
canary conv before/after: same output (canary 81a2bde73ce3dded -> 81a2bde73ce3dded; candidate re-run identical)
