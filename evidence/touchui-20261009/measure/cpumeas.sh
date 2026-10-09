#!/bin/sh
# usage: cpumeas.sh SECONDS ; prints CPU% (of one core) for Hyprland, quickshell instances, squeekboard, touchd + RSS
S=${1:-10}
pids=$(for p in /proc/[0-9]*; do c=$(cat $p/comm 2>/dev/null); case "$c" in Hyprland|quickshell|squeekboard) echo ${p#/proc/};; python3) grep -q s22-touchd $p/cmdline 2>/dev/null && echo ${p#/proc/};; esac; done)
for p in $pids; do echo "$p $(cut -d' ' -f14,15 /proc/$p/stat)"; done > /tmp/cm.a
sleep $S
for p in $pids; do t=$(cut -d' ' -f14,15 /proc/$p/stat); a=$(grep "^$p " /tmp/cm.a | cut -d' ' -f2,3); set -- $a $t; d=$(( ($3+$4) - ($1+$2) )); rss=$(grep VmRSS /proc/$p/status | awk '{print $2}'); args=$(tr '\0' ' ' </proc/$p/cmdline | cut -c1-60); echo "pid=$p cpu%=$(( d * 100 / (S*100) )).$(( (d * 1000 / (S*100)) % 10 )) rssKB=$rss $args"; done
grep -E "^cpu " /proc/stat
