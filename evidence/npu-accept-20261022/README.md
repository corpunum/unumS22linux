# NPU model acceptance harness: first results (2026-10-22)

Tool: `tools/hardware/npu/npu-accept` (logic in `npu_accept_lib.py`, host tests in `tools/hardware/test-npu-accept.py`).
Test models come from `tools/hardware/npu/nnapi/tflite-make-conv.py` (own int8 conv, 1-D conv sweep, seed 7) and the public ARM ML-zoo KWS models.
Each directory holds `verdict.json` (machine verdict) and `report.md`. Compiled `.nnc` files and vendor libraries are not in git.

Default run: 20 random inputs (seed 1234), int8 tolerance max |delta| <= 1, 200-iteration repeat run per input, canary conv before/after.
The CPU reference is TFLite BUILTIN_REF kernels run on the rig (the phone has no TFLite python); the NPU runs on the phone.
The on-phone `enn-run` reports p50/p99/max (not p95), so p99 is shown.

| Model | Graph rules | Verdict | Why |
|---|---|---|---|
| `own_int8_conv3x3` (3x3, 8->16, 32x32) | on | ACCEPTED | max \|d\| 1, 99.2% exact, p50 0.32 ms |
| `kws_micronet_l` (whole graph) | on | ACCEPTED | max \|d\| 1, top-1 20/20, p50 0.47 ms |
| `ds_cnn_l_body_split` (conv body, FC+softmax cut off) | on | ACCEPTED | max \|d\| 1, p50 0.34 ms |
| `ds_cnn_l_full_unsplit` | on | REJECTED before compile | FULLY_CONNECTED after AVERAGE_POOL crashes the compiler |
| `k17_rules_on` / `k32_rules_on` (1x17, 1x32 conv) | on | REJECTED before compile | graph rule `kernel-1d-max-15` |
| `k17_rules_off` | off | REJECTED | compiled; 99.4% of values off by >1, output constant, 5 s stall per call |
| `k32_rules_off` | off | REJECTED | compiled here (no compile error); same numeric failure and stall |
| `k7`, `k9` (1x7, 1x9) | on | ACCEPTED | max \|d\| 1 |
| `k11`, `k15`, `k15c32`, `v11`, `v15` | on (warning only) | REJECTED | numeric: wrong output + 5 s stall |

Findings worth knowing:
- The 17-wide and 32-wide cases are caught by the numeric check even with the graph rule off, so the rule is a fast pre-filter, not the safety net.
- In this model family (1x148 input, int8 per-tensor weights, fused ReLU, 100->64 channels), the NPU already fails at K=11 and K=15, not only from K=17 (K=9 and K=7 pass; K=15 also fails with 32->32 channels and VALID padding). That is narrower than the "K <= 15 is correct" in issue #64, so the graph rule now warns for 1-D widths 10..15 and leaves the decision to the numeric check.
- With `--edge` (saturated all-max input) MicroNet-L has one value off by 2 out of 276 (`kws_micronet_l_edge`), deterministic. Edge inputs are therefore opt-in.
- Compile and execution never produced a DSP_BINARY, a kernel-log alarm, or a changed canary checksum; peak ENN process RSS about 17 MB.
