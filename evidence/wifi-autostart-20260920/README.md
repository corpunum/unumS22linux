# Wi-Fi autostart: two accepted recovery reboots

All captures are sanitized; raw BORE/dmesg, credentials, DHCP addressing and
private logs/backups remain outside the public repository.

| Evidence | Meaning |
| --- | --- |
| install.json | Original files backed up; candidate hashes read back from installed files |
| pre-reboot.json | Wi-Fi marker enabled only after deployment checks; BORE758 baseline |
| pre-reboot-connectivity.json | Existing Wi-Fi/USB/model still healthy before any reboot |
| boot-return.json | First new boot759 selected RECOVERY, native guardian, SSH at16.25s |
| first-boot-state.json | Automatic startup events, completed attempt38.686s, persistent UI configuration, same boot at187.62s |
| after-reboot-connectivity.json | All13 independent checks passed at88.53s on BORE759 |
| second-boot-return.json | Second new boot760 selected RECOVERY, native guardian, SSH at15.52s |
| second-reboot-connectivity.json | All13 independent checks passed at71.80s on BORE760 |
| final-state.json | Still760 at121.04s, completed attempt38.659s, current lease ownership, exact live service argv/root UIDs, installed hashes and desktop state |
| host-tests.txt | 50 mocked host tests; no real credentials, radio activation or phone calls |

The actual traffic checks bind DNS UDP and HTTPS to wlan0; ordinary Alpine
and Arch DNS also pass. USB carrier/default route and CPU model health remain
good. Both boots exceeded60s uninterrupted runtime; only the specifically
requested RESTART2 recovery reboots were issued. No buttons or flashing.

Startup `complete` means live services/association were observed, not internet
acceptance. The separate traffic receipts supply that evidence. Conversely,
the traffic script's generic `not_tested` list means that a single invocation
cannot establish reboot persistence: the boot transitions and startup receipts
above provide the separate proof.

Private first-boot early kernel capture records successful calibration at
29.769118s, phase4624ms. The three optional QDSS waits lasted approximately
139ms,176ms and194ms instead of60s. No recovery/error was observed in those
startup events. The current kernel ring overwrites early startup logs rapidly;
do not use a much later empty search to negate captured earlier evidence.

Not established: normal cold-power-on Linux, unplugged USB use, roaming,
suspend/resume, sustained throughput, battery life, physical finger sensing,
Bluetooth/audio/cellular/camera or GPU/NPU inference. No runtime network
credential or proprietary firmware is part of this publication.
