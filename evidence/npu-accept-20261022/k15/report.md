# NPU acceptance: k15 -> REJECTED

- model sha256 `5b61d3a9a9fe2896`, graph rules on, run 2026-10-22T16:35:41Z
- reasons:
  - output output: 188375 of 189440 values differ by more than 1 (max |delta| 128)
  - NPU output is constant while the CPU reference is not (20 cases, e.g. random00/output)
  - execution stall: max 5008 ms (> 1000 ms; the known failure mode is ~4300 ms with all-zero output)

## 1 Graph
ops: CONV_2D x1

## 2 Compile
ok=True partitions=1 nnc=107128 bytes sections={'DSP_BINARY': False, 'NPU_BINARY': True} time=1.5s

## 3 NPU vs CPU reference (20 inputs: 20 random, 0 edge, 0 real)
- `output` tol 1: max |delta| 128, 367/189440 exact, histogram 0:367, 1:698, 2:675, 3:698, 4:759, 5:694, 6:735, 7:759 -> FAIL
- `output` worst inputs: random00 (128), random01 (128), random02 (128)

## 4 Determinism and latency
determinism: ok - 3 inputs x (cold vs 5-iteration run) and a repeat process: all bit-identical [stalled: only 3 inputs x 5 iterations run]
latency over 15 iterations: p50 5006.32 ms, p99 5007.57 ms, max 5007.57 ms, mean 5004.422 ms (first call median 4996.60 ms)

## 5 Memory and recovery
peak RSS of the ENN process 16964 kB; MemAvailable before/after 5939116/5937528 kB; new kernel-log alarms: 0
canary conv before/after: same output (canary 81a2bde73ce3dded -> 81a2bde73ce3dded; candidate re-run identical)
