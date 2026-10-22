# NPU acceptance: k32_rules_off -> REJECTED

- model sha256 `ebf822b08362f0c7`, graph rules off, run 2026-10-22T16:42:03Z
- reasons:
  - output output: 188320 of 189440 values differ by more than 1 (max |delta| 128)
  - NPU output is constant while the CPU reference is not (20 cases, e.g. random00/output)
  - execution stall: max 5008 ms (> 1000 ms; the known failure mode is ~4300 ms with all-zero output)
- graph errors ignored (rules off): kernel-1d-max-15

## 1 Graph
ops: CONV_2D x1
- error `kernel-1d-max-15`: op 0 CONV_2D has a 1-D kernel 1x32 (width 32 > 15): silently miscompiles on the NPU (K=17 wrong output, K=32 compile fails; issue #64)

## 2 Compile
ok=True partitions=1 nnc=221816 bytes sections={'DSP_BINARY': False, 'NPU_BINARY': True} time=1.6s

## 3 NPU vs CPU reference (20 inputs: 20 random, 0 edge, 0 real)
- `output` tol 1: max |delta| 128, 354/189440 exact, histogram 0:354, 1:766, 2:683, 3:663, 4:719, 5:716, 6:718, 7:709 -> FAIL
- `output` worst inputs: random00 (128), random01 (128), random02 (128)

## 4 Determinism and latency
determinism: ok - 3 inputs x (cold vs 5-iteration run) and a repeat process: all bit-identical [stalled: only 3 inputs x 5 iterations run]
latency over 15 iterations: p50 5006.78 ms, p99 5007.66 ms, max 5007.66 ms, mean 5005.515 ms (first call median 4998.47 ms)

## 5 Memory and recovery
peak RSS of the ENN process 17064 kB; MemAvailable before/after 5933464/5935272 kB; new kernel-log alarms: 0
canary conv before/after: same output (canary 81a2bde73ce3dded -> 81a2bde73ce3dded; candidate re-run identical)
