# S22 cellular assistant readiness evidence — 2026-10-02

The portable-assistant mission requires two separate user-visible outcomes:
internet through the dedicated SIM and completed calls with two-way speech.
CPIF enumeration and a modem boot-state read are preliminary observations;
they do not satisfy either outcome.

## Implemented evidence boundary

`tools/hardware/cellular-readiness-evidence.py` is a passive, read-only
inventory for the Samsung r0s CPIF interface. It reads only:

- `/sys/bus/platform/devices/cpif/driver` to classify the exact driver link;
- `/sys/bus/platform/devices/cpif/modem_state`, capped at 128 bytes;
- fixed CPIF module names under `/sys/module`;
- `operstate` for only `rmnet0` through `rmnet7`;
- names matching `umts_*` under `/sys/class/misc`.

It does not search arbitrary `mif` substrings. This matters because unrelated
camera, devfreq, and NPU paths can contain `mif`. It does not enumerate or open
`/dev/umts_*`; class-name presence is only an endpoint-registration hint.
There is no `/proc` read, AT/QMI request, network probe, call, SMS, firmware
load, or write. A fake sysfs root can be injected with `--sysfs-root` for host
tests.

A positive `cp_interface` binding requires the driver link to resolve strictly
to an existing directory at the exact sysfs
`/sys/bus/platform/drivers/cp_interface` path. A valid driver directory in
that same namespace is reported as `bound_other`; dangling or out-of-namespace
links remain unknown. Module inventory retains the `listed` and `not_listed`
lists and now includes per-name `unknown`/`errors` details; any per-module
lookup error marks that inventory `partial`.

The parser uses the exact state strings in the pinned Samsung kernel at
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`:
`drivers/soc/samsung/cpif/modem_utils.h`'s `modem_state_string[]` and
`modem_main.c`'s `DEVICE_ATTR_RO(modem_state)`. `modem_state_show()` only reads
`mc->phone_state` and formats that token plus newline. The neighboring
`do_cp_crash` attribute is write-only and is never read or touched. In this
driver, `ONLINE` means the CP reached its CPIF online state after its boot
checks. `SIM_ATTACH` and `SIM_DETACH` are modem-state labels, not SIM presence
or network-registration results.

The report keeps SIM presence, SIM registration, a data session, forced
cellular routing, DNS and HTTPS over that route, IMS, outgoing/incoming calls,
and two-way audio as `unknown`. An observed `rmnetN` `up` state is still only a
link observation. Neither that nor CP `ONLINE` proves internet, calls, or
assistant readiness.

## Supplied point-in-time evidence

The coordinator supplied a redacted passive sample from 2026-10-02 at
11:32:57–11:36:09 UTC: `cpif` was bound to `cp_interface`, `modem_state` read
`INIT`, `rmnet0`–`rmnet7` were down, and `wlan0` was up. The hardware-free test
suite mirrors only those non-identifying fields and checks that SIM/data/IMS/
voice remain unknown. This is a time-stamped observation; it is not an
assumption about a later phone state. The older committed `STATUS.md` and
`AUDIO_CELLULAR_PREFLIGHT_2026-09-21.md` likewise record cellular as
unaccepted. Reuse the completed
`CELLULAR_CLOSURE_FOLLOWUP_2026-09-21.md`: its static dependency walk found
zero missing names across 64 ELF objects and 61 dependency names, and its
isolated no-call `dlopen(RTLD_NOW)` resolved `RIL_Init` (constructors ran, but
`RIL_Init` was not called). Do not repeat that closure or loader experiment.
This is host closure/loader evidence only; it did not start `cbd`, RIL, CP, or
telephony.

## Running and checking the collector

On the host with an injected fixture:

```sh
python3 tools/hardware/cellular-readiness-evidence.py --sysfs-root /path/to/fake/sys
python3 tools/hardware/test-cellular-readiness-evidence.py
python3 -O tools/hardware/test-cellular-readiness-evidence.py
PYTHONOPTIMIZE=1 python3 tools/hardware/test-cellular-readiness-evidence.py
```

The coordinator may run the collector against the live `/sys` tree as a
non-mutating observation and return the JSON with subscriber/carrier data
omitted. No phone access was performed by this worker. If it reports CPIF
`ONLINE`, the next evidence step still needs separate SIM registration and a
data test whose route is forced to the mobile interface, followed by DNS and
HTTPS checks on that route.

## Hardware gate

No CP boot, SIM query, mobile data, IMS, or call was performed. The next
non-mutating step is to collect the redacted passive snapshot and reuse the
existing no-call closure review to pin down the remaining start contract and
protected-NV gate. Static inspection already found `cbd` paths for
`/mnt/vendor/efs/nv_data.bin`, `/mnt/vendor/efs/nv_5g_data.bin`, and
`/efs/factory.prop`, including create/write/fsync/removal operations. Any CP
boot would be a separately owner-authorized hardware operation and must wait
for an explicit protected-NV policy; the collector does not report or imply
`READY TO BOOT` from driver binding, module presence, or `ONLINE`.

SIM/data, IMS, and voice trials remain separate later steps. The dedicated
internet result must demonstrate a route forced through the mobile interface
plus DNS and HTTPS success on that interface. Calls require approved
endpoints and evidence of incoming and outgoing calls with uplink and downlink
audio. Emergency numbers and arbitrary recipients are out of scope.
