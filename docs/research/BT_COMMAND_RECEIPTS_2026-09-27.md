# Bluetooth command-receipt reconciliation — 2026-09-27

This is a host-only reconciliation of the persisted plain-H4 trial counters
against the included C transfer/probe sources. No device was accessed for this
analysis. The note intentionally excludes raw trace data, firmware/NVM payloads,
addresses, and controller identity strings.

## Wire counts and phases

The captured UART interval has complete H4 framing at the syscall boundaries:
no returned-byte mismatches, partial frames, or unparsed trailing bytes were
found. Splitting at the successful N_HCI line-discipline attach/detach calls
gives:

| Phase | Outbound HCI commands | Inbound Command Complete events | First return bytes |
| --- | ---: | ---: | --- |
| Before N_HCI attach | 843 | 38 | 37 bytes `0x00`; `0xfc48` byte `0x01` (vendor semantics unresolved) |
| N_HCI attached | 41 | 41 | all bytes `0x00` (standard command success) |
| Total | 884 | 79 | 78 bytes `0x00`; one byte `0x01` |

The 41/41 bridge counters describe only the attached phase; they are not the
total UART command/event counts. The three ordinary commands before attach are
the runtime reset, address-read, and local-version exchanges (`0x0c03`,
`0x1009`, `0x1001`). The attached phase has 41 ordinary command completions,
all successful. Together that is 44 ordinary commands and 44 successful
completions.

The vendor opcode `0xfc00` is used by multiple sequential transfer/probe phases
in the included sources, so its total can be reconciled without treating every
firmware segment as individually acknowledged:

| Source phase | `0xfc00` commands | `0xfc00` completions | Basis |
| --- | ---: | ---: | --- |
| RAM patch transfer | 806 | 1 | Mode 3 omits intermediate segment-event reads; one final patch acknowledgement is checked. |
| Post-patch board exchange | 1 | 1 | One board command and its checked completion. |
| NVM transfer | 29 | 29 | Per-segment completion is read and checked. |
| App/patch-version probes | 3 | 3 | QTI app version plus patch identity before and after the baud transition. |
| Total | 839 | 34 | Matches the captured opcode/return-byte counts. |

The remaining pre-attach vendor command is opcode `0xfc48`, the `baud_3m`
command in `bt-qca6490-baud-probe.c`. Its completion has first return byte
`0x01`. Generic HCI handling would interpret this as Unknown HCI Command, but
the exact vendor return schema is unresolved; that interpretation is not an
established controller error. The probe proceeds to later identity/transfer
phases, but that does not establish that the nonzero byte is harmless or that the command is
optional. Do not normalize it to success without an independent, exact-profile
explanation of the controller's behavior. The [baud reply follow-up](BT_BAUD_REPLY_2026-09-27.md)
corrects the earlier status terminology and records the separate vendor-event
schema limitation.

## Acceptance boundary

The captured return-byte counts reconcile with the code's transfer phases, including
the intentional mode-3 acknowledgement behavior. They do not prove that every
outbound command has an individual completion: the 806 mode-3 patch segments
intentionally do not. Before relying on a permanent userspace controller
service, independently resolve the `0xfc48` return schema against the exact runtime
profile and confirm the expected mode-3 acknowledgement contract. This one
attach/init/detach trial also does not establish sustained operation, restart
reliability, discovery, connection, or audio behavior; those require separately
scoped acceptance criteria and evidence.
