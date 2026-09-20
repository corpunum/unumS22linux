# Native Wi-Fi acceptance, 2026-09-20

`acceptance.json` is emitted by the deployed `wifi-acceptance.py` over pinned
USB SSH. It passed at20:06:36UTC in BORE758. DNS and HTTPS were explicitly
bound to `wlan0`, while both native and Arch app name resolution also passed.
USB rescue and local model health stayed intact. No credentials or network
identifiers are included. `platform.json` records bounded BORE parsing,
installed source hashes and running desktop components. Empty kernel-marker
arrays there reflect the current ring buffer; they do not negate the earlier
preserved calibration evidence.

`acceptance-followup.json` repeats all13 successful checks at20:10:46UTC,
4148s uptime,249.55s after the first saved sample. This is a second live
checkpoint, not a continuous traffic or endurance benchmark.

## Earlier activation chronology

Source: private `wifi-sequenced-20260920-tXNHr3/activation.jsonl` and captured
kernel logs. Times below are kernel/host-observer monotonic seconds in BORE758.
The first observer failed, correctly; later manual completion of the exact
optional firmware requests restored WLAN without a second module insertion.

| Time | Observation |
| --- | --- |
| 2153.906 | Exact module/manifest,14 files and initial CNSS state checked |
| 2153.943 | Four read-only PID1 firmware binds verified |
| 2153.944 | Reference normal WLAN-only recovery policy selected |
| 2153.999 | Module insertion and filesystem-ready completed in0.055s |
| 2164.203 | Normal macloader10s grace expired; calibration continued |
| 2227.180 | First optional QDSS lookup finally returned missing |
| 2231.846 | `Calibration completed successfully`; calibration phase4660ms |
| 2233.318 | Mission-mode QDSS request began another60s wait |
| 2274.283 | Mission startup40s timeout queued WLAN recovery |
| 2294.765 | Optional request returned; queued recovery remained |
| 2299.276 | Observer stopped on `DRIVER_RECOVERY`, despite interface enumeration |
| 2343.915 | Next delayed mission request queued another WLAN recovery |
| about2350–2375 | Exact missing-file responses serviced; WLAN reinitialized |

The responder now handles subsequent wake requests promptly. Passive scan
then returned0 with four BSS entries on2.4/5GHz. The user approved using the
matching active host Wi-Fi profile. WPA2/CCMP association and DHCP followed.
Initial `wpa_supplicant -f` was unsupported and did not start a process; the
documented foreground/start-stop-daemon invocation succeeded.

No new SoC reboot, physical input, EFS change or partition write in this
follow-up. The ring buffer rolled over during continued uptime, so the raw
pre/during/after logs remain private rather than relying on a later dmesg.

Unproven: reboot-autostart, USB-disconnected operation, roaming, suspend/wake,
sustained throughput and ordinary phone battery life. The revised fresh-boot
harness has mocked tests; its responder readiness check was exercised live,
but the revised activation was deliberately not rerun on the working radio.
