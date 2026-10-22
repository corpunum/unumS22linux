# NPU acceptance: v11 -> REJECTED

- model sha256 `43375eb1ca46c314`, graph rules on, run 2026-10-22T16:48:35Z
- reasons:
  - output output: 175699 of 176640 values differ by more than 1 (max |delta| 128)
  - NPU output is constant while the CPU reference is not (20 cases, e.g. random00/output)
  - execution stall: max 5011 ms (> 1000 ms; the known failure mode is ~4300 ms with all-zero output)

## 1 Graph
ops: CONV_2D x1

## 2 Compile
ok=True partitions=1 nnc=90744 bytes sections={'DSP_BINARY': False, 'NPU_BINARY': True} time=1.5s

## 3 NPU vs CPU reference (20 inputs: 20 random, 0 edge, 0 real)
- `output` tol 1: max |delta| 128, 304/176640 exact, histogram 0:304, 1:637, 2:652, 3:624, 4:684, 5:674, 6:687, 7:664 -> FAIL
- `output` worst inputs: random00 (128), random01 (128), random02 (128)

## 4 Determinism and latency
determinism: ok - 3 inputs x (cold vs 5-iteration run) and a repeat process: all bit-identical [stalled: only 3 inputs x 5 iterations run]
latency over 15 iterations: p50 5009.07 ms, p99 5010.78 ms, max 5010.78 ms, mean 5006.745 ms (first call median 4998.09 ms)

## 5 Memory and recovery
peak RSS of the ENN process 17048 kB; MemAvailable before/after 5936988/5939548 kB; new kernel-log alarms: 0
canary conv before/after: same output (canary 81a2bde73ce3dded -> 81a2bde73ce3dded; candidate re-run identical)
