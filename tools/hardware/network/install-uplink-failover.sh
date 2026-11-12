#!/bin/sh
# Install the rig uplink failover daemon DISABLED (run with sudo). Usage:
#   sudo sh install-uplink-failover.sh install <repo-checkout> <config.json> <token-file>
#   sudo sh install-uplink-failover.sh rollback      (also installed as /usr/local/lib/unum/install-uplink-failover.sh)
# install copies three files and the token, loads the unit, and does NOT enable or start it.
set -eu
case "${1:-}" in
  install)
    src=$2/tools/hardware/network; conf=$3; tok=$4
    [ -f "$src/s22-uplink-failover.py" ] && [ -f "$src/unum-uplink-failover.service" ] && [ -f "$src/s22-uplink-cut-test.sh" ] || { echo "files missing in $src" >&2; exit 1; }
    install -d -m 755 /usr/local/lib/unum
    install -d -m 700 /etc/unum
    install -m 755 -o root -g root "$src/s22-uplink-failover.py" /usr/local/lib/unum/s22-uplink-failover.py
    install -m 755 -o root -g root "$src/s22-uplink-cut-test.sh" /usr/local/lib/unum/s22-uplink-cut-test.sh
    install -m 755 -o root -g root "$0" /usr/local/lib/unum/install-uplink-failover.sh   # rollback without the checkout
    install -m 600 -o root -g root "$conf" /etc/unum/uplink-failover.json
    install -m 600 -o root -g root "$tok" /etc/unum/s22-device.token
    install -m 644 -o root -g root "$src/unum-uplink-failover.service" /etc/systemd/system/unum-uplink-failover.service
    systemctl daemon-reload
    echo "installed, not enabled. Next: python3 -I /usr/local/lib/unum/s22-uplink-failover.py once" ;;
  rollback)
    systemctl disable --now unum-uplink-failover 2>/dev/null || true
    [ -x /usr/local/lib/unum/s22-uplink-cut-test.sh ] && sh /usr/local/lib/unum/s22-uplink-cut-test.sh end || true
    [ -f /usr/local/lib/unum/s22-uplink-failover.py ] && [ -f /etc/unum/uplink-failover.json ] && python3 -I /usr/local/lib/unum/s22-uplink-failover.py sweep || true
    rm -f /etc/systemd/system/unum-uplink-failover.service /usr/local/lib/unum/s22-uplink-failover.py /usr/local/lib/unum/s22-uplink-cut-test.sh /usr/local/lib/unum/install-uplink-failover.sh /etc/unum/uplink-failover.json /etc/unum/s22-device.token /run/unum-uplink-failover.json
    rmdir /etc/unum /usr/local/lib/unum 2>/dev/null || true
    systemctl daemon-reload
    echo "removed (audit log /var/log/unum-uplink-failover.jsonl kept)" ;;
  *) sed -n 2,5p "$0" >&2; exit 2 ;;
esac
