# QCA runtime transport profile — 2026-09-27

Host-only diagnosis and correction; this work made no device calls, probe
build, firmware transmission, or radio operation.

The coordinator's sanitized registration receipt reports that the first
kernel-generated HCI command was `0x1003` (Read Local Supported Features),
followed by three userspace IBS WAKE attempts and no ACK/event. The earlier
runtime HCI Reset `0x0c03` had completed before attachment. Host inspection of
the exact embedded runtime NVM profile found Tag 17's six-byte payload has its
IBS-enable bit (byte 0, bit 7) clear. The source field definition identifies
that bit as IBS enable. This explains the mismatch: the generic bridge emitted
IBS control bytes for a profile expecting plain H4.

The HCI probe now validates exactly one well-formed Tag 17 and selects plain
H4 only when that embedded profile has the bit clear; a set bit selects IBS.
Missing, duplicate, short, truncated, or length-inconsistent profile data
fails closed. The generic bridge and standalone CLI still default to IBS.
Plain-H4 forwarding has no WAKE/ACK path. Exhausting IBS ACK retries now
reports `ibs_wake_ack_timeout` with `ETIMEDOUT` rather than an unexplained
failure.

Host tests compile and exercise the actual profile parser using synthetic NVM
records, and exercise the embedded C bridge with fake ioctls, socketpairs, and
PTYS. All 24 tests passed with both `python3` and `python3 -O`. These tests do
not prove kernel/firmware behavior or device acceptance.

The existing AArch64 probe was built with `aarch64-linux-gnu-gcc` 13.3.0 using
`-static -std=c11 -Wall -Wextra -Werror -O2` and
`-I /home/corpunum/s22-linux/tools/hardware`. That include root supplies the
private build inputs `builds/bt-patch-20260922/qca-patch-private.h` and
`builds/bt-runtime-nvm-20260922/s22_nvm_payload.h`; their contents and hashes
are intentionally not recorded here. This source change requires a fresh,
independently reviewed probe build; the prior artifact is unchanged.

`HCIUARTSETPROTO` may queue kernel power-on and automatic controller
initialization; plain H4 selection does not suppress that. No explicit
`HCIDEVUP`, scan, advertising, connection, or pairing command is added. Kernel
line-discipline detach synchronously waits for teardown and has no overall
deadline, so a userspace timeout remains UNKNOWN, not proof of power-off or
recovery. No follow-up trial is authorized by this host change.
