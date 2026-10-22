# NPU acceptance: k17_rules_on -> REJECTED

- model sha256 `daa740c5cc88c648`, graph rules on, run 2026-10-22T16:38:52Z
- reasons:
  - graph rule kernel-1d-max-15: op 0 CONV_2D has a 1-D kernel 1x17 (width 17 > 15): silently miscompiles on the NPU (K=17 wrong output, K=32 compile fails; issue #64)

## 1 Graph
ops: CONV_2D x1
- error `kernel-1d-max-15`: op 0 CONV_2D has a 1-D kernel 1x17 (width 17 > 15): silently miscompiles on the NPU (K=17 wrong output, K=32 compile fails; issue #64)
