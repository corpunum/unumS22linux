# S22 physical-button readiness — 2026-10-02

## Result and scope

The host tools now distinguish input-node enumeration from advertised key
capabilities, observed kernel press/release/repeat sequences, and compositor or
audio effects. Inventory and event analysis are source-only/host-tested. No
phone, SSH, ADB, device socket, event injection, package operation, service,
kernel build, or device-state change was performed by this worker.

No physical press/release behavior is accepted yet. A coordinator supplied a
bounded read-only sysfs and compositor snapshot; it contains no physical key
events. The remaining hardware check is one owner-confirmed short press and
release of each of POWER, VOLUMEUP, and VOLUMEDOWN during one bounded capture.

## Capability bitmap decoding

The pinned source is LineageOS `android_kernel_samsung_s5e9925` at
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`; the inspected clean derived tree
is `3fca50941422439b2019db2e4a3dc1016b2138a1`.
`drivers/input/input.c` formats `capabilities/ev` and `capabilities/key` with
`input_print_bitmap()`. It prints native `unsigned long` words, most-significant
word first, separated by spaces; zero high words are omitted. Bit 0 is the
least-significant bit of the final word. Words are unpadded hexadecimal and
have no `0x` prefix. The live target reports `sizeof(unsigned long) = 64`, so
the parser uses 64-bit words there. The parser records this width and rejects
malformed, non-canonical, missing, or out-of-range capability data.

This is the input driver's sysfs format, not the comma-separated 32-bit chunks
used by the generic kernel `%pb` formatter. On the live target:

| Event node | Name | Raw `capabilities/ev` | Raw `capabilities/key` | Decoded target keys |
| --- | --- | --- | --- | --- |
| `event0` | `gpio_keys` | `3` | `8000000000000 0` | `KEY_VOLUMEUP` (115) |
| `event1` | `sec-pmic-key` | `3` | `14000000000000 0` | `KEY_VOLUMEDOWN` (114), `KEY_POWER` (116) |

For example, `0x8000000000000` is bit 51 in the second 64-bit word, hence key
code `64 + 51 = 115`. `0x14000000000000` sets bits 50 and 52 in that word,
codes 114 and 116. `event7` is `sec_touchscreen`; its advertised key bitmap
does not contain any of the three target codes and it is excluded from event
collection. A name alone never supplies a capability or acceptance claim.

The snapshot also reported `event0` and `event1` as character nodes, mode
`0660`, owner `root`, group `23`, with wakeup enabled. The event-node
major/minor pairs were not included in that supplied snapshot. At collection
time the tool reads the sysfs `dev` value, checks the node type and device
number, then checks the opened descriptor again with `fstat`; a missing or
mismatched identity is not opened or accepted.

## Collector and analyzer

The inventory-only command does not open input nodes:

```sh
python3 tools/hardware/button-event-evidence.py
```

An explicit bounded collection uses read-only, nonblocking evdev descriptors:

```sh
python3 tools/hardware/button-event-evidence.py --seconds 10
```

`--seconds` is limited to 300 seconds and collection defaults to 4,096 raw
`input_event` records (configurable up to 100,000). Only sysfs-verified event
nodes advertising EV_KEY plus at least one of POWER, VOLUMEUP, or VOLUMEDOWN
are eligible. Collection retains only those target EV_KEY records and the
SYN_REPORT/SYN_DROPPED integrity records; letters, passwords, touch data, and
other arbitrary key codes are discarded. The collector uses no ioctl, writes,
injection, or EVIOCGRAB. EOF, permission/open failures, device-number mismatch,
read errors, a partial record, an unterminated frame, SYN_DROPPED, or the event
limit makes the affected evidence incomplete.

An offline JSON analyzer is available with `--analyze CAPTURE.json`. It does
not open event nodes. A press is EV_KEY value 1, release is value 0, and repeat
is value 2. A press/release result requires ordered transitions on the same
capability-verified node, committed by SYN_REPORT frames. A repeat by itself,
a release without a prior press, a press without a release, and no matching
events have distinct results. After SYN_DROPPED, events are ignored through
the next SYN_REPORT. Because the collector does not query device state to
reconstruct lost transitions, that node remains incomplete. A no-event window
means only that no matching event was observed in that interval.

`input-power-readiness.py --events N` delegates to this same candidate-only
collector; its inventory still reports capability and node identity status
separately. Capability bits establish configured support, not a physical
press. An observed evdev sequence establishes kernel event behavior, not that
a human caused it or that a built-in handset switch was the source. Compositor
dispatch and audio change are outside the collector's measurements.

## Source and live integration diagnosis

At pinned source, `drivers/input/keyboard/s2mps25-key.c` registers the
`sec-pmic-key` input device and reports the selected EV_KEY code and
`input_sync()` from its key work path. `drivers/input/keyboard/gpio_keys.c`
registers configured key capabilities and reports GPIO state followed by
`input_sync()`. This explains how the live nodes can expose the advertised
codes; source behavior does not prove that a handset switch generated an
event. The code maps used by the host inventory are POWER=116, VOLUMEUP=115,
and VOLUMEDOWN=114.

Coordinator-provided read-only compositor evidence at 2026-10-02 11:34 UTC
reported Hyprland 0.56.2 (`efb50993780079460b0cbed1363e2166a2de1d9f`), the
real `gpio_keys` and `sec-pmic-key` devices, and one relevant bind:
`XF86PowerOff` to the already-deployed Lua DPMS toggle. The installed Lua
source hash was
`ed9dff6bf6775a942df85c81c0f46f8c9c201091d5a49c54a8022de4242f877d`.
The display was enabled (`dpmsStatus=true`). The deployed Lua source contains
no XF86Audio raise/lower binding. The standalone DPMS daemon remains
undeployed and must not be run alongside the Lua binding. A controlled DPMS
dispatcher exercise and its restoration are separate evidence from a physical
power-key event.

Short-press display DPMS/wake is distinct from Linux suspend/resume,
long-press handling, and cold-power-on boot selection. This work changes none
of those paths. The portable-assistant goal and current input inventory do not
establish unplugged or cold-start permanence; no boot or power-cycle test was
performed.

Volume action is not integrated. The snapshot found the `pactl` executable,
but no active PulseAudio, PipeWire, WirePlumber, or pipewire-pulse endpoint;
`wpctl`, `amixer`, `omarchy-adjust-volume`, and `omarchy-volume` were absent.
There is therefore no evidenced working audio backend for a volume binding.
Adding a compositor bind without first identifying a working audio route
would not fix volume behavior. Any future config change needs expanded file
ownership after that route is established.

## Remaining owner-assisted check

After the coordinator makes this exact reviewed collector available in the
native environment and confirms the installed file hash, run one 15-second
capture while the owner performs one short press and full release of each
physical POWER, VOLUMEUP, and VOLUMEDOWN key, one at a time. Do not hold a key
to seek repeat events and do not inject synthetic events. Inspect the JSON for
the matching event path and codes 116, 115, and 114; each short press should
have a value-1/value-0 pair in complete SYN_REPORT frames, with no dropped or
partial evidence. Repeat value 2 is optional and is not required for a short
press.

Exact collection command after that reviewed file is available:

```sh
python3 tools/hardware/button-event-evidence.py --seconds 15
```

This event check does not test a compositor action. The power-key DPMS effect
must be checked separately through the existing Lua binding with display
restoration confirmed. Volume-effect acceptance remains blocked on a working
audio route and a later authorized config change. No such owner-assisted press
test or live event collection has occurred in this work item.

## Host collector-path regression scope

The event-evidence tests now also execute `collect()` through three bounded
syscall-path failures using a fake capability inventory, a temporary file
opened read-only, and a mocked `fstat` identifying it as the expected
character-device number. Controlled `read`, `select`, and host time are used;
no real event device is opened. They exercise a read error and descriptor
cleanup, a partial record followed by EOF, and exhaustion of the raw-record
budget where the final counted record is a discarded private key code. The
budget case preserves the earlier complete POWER press/release frames for
diagnosis but requires the overall status to be incomplete and confirms that
the private key event payload is absent from output. These are deterministic
host regressions, not evidence about live evdev behavior or physical buttons.
