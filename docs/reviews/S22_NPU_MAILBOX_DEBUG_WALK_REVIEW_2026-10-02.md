# S22 NPU mailbox debug-walk bounds: independent review — 2026-10-02

## Conclusion

I found no bound-correctness blocker in the optional diagnostic-walker patch
within the host-tested scope. It fails closed on malformed/stale ring state,
and the extracted production C regression demonstrates the prior non-progress
case stopping at its watchdog while the patched walker rejects it before the
command-copy helper. This is scaled host-C evidence only: it is not a kernel
build, mailbox-runtime result, whole-publisher bound, BOOTUP result, or device
acceptance.

The exact no-environment/public-source route was attempted once and exited 77
because the public fetch returned `Network is unreachable`. That route is
unavailable, not passed. The explicit SHA-pinned local-fixture route passed in
all three Python modes below.

## Reviewed source and pinned ABI

Reviewed author commit `ee7ae49f238d91e76bf8ace98c3f5def3f82ee6f` and
integrated commit `b63c089d417ffc692ba9038ffc171b3164ddc2bc`. Archived the exact
integrated source read-only; the target doc, patch, and test blobs match the
author commit at both revisions:

- Research note blob `12123b760e0f82001f3e6f1fc16d78627e6e8bc2`.
- Patch blob `caa89159ee33b9cae7245352885be08a11fc573f`; file SHA-256
  `20c700bfa11f13836c76c88cca28a4f8dfa459e5cf146292a814880cd5850b29`.
- Test blob `a779170969c3c942c05b9e3a48af0d31ac4c416f`; file SHA-256
  `ece401e51f8ecac75cb5293068df9d2b1c784f5e1e79753e6c741959ffecb9a2`.

The configured v9 mailbox / v10 command ABI was checked against a clean local
source fixture at `3fca50941422439b2019db2e4a3dc1016b2138a1`. The dispatcher
selects `mailbox_msg_v10.h`; `MESSAGE_MARK` is `0xDEADC0DE`; `struct message`
and `struct command` are each 24 bytes; v9 configures four 128-KiB and two
256-KiB rings. The fixture paths below are represented without host-specific
absolute paths. Each file's SHA-256 matched the test manifest:

| Source | SHA-256 |
| --- | --- |
| `mailbox_ipc.c` | `171b039aeedd95edfb8029e572df9dc7cc335c9f9156c22b841dc0bfc6bf30d8` |
| `mailbox_msg.h` dispatcher | `5985d2b5ebb34eecd02c2506dbd4dbb51dbe0cf720ad450c9077a3fb50edf0ef` |
| `mailbox_msg_v10.h` | `ca18870a661dce7c1bebe0c008f5c70f14eba93c63539b8cde010294df9c56f0` |
| `mailbox_v9.h` | `5719acfe4ac6f366466b390a9e24cbbed0af5461ab7dcff2e1a6e41f44457650` |
| `npu-interface.c` | `c2deaa0abd990184b64373bb13983f048b925e0f5f421f6623de7de85f667108` |
| `npu-if-protodrv-mbox2.c` | `d13247bd90ab27dd58cfe9241ec1a07460ab3b353d86a268f8a6fadefc6ff755` |
| `s5e9925_defconfig` | `de87dbdff5a4082b2aa6fd511a69b9766ddfb0369738c76885c9766178f2b4f5` |

The regression uses the production ABI sizes and a scaled 256-byte arena; it
does not model the configured physical ring sizes, MMIO, firmware, scheduling,
or kernel locking.

## Patch review and behavior evidence

The patch changes only `__mbx_ipc_print`. Before dereferencing the command it
requires a nonzero power-of-two segment, monotonic counters, occupancy no
larger than the segment, word-aligned logical ring pointers, a complete
header, the exact command length, and an aligned command range after the
header and wholly within the unread interval. The range checks are ordered so
the unsigned `wptr - msg.data` subtraction is reached only after
`msg.data <= wptr`. This also ensures `msg.data + msg.length` cannot wrap past
`wptr`. A separate strict-progress/in-range check documents and enforces the
walker invariant.

The walk budget is `floor(segment_length / (sizeof(message) +
sizeof(command)))`; every accepted record consumes at least that header and
command size, while wrap padding can only reduce the number of records. The
`MESSAGE_MARK` branch advances using the same validated next pointer and
continues to skip printing that record. The patch deliberately rejects
`wptr < rptr` rather than attempting modular 32-bit sequence-counter handling,
consistent with this walker's existing monotonic comparison.

The test compiles and executes the SHA-verified extracted production C at
`-O0` and `-O2` with `-Wall -Wextra -Werror` (plus the documented
`-Wno-unused-parameter` for the source helper's unused argument). In each
Python mode it exercises the baseline repeat-until-watchdog control and the
patched malformed-record rejection, plus empty and maximum-valid queues,
normal and `MESSAGE_MARK` records, a physically wrapped header,
producer-inserted command padding, malformed header/command/pointer bounds,
the walk budget, and `wptr < rptr`. The fixture route passed with:

- normal Python;
- `python3 -O`;
- `PYTHONOPTIMIZE=1`.

I also compared extracted function bodies before and after patch application.
The nine production copy/publication/reference helpers were byte-identical,
including `mbx_ipc_put`, `mbx_ipc_ref_msg`, and `mbx_ipc_clr_msg`; no helper
body changed. This confirms the regression's `mbx_ipc_put` preservation check
and rules out a publication-path edit in the patch.

The source path inspected for context is synchronous: the protodrv invokes
`nw_req_manager`, which calls `npu_set_cmd` and only reaches the success debug
print after publication succeeds. The timeout dump also calls this walker.
This establishes where this particular diagnostic loop is reached in the
inspected source; it does not prove all outer callers' lock state or runtime
behavior.

## C8 host-runner integration

Independently inspected integration commit
`f98bbcf5756f3aaade88b885dc6499d8ffcf1546`, whose parent is the reviewed
integration commit `b63c089d417ffc692ba9038ffc171b3164ddc2bc`. Its runner diff
is exactly three insertions: the reviewed test path is added to both the
reviewed-host-test and execution lists, and to the expected policy tuple. The
test blob remains `a779170969c3c942c05b9e3a48af0d31ac4c416f` (SHA-256 above).
The host-runner policy test passed 7/7 in normal Python, `-O`, and
`PYTHONOPTIMIZE=1`. These are policy-integration tests, not a full C8 suite
result. Separately, the coordinator reported that its full clean suite's C8
no-environment attempt exited 77 on a public read timeout; no public-fetch
pass is claimed here.

## Limits and residual risk

The malformed stale-pointer example is synthetic. The bounded walker does
not bound publication or callback completion. In particular,
`npu_power_wait_cancel_and_drain()` still waits without a timeout in
`wait_for_completion(&waiter->publish_done)` while a publisher is outstanding;
this patch creates no safe cancellation boundary for a non-returning
publisher. The host watchdog/shims are not evidence of the duration or
behavior of a real kernel call. No C8 kernel compile, phone operation, module
load, deployment, or runtime/BOOTUP test was performed for this review.
