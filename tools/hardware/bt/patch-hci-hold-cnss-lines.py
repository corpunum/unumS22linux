#!/usr/bin/env python3
"""Patch the deployed bt-qca6490-hci-hold (sha256 66c7863c...) so the shared
WLAN-rail baseline no longer requires exactly TWO 'qcom,cnss-qca6490-vreg_wlan'
consumer lines in regulator_summary (2026-10-21: after some boots CNSS holds
the rail through ONE consumer handle, every bring-up then failed the gate with
-EBUSY, printed as baud_probe_result=-5, and Bluetooth never came up).

votes_match() keeps every vote check: exactly one vreg_wlan rail line with use
1 + expected_bt, exactly one bt_qca6490 consumer line with bt == expected_bt,
and CNSS consumer votes summing to 1 (so zero CNSS lines still fail). Only the
line-COUNT term `cnsss == 2` is dropped: at vaddr 0x401920
`ccmp w0, #0x2, #0x0, eq` (7a420800) -> `ccmp w0, w0, #0x0, eq` (7a400000).
The source equivalent is in bt-version-transport-probe.c (cnsss >= 1). The
NVM payload header needed for a source rebuild is no longer on the rig.

usage: patch-hci-hold-cnss-lines.py IN OUT
"""
import hashlib, sys
IN_SHA = '66c7863cb39fb86636359d672363f54144cd47c65f5993b9f7907224c278bb35'
OFF, OLD, NEW = 0x1920, bytes.fromhex('0008427a'), bytes.fromhex('0000407a')
src, dst = sys.argv[1:3]
b = bytearray(open(src, 'rb').read())
if hashlib.sha256(b).hexdigest() != IN_SHA:
    sys.exit('unexpected input binary (sha256 mismatch)')
if b[OFF:OFF + 4] != OLD:
    sys.exit('unexpected bytes at patch site')
b[OFF:OFF + 4] = NEW
open(dst, 'wb').write(b)
print('patched', dst, hashlib.sha256(b).hexdigest())
