# Reviewed QCA6490 startup sequence

This device-specific development procedure supersedes the first failed WLAN
experiment. BORE759/760 now have automatic association and internet traffic;
see [Wi-Fi evidence and limitations](WIFI_NATIVE.md). **Do not execute this
sequence again on the currently loaded driver.** It is not a general installer
or a boot service by itself. The optional [boot runner](WIFI_AUTOSTART.md)
now invokes the revised harness; two fresh recovery boots passed end-to-end.

## Before a future single activation

Keep the known-good RECOVERY environment, pinned USB SSH, active model health
endpoint and read-only CNSS debugfs available. Retain the exact20260915
Lineage module/firmware closure on userdata. Never substitute the ABI-mismatched
stock module, execute the vendor rc wholesale, or run proprietary macloader
while EFS is off-limits. The vendor reference rc is from the preserved stock
board image, not proven to be the OTA's rc.

Start the reviewed optional-firmware responder **before** loading WLAN:

```sh
start-stop-daemon --start --background --make-pidfile \
  --pidfile /run/s22-optional-firmware.pid \
  --stdout /srv/s22/hardware/wifi-tools/optional-firmware.log \
  --stderr /srv/s22/hardware/wifi-tools/optional-firmware.err \
  --exec /usr/bin/python3 -- \
  /srv/s22/hardware/wifi-tools/wifi-optional-firmware.py --serve --acknowledge
```

This is a recorded invocation, not permission to start duplicate services.
The helper holds an owner lock. Wait for verified readiness; a background
launch returning0 does not establish health. The one-shot harness checks its
exact script hash, metadata, process arguments/root ownership and held lock.
Do not stop it while this loaded WLAN driver can request firmware.

On an independently authorized **fresh** CNSS state only:

```sh
python3 /srv/s22/hardware/wifi-tools/wifi-bringup-once.py
python3 /srv/s22/hardware/wifi-tools/wifi-bringup-once.py --activate
```

Default mode is inspection. Activation requires initial `0x400000` and no
WLAN module. It verifies14 firmware/config files and makes **four** read-only
PID1 firmware mounts, preserving touchscreen assets. It sets the reference
normal WLAN-only recovery policy, then inserts the exact module and writes
`fs_ready=1` in one process without intervening remote investigation. This
must occur before the130s deferred-registration deadline. It never fabricates
MAC completion, calibration data, or EFS files.

The driver's10s macloader grace may expire normally; source then continues.
Live DT enables CBC and does not require NV/DMS MAC. The host driver's
`enable_mac_provision=0` default permits the firmware/default MAC path.
Successful calibration must be observed in **new kernel logs**, not merely
the `COLD_BOOT_CAL_DONE` bit, which also appears on some failure paths.
`CalDB:0` permits no calibration-file download; it is not a missing-file error.

The exact optional QDSS request otherwise blocks60s, longer than the40s
mission boot timeout. Responding `loading=-1` completes an absent optional
file request; it does not pretend to load firmware or bypass calibration.
No global firmware timeout/path changes are needed.

The observer stops on CNSS recovery/error state or deadline. It does **not**
unload, retry, remove firmware mounts or stop the responder under a live
kernel. A stopped observer does not stop kernel recovery. Its success only
establishes calibration/enumeration, not association or internet access.
Preserve private logs and USB rescue if it fails; do not improvise reloads.

## Existing private profile and networking

The profile installer refuses overwriting an existing file. Reusing another
host profile requires owner authorization; metadata-only selection does not
read passwords. Never print, commit, or put credentials in command arguments.

The commands recorded for the successful current session were:

```sh
start-stop-daemon --start --background --make-pidfile \
  --pidfile /run/s22-wpa.pid \
  --stdout /srv/s22/hardware/wifi-private/wpa.log \
  --stderr /srv/s22/hardware/wifi-private/wpa.err \
  --exec /sbin/wpa_supplicant -- -D nl80211 -i wlan0 \
  -c /srv/s22/hardware/wifi-private/wpa_supplicant.conf
udhcpc -i wlan0 -p /run/s22-udhcpc.pid \
  -s /srv/s22/hardware/wifi-tools/wifi-dhcp-hook.py -n -t 4 -T 3 -a1000
python3 /srv/s22/hardware/wifi-tools/wifi-acceptance.py
```

Do not launch duplicate WPA/DHCP processes. Confirm WPA2/CCMP `COMPLETED`
before DHCP. Raw status, scans, DHCP logs and resolver backups are private.
The acceptance command intentionally sends only a public DNS/HTTPS probe
and reports sanitized results. A persisted profile/tool is not boot-autostart
proof. The separately recorded recovery reboots now establish that proof;
physical unplug, roaming and suspend/resume remain separate tests.
