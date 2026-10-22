#!/usr/bin/env python3
"""Patch bt-qca6490-hci-hold (input: the cnss-lines patched binary, sha256
3ec69d2f...) so the live-WLAN baseline no longer requires USB ECM carrier.

Owner decision 2026-10-22: Bluetooth must start without the USB link. The
phone's USB goes to the ROG Ally, not the rig, so ECM is not the rescue path;
Tailscale over Wi-Fi is. validate_live_wlan_baseline() keeps every Wi-Fi/
CNSS/regulator safety check: wlan module + wlan0, CNSS stats in the live
state, no existing hci device, and votes_match() on regulator_summary. Only
the read of /sys/class/net/ecm0/carrier and its "1\\n" comparison are
skipped. This is the only reference to that path in the binary.

At vaddr 0x401c40 (file 0x1c40), the first instruction of the ECM step,
`adrp x0, 471000` (90000380) becomes `b 0x401ca8` (1400001a), which jumps to
the has_hci_device() step that followed a passing carrier check.
The source equivalent is in bt-version-transport-probe.c.

usage: patch-hci-hold-ecm-gate.py IN OUT
"""
import hashlib, sys
IN_SHA = '3ec69d2f99f6565beb82c6dd7545c03276e846d60e7d0892b3f865075210e247'
OFF, OLD, NEW = 0x1c40, bytes.fromhex('80030090'), bytes.fromhex('1a000014')
src, dst = sys.argv[1:3]
b = bytearray(open(src, 'rb').read())
if hashlib.sha256(b).hexdigest() != IN_SHA:
    sys.exit('unexpected input binary (sha256 mismatch; apply patch-hci-hold-cnss-lines.py first)')
if b[OFF:OFF + 4] != OLD:
    sys.exit('unexpected bytes at patch site')
b[OFF:OFF + 4] = NEW
open(dst, 'wb').write(b)
print('patched', dst, hashlib.sha256(b).hexdigest())
