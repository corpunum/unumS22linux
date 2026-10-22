# NPU acceptance: k17_rules_off -> REJECTED

- model sha256 `daa740c5cc88c648`, graph rules off, run 2026-10-22T16:38:52Z
- reasons:
  - output output: 188383 of 189440 values differ by more than 1 (max |delta| 128)
  - NPU output is constant while the CPU reference is not (20 cases, e.g. random00/output)
  - execution stall: max 5008 ms (> 1000 ms; the known failure mode is ~4300 ms with all-zero output)
- graph errors ignored (rules off): kernel-1d-max-15

## 1 Graph
ops: CONV_2D x1
- error `kernel-1d-max-15`: op 0 CONV_2D has a 1-D kernel 1x17 (width 17 > 15): silently miscompiles on the NPU (K=17 wrong output, K=32 compile fails; issue #64)

## 2 Compile
ok=True partitions=1 nnc=123512 bytes sections={'DSP_BINARY': False, 'NPU_BINARY': True} time=1.6s

## 3 NPU vs CPU reference (20 inputs: 20 random, 0 edge, 0 real)
- `output` tol 1: max |delta| 128, 368/189440 exact, histogram 0:368, 1:689, 2:718, 3:740, 4:694, 5:701, 6:743, 7:679 -> FAIL
- `output` worst inputs: random00 (128), random01 (128), random02 (128)

## 4 Determinism and latency
determinism: ok - 3 inputs x (cold vs 5-iteration run) and a repeat process: all bit-identical [stalled: only 3 inputs x 5 iterations run]
latency over 15 iterations: p50 5005.95 ms, p99 5007.97 ms, max 5007.97 ms, mean 5004.871 ms (first call median 4998.51 ms)

## 5 Memory and recovery
peak RSS of the ENN process 17028 kB; MemAvailable before/after 5938264/5933368 kB; new kernel-log alarms: 0
canary conv before/after: same output (canary 81a2bde73ce3dded -> 81a2bde73ce3dded; candidate re-run identical)
