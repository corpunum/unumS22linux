# NPU mailbox diagnostic walk bounds — 2026-10-02

## Result

Prepared an optional, one-file patch that makes the mailbox diagnostic walker
reject malformed ring metadata instead of following an invalid or
non-progressing pointer indefinitely. The regression compiles the actual
extracted v9 mailbox C at `-O0` and `-O2`, reproduces the baseline loop, then
checks the patched path alongside producer-generated valid records, wrap
padding, and `MESSAGE_MARK` handling. This is host-only C evidence; it is not a
kernel build, mailbox-runtime, firmware, or device result. The patch remains
separate from the six-patch profile and the prior missing-callback patch.

## Actual callback and failure path

The pinned s5e9925 defconfig selects mailbox protocol v9, command ABI v10,
`CONFIG_NPU_USE_BOOT_IOCTL=y`, `CONFIG_DSP_USE_VS4L=y`, and leaves
`CONFIG_NPU_USE_MAILBOX_GROUP` unset. The message dispatcher selects
`mailbox_msg_v10.h` for command version 10. The exact v10 `struct message` and
`struct command` are each 24 bytes. The v9 mailbox header configures four
128-KiB rings and two 256-KiB rings; the C harness uses a scaled 256-byte
power-of-two byte arena while retaining the exact production structs and
record sizes.

The synchronous call path is:

1. `npu-interface.c` registers `nw_req_manager` as `nw_post_request`; the
   protodrv mailbox adapter invokes that function pointer synchronously.
2. `nw_req_manager` builds the message and command, calls `npu_set_cmd`, and
   returns on its error path if publication fails. Only after success does it
   call `mbx_ipc_print_dbg` before returning `TRUE`.
3. `npu_set_cmd` calls `mbx_ipc_put` first, then `__send_interrupt`. With
   mailbox groups disabled, the interrupt helper polls the configured status
   bit with a finite decrementing counter. On timeout it calls
   `dbg_dump_mbox()` and returns `-EWOULDBLOCK`; `dbg_dump_mbox()` calls the
   same mailbox print helper for its response/request rings.
4. Both the successful synchronous debug print and timeout dump reach
   `__mbx_ipc_print` in `mailbox_ipc.c`.

The original walker checked only `msg.length`, copied a command, then advanced
with `rptr = msg.data + msg.length`. Neither the normal nor `MESSAGE_MARK`
branch checked that this result was in the unread range or greater than the
old `rptr`. With `rptr=0x10`, `wptr=0x40`, a valid 24-byte command length, and
stale `msg.data=0xfffffff8`, 32-bit addition yields `next_rptr=0x10`; the
`while (rptr < wptr)` loop repeats. The exact C regression observes repeated
header and command copies until a host-only watchdog interrupts it. This is a
constructed malformed/stale record, not evidence that normal publication
produces it or that it occurred on a device. The host arena includes spare
bytes beyond the scaled ring so the baseline's corrupt copy remains within the
host allocation; the patched path rejects the out-of-range pointer before
calling the command copy helper.

The source functions in this successful callback and timeout dump do not
acquire `interface.lock`; the patch adds no lock and makes no broader claim
about locks held by every outer caller. The diagnostic loop runs even when
debug output macros compile to no-ops: its loop and pointer assignments are
ordinary C control flow outside the logging calls.

## Patch and regression boundary

The patch changes only `__mbx_ipc_print`. It validates a nonzero power-of-two
segment, monotonic 32-bit `rptr`/`wptr`, occupancy no larger than one segment,
a complete readable message header, the exact command length, and a data
pointer after the header and wholly within the unread range. Padding before a
wrapped command remains permitted. It derives a maximum walk count from the
minimum 24-byte header plus 24-byte command record size, and explicitly
requires the next pointer to advance and stay no later than `wptr`. The
`MESSAGE_MARK` path uses that same validated next pointer and still skips
printing the marked record. Since the production header copy uses volatile
`u32` reads, misaligned ring pointers are rejected before copying; misaligned
command pointers are also rejected. The diagnostic is read-only; it does not
change mailbox publication, consumer pointers, IRQ handling, or request
ownership.

The source uses unsigned 32-bit monotonic counters and its existing traversal
already relies on `rptr < wptr`. This patch does not introduce modular
sequence-number handling: if those counters cross `2^32` so `wptr < rptr`, it
fails closed and reports invalid ring state. A regression exercises that
case. Handling counter rollover as a valid queue remains outside this patch.

`test-npu-mailbox-debug-walk-bounds.py` compiles the SHA-verified production
message/command declarations, ring-copy helpers, `mbx_ipc_put`, MARK helpers,
and baseline/patched print functions. Its shims provide only host logging,
byte storage, and a read-count watchdog; they do not emulate Linux locking,
MMIO, IRQs, firmware, or scheduling. Cases cover baseline non-progress,
empty and maximum-valid scaled queues, normal and marked messages, a physically
wrapped header, producer-inserted command padding at the segment boundary,
truncated headers/commands, unaligned pointers, bad lengths/data pointers,
invalid ring bounds, the explicit record budget, and 32-bit pointer rollover.
The test verifies
that mailbox publication's extracted `mbx_ipc_put` body is byte-identical
before and after applying this patch.

During draft validation, the first host unit used the v9 message header because
the mailbox protocol is v9. The preserved target defconfig independently
selects command version 10, so the dispatcher actually uses
`mailbox_msg_v10.h` (`MESSAGE_MARK=0xDEADC0DE`, not the v9 value). That draft
run was discarded; the final manifest pins the dispatcher and v10 header, and
all results below are from the corrected ABI.

## Pinned inputs and verification

Pinned base: `4e5c5ad7d950e4de0688b5663965f2075654b2ad`. The explicitly
configured local source fixture was clean at
`3fca50941422439b2019db2e4a3dc1016b2138a1`; every requested file was checked
against that pinned source and its SHA-256. The no-environment run used the
bounded public SHA loader and also passed. The loader has no implicit local
fixture fallback.

SHA-256 inputs:

- `mailbox_ipc.c` — `171b039aeedd95edfb8029e572df9dc7cc335c9f9156c22b841dc0bfc6bf30d8`
- `mailbox_msg.h` — `5985d2b5ebb34eecd02c2506dbd4dbb51dbe0cf720ad450c9077a3fb50edf0ef`
- `mailbox_msg_v10.h` — `ca18870a661dce7c1bebe0c008f5c70f14eba93c63539b8cde010294df9c56f0`
- `mailbox_v9.h` — `5719acfe4ac6f366466b390a9e24cbbed0af5461ab7dcff2e1a6e41f44457650`
- `npu-interface.c` — `c2deaa0abd990184b64373bb13983f048b925e0f5f421f6623de7de85f667108`
- `npu-if-protodrv-mbox2.c` — `d13247bd90ab27dd58cfe9241ec1a07460ab3b353d86a268f8a6fadefc6ff755`
- `s5e9925_defconfig` — `de87dbdff5a4082b2aa6fd511a69b9766ddfb0369738c76885c9766178f2b4f5`

The explicit local-source route passed under normal Python, `python3 -O`, and
`PYTHONOPTIMIZE=1`; the public SHA-loader route independently passed under all
three modes with the source-tree variable unset. Every one of those six runs
compiled and executed the extracted C at `-O0` and `-O2` with
`-Wall -Wextra -Werror`. The one explicit warning
exception is `-Wno-unused-parameter` for the pinned `__get_readable_size`
helper's unused `sgmt_len` argument. No kernel build was run for this optional
patch.

Patch SHA-256:
`20c700bfa11f13836c76c88cca28a4f8dfa459e5cf146292a814880cd5850b29`.
Test SHA-256:
`ece401e51f8ecac75cb5293068df9d2b1c784f5e1e79753e6c741959ffecb9a2`.

## Remaining limits

This patch bounds one synchronous diagnostic loop; it does not prove mailbox
publication or callback completion is bounded. In particular,
`npu_power_wait_cancel_and_drain()` still uses the unbounded
`wait_for_completion(&waiter->publish_done)` while a publisher is outstanding.
No timeout-and-free shortcut is safe while a publisher/callback may still
reference request storage, and this work does not establish a cancellation
boundary for a genuinely non-returning publication call. The malformed ring
case is synthetic, and valid records are tested with exact ABI sizes but a
scaled host ring. Kernel locks, real mailbox memory ordering, hardware,
firmware, BOOTUP, runtime, and device acceptance remain unproven.
