# NPU acceptance: ds_cnn_l_full_unsplit -> REJECTED

- model sha256 `fc40799238db5112`, graph rules on, run 2026-10-22T16:35:32Z
- reasons:
  - graph rule fc-after-avgpool: op 14 FULLY_CONNECTED follows AVERAGE_POOL_2D: crashes the on-device compiler (SIGSEGV in the IR builder); cut the model before the pool/FC tail (tflite-cut.py)

## 1 Graph
ops: AVERAGE_POOL_2D x1, CONV_2D x6, DEPTHWISE_CONV_2D x5, FULLY_CONNECTED x1, RESHAPE x2, SOFTMAX x1
- warning `leading-reshape`: first op is RESHAPE; refused by the stock NpuSkip gate (the patched gates are used)
- error `fc-after-avgpool`: op 14 FULLY_CONNECTED follows AVERAGE_POOL_2D: crashes the on-device compiler (SIGSEGV in the IR builder); cut the model before the pool/FC tail (tflite-cut.py)
