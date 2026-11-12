#!/bin/sh
# Make the phone's OpenUnum an unprivileged user (hub U-8cdacbb0). Native root, as root.
#   openunum-agent-user.sh apply      create chroot user unumagent (1500:1500), hand it /root/.openunum,
#                                     close the rest of /root, keep plugin code root-owned
#   openunum-agent-user.sh check      adversarial test AS that user: every line must say DENIED
#   openunum-agent-user.sh rollback   ownership back to root (then: touch .../openunum/run-as-root, restart)
# OpenUnum must be stopped for apply/rollback (create /run/s22-hold/openunum first so keepalive waits).
set -eu
C=/mnt/omarchy-trial
U=unumagent; ID=1500
DATA=$C/root/.openunum
as_agent() { chroot "$C" /usr/sbin/setpriv --reuid $ID --regid $ID --clear-groups --inh-caps=-all --bounding-set=-all --no-new-privs "$@"; }
case "${1:-}" in
apply)
  grep -q "^$U:" $C/etc/group  || echo "$U:x:$ID:" >> $C/etc/group
  grep -q "^$U:" $C/etc/passwd || echo "$U:x:$ID:$ID:OpenUnum agent:/root:/usr/bin/nologin" >> $C/etc/passwd
  # /root: traverse only, owned by root, so the agent cannot rename or replace .openunum itself.
  chown 0:0 $C/root; chmod 711 $C/root
  for d in $C/root/.ssh $C/root/.config $C/root/.cache $C/root/.npm $C/root/.local $C/root/.agent-reach-venv; do
    [ -e "$d" ] && chmod go-rwx "$d"
  done
  chown -R -h $ID:$ID "$DATA"
  chmod 700 "$DATA"
  # Plugin code stays root's: the agent runs it but cannot change it.
  chown -R -h 0:0 "$DATA/plugins"; chmod -R go-w "$DATA/plugins"
  echo "applied: $(chroot $C id $U)" ;;
rollback)
  chown -R -h 0:0 "$DATA"; chmod 750 $C/root
  echo "rolled back: $DATA is root's again" ;;
check)
  fail=0
  S=$(for p in /proc/[0-9]*; do [ "$(cat $p/comm 2>/dev/null)" = s22d ] && echo ${p#/proc/}; done | head -1)
  try() {  # $1 = label, rest = command that must FAIL for the agent
    label=$1; shift
    if as_agent sh -c "$*" >/dev/null 2>&1; then echo "REACHABLE  $label"; fail=1; else echo "DENIED     $label"; fi
  }
  try "read the owner credential via /proc"     "cat /proc/$S/root/srv/s22/state/owner-session"
  try "list native state via /proc"             "ls /proc/$S/root/srv/s22/state"
  try "flip the mic switch via /proc"           "echo x >> /proc/$S/root/srv/s22/state/device-switches.json"
  try "read s22d's memory"                      "head -c 1 /proc/$S/mem"
  try "start forwarding (uplink) directly"      "echo 1 > /proc/sys/net/ipv4/ip_forward"
  try "add a NAT rule"                          "iptables-legacy -t nat -A POSTROUTING -j MASQUERADE"
  try "read root's ssh keys"                    "ls /root/.ssh"
  try "change plugin code"                      "touch /root/.openunum/plugins/s22-phone/index.mjs"
  try "make a device node"                      "mknod /tmp/x-agent-check b 8 0"
  try "become root with su"                     "su -c true"
  try "owner model switch through touchd"       "s22-ui model local | grep -q '\"ok\": true'"
  try "pair through touchd"                     "s22-ui pair forget | grep -q '\"ok\": true'"
  # and what it must still be able to do
  ok() { label=$1; shift; if as_agent sh -c "$*" >/dev/null 2>&1; then echo "WORKS      $label"; else echo "BROKEN     $label"; fail=1; fi; }
  ok "write its own data"                       "touch /root/.openunum/.agent-check && rm /root/.openunum/.agent-check"
  ok "reach its own API"                        "curl -fsS -m 5 http://127.0.0.1:18880/health"
  ok "reach s22d's local device API"            "curl -fsS -m 5 http://127.0.0.1:8766/v1/device/volume.get"
  ok "use touchd as the agent (status)"         "s22-ui status | grep -q '\"ok\": true'"
  exit $fail ;;
*) echo "usage: $0 apply|check|rollback" >&2; exit 2 ;;
esac
