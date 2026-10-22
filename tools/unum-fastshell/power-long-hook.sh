#!/bin/sh
# s22-buttons hook "power-long" (Power held >= 3 s), installed as
# /srv/s22/buttons/hooks/power-long by install-fastshell.sh.
# The way back that does not depend on the fast shell itself: select the classic
# shell (Quickshell on sway) and stop unum-fastshell by its exact PID. seatd then
# gives the panel back to sway and s22-touchd starts Quickshell.
# Re-enable with: s22-desktop-profile fastshell
F=/srv/s22/state/touchui/ui-backend
if [ "$(cat $F 2>/dev/null)" = fastshell ]; then
  printf 'quickshell\n' > $F.tmp && mv $F.tmp $F
fi
pid=$(cat /run/s22-fastshell.pid 2>/dev/null)
if [ -n "$pid" ] && [ "$(cat /proc/$pid/comm 2>/dev/null)" = unum-fastshell ]; then
  kill -TERM "$pid"
fi
echo "$(date '+%F %T') power-long: classic shell selected (fastshell pid ${pid:-none})" >> /srv/s22/state/fastshell/hook.log 2>/dev/null
exit 0
