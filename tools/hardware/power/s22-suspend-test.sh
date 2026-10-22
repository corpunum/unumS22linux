#!/bin/sh
# Suspend once with an RTC alarm as the guaranteed wake source.
# usage: s22-suspend-test.sh SECONDS [mem|freeze] [pm_test level|none]
# Run detached (setsid nohup) so a dropped SSH session does not matter.
# Log: /srv/s22/power/suspend-<ts>.log
S=${1:-20}; ST=${2:-mem}; T=${3:-none}
L=/srv/s22/power/suspend-$(date +%Y%m%dT%H%M%S).log
exec >>"$L" 2>&1
echo "== $(date) suspend test: ${S}s state=$ST pm_test=$T mem_sleep=$(cat /sys/power/mem_sleep)"
echo "pre: success=$(cat /sys/power/suspend_stats/success) fail=$(cat /sys/power/suspend_stats/fail) uptime=$(cut -d' ' -f1 /proc/uptime)"
cat /sys/class/power_supply/battery/current_now
mark="s22-suspend-test-$$"
echo "$mark" > /dev/kmsg
echo "$T" > /sys/power/pm_test
echo 0 > /sys/class/rtc/rtc0/wakealarm
echo "+$S" > /sys/class/rtc/rtc0/wakealarm
t0=$(date +%s)
if echo "$ST" > /sys/power/state; then rc=0; else rc=$?; fi
t1=$(date +%s)
echo none > /sys/power/pm_test
echo 0 > /sys/class/rtc/rtc0/wakealarm
echo "returned rc=$rc after $((t1-t0))s wall; success=$(cat /sys/power/suspend_stats/success) fail=$(cat /sys/power/suspend_stats/fail) last_failed_dev=$(cat /sys/power/suspend_stats/last_failed_dev 2>/dev/null) last_failed_errno=$(cat /sys/power/suspend_stats/last_failed_errno 2>/dev/null) last_failed_step=$(cat /sys/power/suspend_stats/last_failed_step 2>/dev/null)"
cat /sys/power/pm_wakeup_irq 2>/dev/null && echo "^ pm_wakeup_irq"
dmesg | awk -v m="$mark" 'f{print} index($0,m){f=1}' | grep -E "PM:|EXYNOS|exynos-pm|Resume caused|MIF|suspend|resume|Freezing|Restarting|wakeup|abort|fail|error" | grep -v -E "Watchdog|s2m_rtc_read_time|max77705_get" | head -150
echo "== health"
sleep 8
sh /srv/s22/power/s22-power-health.sh
echo "== done $(date)"
