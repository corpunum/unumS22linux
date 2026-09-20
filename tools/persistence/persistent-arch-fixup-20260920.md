# Persistent Arch signed fixup — 2026-09-20

This is a delta for the already-built persistent candidate. The original
candidate archive is unchanged. Packages were downloaded from the Arch Linux
ARM `aarch64/extra` repository and verified with the isolated candidate Arch
keyring before installation under QEMU-aarch64 with the normal pacman
signature checks enabled. No signature policy was weakened.

Packages and SHA-256:

| package | SHA-256 |
|---|---|
| `libbsd-0.12.2-2-aarch64.pkg.tar.xz` | `3d47f2e64ab9c2dfbdc5791ad7db237ed3c9708ad80718c26f4b850cf2b69238` |
| `libmd-1.2.0-1-aarch64.pkg.tar.xz` | `2bcd0088c3e5f0380a7cb5204aecf78f1cc054d95e1d7164a987fa81c755956d` |
| `inotify-tools-4.25.9.0-1-aarch64.pkg.tar.xz` | `9b73d4618d3e010b49d0fadcad0d1313dd5bc08552b383adf4be2f55dd3eb79b` |

All three detached signatures validated as GOODSIG/VALIDSIG from Arch Linux
ARM Build System key `68B3537F39A313B3E574D06777193F152BDBE6A6`.

The candidate package transaction completed with pacman exit 0 and its normal
`ConditionNeedsUpdate` hook. The delta archive contains only the three package
payload file sets plus their package database directories under
`var/lib/pacman/local/`; it does not contain a keyring or private keys.

Delta archive:

`builds/persistent-arch-fixup-20260920.tar.gz`

SHA-256: `5d3eeb6d054b3a4ed320a208f44bb51b7be0145133ce86e9b0d27db91dd36841`

The candidate’s AArch64 loader listing for `/usr/bin/squeekboard` now resolves
both previously missing libraries, `libbsd.so.0` and `libmd.so.0`; no `not
found` entries were observed. Native deployment should run `ldconfig` and any
normal distro cache update as part of its install flow. Host verification used
QEMU-aarch64; native phone installation/runtime remains unverified here.
