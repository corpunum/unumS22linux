# NPU acceptance: kws_micronet_l -> ACCEPTED

- model sha256 `15723bcc7d735995`, graph rules on, run 2026-10-22T16:35:11Z

## 1 Graph
ops: AVERAGE_POOL_2D x1, CONV_2D x9, DEPTHWISE_CONV_2D x7, RESHAPE x1

## 2 Compile
ok=True partitions=1 nnc=721704 bytes sections={'DSP_BINARY': False, 'NPU_BINARY': True} time=2.4s

## 3 NPU vs CPU reference (20 inputs: 20 random, 0 edge, 0 real)
- `Identity` tol 1: max |delta| 1, 159/240 exact, histogram 0:159, 1:81 -> ok
- top-1 agreement: 20/20

## 4 Determinism and latency
determinism: ok - 20 inputs x (cold vs 200-iteration run) and a repeat process: all bit-identical
latency over 4000 iterations: p50 0.47 ms, p99 1.39 ms, max 2.47 ms, mean 0.601 ms (first call median 0.89 ms)

## 5 Memory and recovery
peak RSS of the ENN process 17156 kB; MemAvailable before/after 5942040/5939532 kB; new kernel-log alarms: 0
canary conv before/after: same output (canary 81a2bde73ce3dded -> 81a2bde73ce3dded; candidate re-run identical)
