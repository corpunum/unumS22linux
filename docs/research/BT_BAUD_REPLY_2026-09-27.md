# QCA `0xfc48` baud reply: framing resolved, command result unresolved

This host-only follow-up inspected the baud probe source, the existing
sanitized command-receipt note, the persisted H4 capture, and read-only pinned
kernel/HAL research. It made no device calls and does not amend the existing
probe or trial evidence.

## What the capture establishes

The persisted trial output and source-matched capture loop establish one
complete H4 Command Complete for opcode `0xfc48`, with a four-octet parameter
area: one command-credit octet, the two-octet little-endian opcode, and exactly
one return parameter. The capture loop reads the H4 event header and declared
length and deliberately labels the event/payload `UNKNOWN`; it does not parse
the last byte as a status. The earlier receipt's status label is therefore an
interpretation, not a field decoded by that capture loop. To keep private
capture bytes out of this commit, the report omits the observed return-octet
value; test fixtures below are generated synthetic packets.

## What the source establishes

The pinned kernel's generic HCI Command Complete handler loads the first byte
after the command opcode into its `status` variable. Its QCA baud routine emits
opcode `0xfc48`, uses the QCA baud enum, waits for the controller to process
the request, and then changes the host UART speed; it does not wait for or
validate a Command Complete response. The QCA enum assigns `0x0e` to 3,000,000
baud. This matches the command sent by the probe.

The generic interpretation of return byte `0x01` is the standard HCI status
“Unknown HCI Command.” The Bluetooth Core Specification says an unsupported
command returns status `0x01` in either Command Complete or Command Status.
Current Linux source describes the first-return-byte rule for otherwise
unknown opcodes as an assumption for vendor commands and explicitly allows
that a vendor may not follow it. The QCA baud sender has no command-specific
return parser that would settle the ambiguity. The available HAL audit records
a stateful vendor-event path but no standalone `0xfc48` response contract.

An older public Qualcomm Rome HAL does define `BAUDRATE_CHANGE_SUCCESS` as
`1`, but for a different field: after sending FC48 and changing the local
UART, it reads and validates a separate HCI Vendor Specific Event with
response code `0x92`, then waits for a later Command Complete. In that source, the
Command Complete is only checked for successful reading; its return parameter
is not compared with or translated from the vendor-event result. This confirms
that `1` can mean success in the Rome VSE profile, but does not map the
captured one-byte Command Complete field. The source is an older Rome path, not
an exact QCA6490 unified-format contract.

The local recovered QTI HAL audit likewise finds a baud request, host baud
change, delay, and flow restoration, but no field-level interpretation for
this Command Complete return byte. The current public Linux QCA UART path also
does not parse this command's Command Complete result; for WCN3990 it handles
the baud-change vendor event separately. Neither source supplies the missing
translation for the captured profile.

Read-only inspection of the recovered QTI HAL event handler is consistent
with that limit: it consumes an opcode-matched FC48 Command Complete and reads
onward without testing that event's return parameter. Its separate stateful
vendor-event path recognizes response code `0x92` and checks a response field
against `1`. That establishes a result rule for the separate event path, not a
translation or meaning for the Command Complete return byte.

So the evidence supports both of these statements, with different confidence:

- The pinned Linux HCI core interprets `0x01` as status `Unknown HCI Command`.
- The controller's `0xfc48`-specific schema is not established here; a
  vendor-defined return byte with value `0x01` cannot be excluded.

The later successful patch-identity exchange at the new host baud does not
resolve the meaning of this earlier return parameter and is not used to waive
it.

## Fail-closed parser

`tools/hardware/bt-baud-reply.py` checks the H4 event boundary, Command
Complete event code, opcode, one command credit, and the observed single-byte
return layout. For the captured shape it reports the conditional generic HCI
status candidate while setting `status_semantics_confirmed=false` and
`decision=REFUSE`. A zero byte is also refused; it is not promoted to success.
Different return lengths/credit counts are reported as unresolved layouts.

Example (the octets are a synthetic protocol fixture matching the sanitized
observed schema):

```sh
python3 tools/hardware/bt-baud-reply.py '04 0e 04 01 48 fc 7f'
```

The tool exits `3` for a structurally valid but unresolved response and `2`
for invalid or unexpected framing. It reads no device, private capture, or
firmware file.

Host-only negative regressions are in
`tools/hardware/test-bt-baud-reply.py`. They ensure the observed byte is not
silently accepted, the synthetic `0x00` candidate is not treated as confirmed
success, and malformed, wrong-opcode, or unobserved layouts are refused.

## Sources and remaining evidence

- Pinned source inspected read-only: `lineage/android_kernel_samsung_s5e9925/drivers/bluetooth/hci_qca.c`, `drivers/bluetooth/btqca.h`, and `net/bluetooth/hci_event.c`. The local QCA UART driver is not itself evidence that its transport binds to the target QCA6490 device-tree path.
- Public [Android kernel QCA UART driver at a pinned revision](https://android.googlesource.com/kernel/common/+/03329f99/drivers/bluetooth/hci_qca.c) shows the `0xfc48` baud request and the send/wait behavior.
- Public [QCA baud enum](https://android.googlesource.com/kernel/common/+/refs/tags/android14-5.15-2023-08_r11/drivers/bluetooth/btqca.h) assigns 3,000,000 baud enum value `0x0e`.
- Public [Linux HCI Command Complete handling](https://android.googlesource.com/kernel/common/+/a0ee3c4886654c2ea89dabd042a8ada8df500100/net/bluetooth/hci_event.c) documents that first-byte status treatment for otherwise unknown/vendor opcodes is an assumption.
- The [Bluetooth Core Specification, HCI Command Complete event](https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/Core-61/out/en/host-controller-interface/host-controller-interface-functional-specification.html) defines the status for unsupported commands as `0x01`.
- Public [Qualcomm Rome HAL baud handling](https://android.googlesource.com/platform/hardware/qcom/bt/+/ea15d9e/msm8996/libbt-vendor/src/hw_rome.c) checks the `0x92` vendor event's baud result before separately waiting for Command Complete; the [public QCOM header history](https://android.googlesource.com/platform/hardware/qcom/bt/%2B/39550b5b9eed5cd83d669f704d11a2574f5cbffd%5E2..39550b5b9eed5cd83d669f704d11a2574f5cbffd/) defines the legacy success constant as `1`.
- Public [current Android Linux QCA UART driver](https://android.googlesource.com/kernel/common/+/refs/tags/android15-6.6-2025-07_r5/drivers/bluetooth/hci_qca.c) sends FC48 and changes host baud after a delay; it does not parse the FC48 Command Complete return parameter.
- Existing local [Bluetooth hardware-reuse preflight](HARDWARE_REUSE_BT_PREFLIGHT_2026-09-21.md) records the private HAL's stateful event parsing and its limits; it does not supply a standalone baud-reply fixture.

Resolution requires an exact-profile QCA6490 `0xfc48` return-parameter
definition or another source that directly disambiguates this one-byte field.
Until then, this result remains a refusal. No repeat hardware attempt is
proposed by this host-only change.
