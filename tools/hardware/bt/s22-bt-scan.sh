#!/bin/sh
# s22-bt-scan [scan.py args]: bounded Bluetooth discovery (~20 s controller window).
# 1. The reviewed plain-H4 bridge probe powers the QCA6490, loads the patch/NVM,
#    switches to 3 Mbaud and registers hci0 for 20 s, then detaches and powers off.
#    It is pinned to the X2 core with SCHED_FIFO 50, because the 115200 -> 3M
#    baud switch is timing-sensitive and failed once under the normal ~14 load.
# 2. s22-bt-scan.py (Arch chroot) does a passive LE scan and a classic inquiry on hci0.
# No pairing, connection or advertising. Logs go to /srv/s22/state/bt/scan-*.
set -u
P=/srv/s22/bt-trial-20260927/bt-hci-plain-h4-20260927/bt-qca6490-hci-bridge-probe
D=/srv/s22/state/bt/scan-$(date +%Y%m%d-%H%M%S); mkdir -p "$D"
[ -e /sys/class/bluetooth/hci0 ] && { echo "hci0 already present; refusing" >&2; exit 1; }
python3 -c '
import os, sys, ctypes
os.sched_setaffinity(0, {7})
prm = ctypes.c_int(50)
ctypes.CDLL(None).syscall(119, 0, 1, ctypes.byref(prm))  # sched_setscheduler(SCHED_FIFO); musl wrapper is ENOSYS
p = sys.argv[1]
os.execv(p, [p, "--execute", "--allow-shared-wlan-rail", "--live-wlan-vote", "--uart", "/dev/ttySAC1",
             "--btpower", "/dev/btpower", "--btpower-rdev", "503:0"])
' "$P" > "$D/probe.out" 2>&1 &
PP=$!
chroot /mnt/omarchy-trial python3 /opt/s22-bt/s22-bt-scan.py "$@" > "$D/scan.json" 2> "$D/scan.err"
wait $PP; rc=$?
echo "probe_rc=$rc" >> "$D/probe.out"
cat "$D/scan.json"; grep -E "bridge_result|baud_probe_result|probe_rc" "$D/probe.out"
echo "log: $D"
