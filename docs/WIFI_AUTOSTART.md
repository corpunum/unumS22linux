# Native Wi-Fi recovery-boot startup

First live recovery-reboot acceptance: **BORE759**, 2026-09-20. The optional
task started at13.043s, completed service startup with association at38.686s,
and all13 network/model checks passed at88.53s. The session remained up past
187s before a separately requested second test. This is a recovery-target
software reboot, not a normal-power-on or disconnected-USB test.

The second consecutive recovery reboot, **BORE760**, also passed: association
and services ready38.659s, all13 traffic/model checks passed71.80s, and final
state checked121.04s. This exercises a completed prior attempt becoming eligible
in a new boot; the same-boot duplicate check was separately verified read-only.
All50 focused host tests pass. No image/partition write was made.

This is an optional ordinary-file extension to the installed native RECOVERY
environment. It does not install Linux in normal BOOT, alter a partition image,
or run OpenRC. The native guardian starts the native root and SSH directly;
an `/etc/local.d` script would not run in this installation.

## Startup and isolation

The existing persistent desktop supervisor mounts verified userdata, starts
the model and Hyprland/Omarchy, and publishes its runtime-ready marker. Only
then does it spawn `wifi-autostart.py --start` in a separate process session.
Failure or absence of this optional process does not restart the desktop,
SSH or guardian. It is invoked once, with no automatic radio activation retry.

The enable marker is `/etc/s22-wifi-enabled`; `/etc/s22-wifi-disabled` overrides
it. No script automatically enables itself. The runner checks native PID1,
kernel, mounted userdata, private profile permissions and duplicate processes.
Before any radio startup it synchronously persists an attempt record in
`/srv/s22/hardware/wifi-private/autostart-attempt.json`. A previous incomplete
or failed attempt prevents further automatic activation, including after reboot.
Do not discard that record to retry without first investigating the failure.

The startup sequence is: exact optional-firmware responder, fresh-state WLAN
calibration, WPA supplicant, then DHCP. Firmware binds remain read-only. The
responder remains available after an observer failure because a loaded kernel
driver may still request firmware. There is no automated unload, flash,
firmware-path change, EFS write or forced reboot.

The `complete` attempt status means the service processes started and were
checked; it does **not** mean internet works. Association, DHCP, DNS and
WLAN-forced HTTPS require separate live acceptance. WPA handles network
association and udhcpc retains normal retry/renewal behavior, but this is not
a general crash-restarting service manager.

## Boot-specific fixes

The WLAN driver can idle-power-off between calibration and association.
The harness accepts only the reviewed full `0x420107` or idle `0x420100`
states, with a real wlan0 and fresh calibration-success log evidence. It does
not accept the calibration bit alone. The optional boot invocation permits
an absent USB carrier; interactive experiments still require USB by default.

The DHCP lease and resolver-ownership metadata now include the kernel boot ID.
An old boot's metadata is discarded without applying its routes or resolver
backup. This matters because native bootstrap initializes a fresh USB resolver
on every boot. Same-boot DNS renewals preserve the resolver inode shared with
Arch and keep respecting manual resolver changes.

The per-interface `ecm0/ignore_routes_with_linkdown=1` IPv4 setting prevents a
disconnected USB route from hiding the WLAN route. It does not delete or change
the priority of the USB route when connected. The pinned kernel's
`net/ipv4/fib_trie.c:1363` and `include/linux/inetdevice.h:249` implement that
filter. Physical unplug/replug behavior requires a separate test; a sysctl
readback alone is not proof of an untethered working phone.

## Recovery and evidence

Preserve USB rescue and the existing recovery image. Use only the tested
`s22-reboot recovery` target for a separately controlled reboot test; ordinary
`reboot` or the helper's `normal` target can enter the restored Android BOOT
and must not be used. Startup code never invokes a reboot itself.

Disable future optional launches by creating `/etc/s22-wifi-disabled`. This
does not stop a running driver/responder. Do not kill firmware services or
unmount their assets under the loaded kernel. Original startup/helper files
are preserved before deployment; restoration is an ordinary-file operation,
not a partition flash. Private logs, attempt state and network credentials
stay on userdata. See `EXPERIMENTS.md` for actual deployment and boot results.

Installed originals are preserved under
`/srv/s22/hardware/backups/wifi-autostart-20260920T2035Z/`. The private
directory also contains old lease/resolver metadata; never publish it.
Deployment and sanitized acceptance records are in
[`evidence/wifi-autostart-20260920/`](../evidence/wifi-autostart-20260920/).
The standalone traffic probe's `not_tested` field describes what that single
invocation cannot establish. Reboot persistence is established by the separate
BORE transition, automatic startup events, same-boot attempt receipt, and
post-reboot traffic checks together.
