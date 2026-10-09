#!/bin/sh
# walk the touch UI pages and take screenshots into /srv/s22/state/touchui/shots
C=/mnt/omarchy-trial; U="chroot $C /usr/local/bin/s22-ui"; O=/srv/s22/state/touchui/shots; mkdir -p $O
shot() { $U screenshot /tmp/w-$1.png >/dev/null && cp $C/tmp/w-$1.png $O/$1.png; }
for p in ${PAGES:-home chat phone camera files settings switcher}; do
  if [ $p = home ]; then $U home; else $U open $p; fi >/dev/null
  sleep 6; shot $p
done
