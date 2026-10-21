#!/bin/sh
# gnss-rt.sh: run the stock GNSS engine (gpsd) and our client (gnss-probe) in a minimal
# Bionic root, /srv/s22/gnss-rt, with no modem device nodes and a seccomp guard.
#
#   gnss-rt.sh check            linker --list for gpsd and gnss-probe (no hardware access)
#   gnss-rt.sh start            start gpsd (guarded, under strace) in the background; pid in $STATE/gpsd.pid
#   gnss-rt.sh probe [ARGS]     run gnss-probe (guarded) in the same root, e.g. --seconds 300 --nmea
#   gnss-rt.sh stop             kill gpsd by its exact PID, then put Kepler back into reset
#   gnss-rt.sh status           gnss_status, Kepler IRQ counts, gpsd pid
#
# Every process runs under gnss-guard (seccomp): ioctl 0x4b10 (whole-phone watchdog reset),
# ioctl 0x6f27 (CP status / SIM-changed flag), reboot, kexec and module loading fail with EPERM.
# The root's /dev holds only null, zero, random, urandom and gnss_ipc: no umts_*, no modem.
set -u
R=${GNSS_ROOT:-/srv/s22/gnss-rt}
SYS=${GNSS_SYSTEM:-/srv/s22/android-rt/system}
STATE=${GNSS_STATE:-/srv/s22/state/gnss}
GUARD=$R/bin/gnss-guard
CFG=/vendor/etc/gnss/gps.linux.cfg
mkdir -p "$STATE"

# enter ROOT CMD...: private mount namespace, minimal /dev, chroot; seccomp first so it covers all
enter() {
  exec "$GUARD" unshare -m sh -c '
    R=$0; SYS=$1; shift 1
    mount --make-rprivate / || exit 9
    mount --bind "$SYS" "$R/system" && mount -o remount,bind,ro "$R/system" || exit 9
    for n in null zero random urandom gnss_ipc; do
      [ -e "$R/dev/$n" ] || : > "$R/dev/$n"
      mount --bind "/dev/$n" "$R/dev/$n" || exit 9
    done
    mount -t proc proc "$R/proc" || exit 9
    mount --rbind /sys "$R/sys" && mount -o remount,bind,ro "$R/sys" || exit 9
    for n in "$R"/dev/*; do
      case ${n##*/} in null|zero|random|urandom|gnss_ipc) ;; *) echo "unexpected $n in $R/dev" >&2; exit 9 ;; esac
    done
    exec env LD_PRELOAD=/vendor/lib64/propshim.so chroot "$R" "$@"
  ' "$R" "$SYS" "$@"
}

pid_alive() {
  [ -n "${1:-}" ] && [ -d "/proc/$1" ] && tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null | grep -q 'vendor/bin/hw/gpsd'
}

status() {
  echo "gnss_status: $(cat /sys/devices/platform/gnssif/gnss_status 2>/dev/null)"
  grep -E 'kepler' /proc/interrupts | awk '{s=0; for(i=2;i<=9;i++) s+=$i; print "  irq", $NF, s}'
  p=$(cat "$STATE/gpsd.pid" 2>/dev/null)
  if pid_alive "$p"; then echo "gpsd pid $p running"; else echo "gpsd not running"; fi
}

case "${1:-}" in
  check)
    (enter /system/bin/linker64 --list /vendor/bin/hw/gpsd) 2>&1 | grep -E 'not found|=>' | sed 's/^/gpsd: /' | grep -E 'not found|libandroid_net|libc\+\+|libssl'
    (enter /system/bin/linker64 --list /bin/gnss-probe) 2>&1 | grep -E 'not found|hidl|gnss' | sed 's/^/probe: /'
    (enter /system/bin/linker64 --list /vendor/lib64/hw/android.hardware.gnss@2.1-impl.so) 2>&1 | grep -E 'not found|vndksupport' | sed 's/^/hal: /'
    ;;
  start)
    p=$(cat "$STATE/gpsd.pid" 2>/dev/null)
    if pid_alive "$p"; then echo "gpsd already running: $p"; exit 1; fi
    ts=$(date +%Y%m%dT%H%M%S)
    TRACE=openat,ioctl,connect,bind,socket,reboot,execve,finit_module,init_module
    [ -n "${GNSS_STRACE_ALL:-}" ] && TRACE=all
    # strace (native root) records the calls that matter; the guard sits below it
    nohup strace -f -tt -s 96 -o "$STATE/gpsd-$ts.strace" \
      -e trace=$TRACE \
      sh "$0" _gpsd > "$STATE/gpsd-$ts.log" 2>&1 &
    for i in 1 2 3 4 5 6 7 8 9 10; do
      sleep 1
      p=$(for d in /proc/[0-9]*; do tr '\0' ' ' < $d/cmdline 2>/dev/null | grep -q '^/vendor/bin/hw/gpsd -c' && echo ${d#/proc/}; done | head -1)
      [ -n "$p" ] && break
    done
    [ -n "$p" ] || { echo "gpsd did not start, see $STATE/gpsd-$ts.log"; exit 1; }
    echo "$p" > "$STATE/gpsd.pid"
    echo "gpsd pid $p; log $STATE/gpsd-$ts.log; strace $STATE/gpsd-$ts.strace"
    ;;
  _gpsd)
    # exec gpsd itself (PT_INTERP /system/bin/linker64): it finds its embedded Kepler patch
    # through its own executable, so starting it as "linker64 gpsd" fails codeload
    enter /vendor/bin/hw/gpsd -c "$CFG"
    ;;
  probe)
    shift
    enter /bin/gnss-probe "$@"
    ;;
  stop)
    p=$(cat "$STATE/gpsd.pid" 2>/dev/null)
    if pid_alive "$p"; then
      kill -TERM "$p"
      for i in 1 2 3 4 5; do pid_alive "$p" || break; sleep 1; done
      pid_alive "$p" && kill -KILL "$p"
      echo "gpsd $p stopped"
    else
      echo "gpsd not running"
    fi
    rm -f "$STATE/gpsd.pid"
    "$R/bin/gnss-hold-reset"
    ;;
  status) status ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac
