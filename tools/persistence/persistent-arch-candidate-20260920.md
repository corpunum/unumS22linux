# Persistent Arch candidate provenance (host-only)

Date: 2026-09-20. This is a host-side candidate only; it has not been
mounted, booted, flashed, or accepted as persistent on a phone.

## Candidate

`rootfs/persistent-arch-candidate-20260920/`

The candidate was copied from the locally existing Arch graphics userspace
`/tmp/s22-omarchy-arch/graphics-rootfs2` (not from the phone). The source was
about 1.9 GiB and the candidate is about 2.0 GiB after the explicitly recorded
Python/package additions and UI helper files. The existing `/mnt/omarchy-trial`
and `/mnt/model-bench` conventions are preserved; this candidate does not
contain or alter those mounts.

Pinned source/artifacts:

| Item | Revision/hash |
| --- | --- |
| `/opt/omarchy-source` source tree | git `c668141e9c42b13c80c9ca4ea108e11708c5e8a5` |
| GCC16 Aquamarine override | SHA256 `6fb3f4377ac3d14e4d74a18bdc5316e0acc23779e6894a1dab449cdb294d42ca` |
| `arch-omarchy-trial.tar.gz` source archive | SHA256 `ecb5ab0c75abb0bbb5e41cb1b9c017229974b87ec061c51110a580435439104d` |
| `omarchy-aarch64.db` source database | SHA256 `7d1dc130eaea26ab36c90233afd432409b4accb4c886d47268a2c43f9e754c10` |
| Python package delta | SHA256 `17015e87d76198ffda880a95fb5ce46f340a68247bd2b4148a7078d1d3f1f250` |
| mpdecimal dependency delta | SHA256 `388611eba7f9b0dddcc03adb4f28bcb8fd2c42fbd8c930ca6b9b0f9e4ece0f1e` |

The signed Python package and its local signed dependency were installed into
the candidate with the candidate's Arch `pacman`, keyring and package DB. The
temporary QEMU helper and package archives were removed after installation;
no signature checks were disabled.

## Package/ownership checks

Using ARM64 QEMU against the candidate root:

* `pacman -Dk`: `No database errors have been found!`
* package count after Python, Squeekboard and GNOME closure additions: 346
* `pacman -Qk`: every checked package reported zero missing files
* ownership was verified for `/usr/bin/python3`, `Hyprland`, `squeekboard`,
  `libgnome-desktop-3.so.20.0.0`, `libgmobile.so.0`, `foot`, `quickshell`,
  and `/usr/lib/libaquamarine.so.14`
* Python runtime reported `Python 3.14.7`

The generated schema closure includes `gschemas.compiled` (44,751 bytes) and
`org.gnome.desktop.enums.xml` (8,013 bytes). The candidate has
`/opt/s22-ui/shell -> /opt/omarchy-source/shell`. Full bounded output is in
[`persistent-arch-candidate-20260920-verification.txt`](persistent-arch-candidate-20260920-verification.txt).

The GCC16 Aquamarine override is intentionally an opt-in custom file under
`/opt/s22-aquamarine`; it is not claimed as owned by the distro `aquamarine`
package. Likewise, copied Hyprland configs, `phone-direct-trial.sh`, the
Omarchy UI launcher, and `s22-chat` are local candidate additions rather than
pacman-owned files.

## Included local additions

* `/opt/s22-aquamarine/libaquamarine.so.0.15.1` plus `libaquamarine.so.14`
  and `libaquamarine.so` symlinks.
* `/opt/s22-ui/launch-ui.sh` and `shell-trial.json`.
* `/usr/local/bin/s22-chat`, now the canonical HTTP client from
  `tools/linux-rootfs/s22-chat-http.py` (SHA256
  `1f8cf8949103c3771045c652d2b2ea607ecbd65e080e04139c30de0eb9b1bb16`),
  preserving `/mnt/model-bench` and loopback model-server assumptions.
* `/root/phone-direct-trial.sh` and the five existing Hyprland trial/display
  configs.

The Omarchy startup config retains `debug.disable_logs = true` and
`enable_stdout_logs = false`; the trial harness supplies `TZ=Europe/Athens`.

## Unresolved blockers

This is not an installed persistent system. It has not been tested from a
userdata filesystem, with the recovery bootstrap's mount namespace, or across
reboot/power loss. No userdata backup/restorability gate has been satisfied.
No startup supervisor, userdata mount unit, recovery-side Arch handoff, or
cold-BOOT path was added. Existing trial scripts intentionally stop/restore
Weston and must not be treated as permanent boot services. GPU/NPU readiness,
physical touch, Wi-Fi/cellular, suspend and Android FBE/userdata access remain
unproven. Keep the verified RECOVERY/Alpine rescue path unchanged until those
gates are separately authorized and tested.
