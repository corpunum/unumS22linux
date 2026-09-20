#!/bin/sh
# Start a bounded Alpine Weston DRM/Pixman session on the native phone.
#
# This is a target-side launcher, not a rendering test.  It deliberately uses
# the downstream DPU card explicitly and Pixman CPU rendering; it does not
# select Mesa acceleration, change modes outside Weston, or touch partitions.
set -eu

DRM_DEVICE=${WESTON_DRM_DEVICE:-/dev/dri/card1}
DRM_CARD=${WESTON_DRM_CARD:-${DRM_DEVICE##*/}}
WESTON_SEAT=${WESTON_SEAT:-seat0}
WESTON_MODE=${WESTON_DRM_MODE:-preferred}
WESTON_OUTPUT=${WESTON_DRM_OUTPUT:-DSI-1}
WESTON_SCALE=${WESTON_SCALE:-2}
WESTON_SOCKET=${WESTON_WAYLAND_SOCKET:-wayland-0}
WESTON_XWAYLAND=${WESTON_XWAYLAND:-auto}
WESTON_TERMINAL_MODE=${WESTON_TERMINAL_MODE:---maximized}
RUNTIME_UID=${WESTON_UID:-$(id -u)}
RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$RUNTIME_UID}
WESTON_CONFIG=${WESTON_CONFIG:-/run/weston-native.ini}
# Alpine seatd 0.9.3 has a fixed server socket path; libseat is pointed at it
# explicitly below rather than accepting a client-only SEATD_SOCK override.
SEATD_SOCKET=/run/seatd.sock
SEATD_GROUP=${SEATD_GROUP:-root}
WESTON=${WESTON_BIN:-/usr/bin/weston}
WESTON_TERMINAL=${WESTON_TERMINAL_BIN:-/usr/bin/weston-terminal}
WESTON_KEYBOARD=${WESTON_KEYBOARD_BIN:-/usr/libexec/weston-keyboard}
SEATD=${SEATD_BIN:-/usr/bin/seatd}
LIBSEAT=${LIBSEAT_LIBRARY:-/usr/lib/libseat.so.1}
LIBUDEV=${LIBUDEV_LIBRARY:-/usr/lib/libudev.so.1}

die() {
  printf 'start-weston-native: %s\n' "$*" >&2
  exit 1
}

need_file() {
  [ -e "$1" ] || die "missing $2: $1"
}

need_exec() {
  [ -x "$1" ] || die "missing executable $2: $1"
}

need_exec "$WESTON" weston
need_exec "$WESTON_TERMINAL" weston-terminal
need_exec "$WESTON_KEYBOARD" weston-keyboard
need_exec "$SEATD" seatd
need_file "$DRM_DEVICE" "DRM device"
[ -c "$DRM_DEVICE" ] || die "DRM device is not a character device: $DRM_DEVICE"
[ "$DRM_CARD" = "${DRM_CARD##*/}" ] || die "Weston DRM card must be a basename: $DRM_CARD"
[ -d /sys/class/drm ] || die 'DRM sysfs is not mounted at /sys/class/drm'
need_file "$LIBUDEV" 'eudev/libudev ABI'
need_file "$LIBSEAT" 'libseat ABI'

# Alpine's libseat 0.9.3 package is built with seatd/logind/noop backends;
# this check prevents accidentally relying on the unavailable "builtin" name.
grep -a -q 'backend_seatd' "$LIBSEAT" || die 'libseat has no compiled seatd backend'

mkdir -p "$RUNTIME_DIR"
chmod 700 "$RUNTIME_DIR"
if [ "$(id -u)" = 0 ]; then
  chown 0:0 "$RUNTIME_DIR"
fi

# Weston uses libudev for DRM/input enumeration.  The existing addon includes
# eudev-libs (libudev.so.1), but intentionally does not include an eudev daemon.
# A daemon is started when present; explicit card1 selection and
# --continue-without-input keep the no-daemon diagnostic path usable.
UDEVD=''
if [ -x /sbin/udevd ]; then
  UDEVD=/sbin/udevd
elif [ -x /usr/lib/eudev/udevd ]; then
  UDEVD=/usr/lib/eudev/udevd
fi
if [ -n "$UDEVD" ]; then
  # Desktop restarts must not leave another netlink listener behind each time.
  if ! pidof udevd >/dev/null 2>&1; then
    "$UDEVD" --daemon >/dev/null 2>&1 || true
  fi
  if command -v udevadm >/dev/null 2>&1; then
    udevadm trigger --action=add --subsystem-match=input >/dev/null 2>&1 || true
    udevadm trigger --subsystem-match=drm >/dev/null 2>&1 || true
    udevadm settle >/dev/null 2>&1 || true
  fi
else
  printf '%s\n' 'start-weston-native: eudev daemon absent; using libudev plus explicit DRM card and no-input continuation' >&2
fi

export XDG_RUNTIME_DIR="$RUNTIME_DIR"
export LIBSEAT_BACKEND=seatd
export SEATD_SOCK="$SEATD_SOCKET"
export SEATD_VTBOUND=0

SEATD_PID=''
SEATD_STARTED=0
WESTON_PID=''
cleanup() {
  if [ -n "$WESTON_PID" ]; then
    kill "$WESTON_PID" 2>/dev/null || true
    wait "$WESTON_PID" 2>/dev/null || true
  fi
  if [ "$SEATD_STARTED" = 1 ] && [ -n "$SEATD_PID" ]; then
    kill "$SEATD_PID" 2>/dev/null || true
    wait "$SEATD_PID" 2>/dev/null || true
  fi
  rm -f -- "$WESTON_CONFIG"
}
trap cleanup EXIT HUP INT TERM

# No libseat builtin backend is available in the pinned Alpine package, so use
# the packaged seatd daemon.  SEATD_VTBOUND=0 is intentional for this kernel's
# no-VT display path; the daemon still brokers DRM access through libseat.
if [ ! -S "$SEATD_SOCKET" ]; then
  SEATD_SOCK="$SEATD_SOCKET" "$SEATD" -u "$(id -un)" -g "$SEATD_GROUP" -l info \
    >/run/seatd-native.log 2>&1 &
  SEATD_PID=$!
  SEATD_STARTED=1
  ready=0
  i=0
  while [ "$i" -lt 50 ]; do
    if [ -S "$SEATD_SOCKET" ]; then
      ready=1
      break
    fi
    kill -0 "$SEATD_PID" 2>/dev/null || break
    i=$((i + 1))
    sleep 0.1
  done
  [ "$ready" = 1 ] || die "seatd did not create $SEATD_SOCKET; see /run/seatd-native.log"
fi

cat > "$WESTON_CONFIG" <<EOF
[core]
backend=drm-backend.so
renderer=pixman
shell=desktop-shell.so
idle-time=0

[output]
name=$WESTON_OUTPUT
mode=$WESTON_MODE
scale=$WESTON_SCALE

[shell]
panel-position=top
startup-animation=none

[input-method]
path=$WESTON_KEYBOARD
EOF
chmod 600 "$WESTON_CONFIG"

printf 'start-weston-native: launching Weston 14 DRM/Pixman card=%s output=%s mode=%s scale=%s\n' \
  "$DRM_CARD" "$WESTON_OUTPUT" "$WESTON_MODE" "$WESTON_SCALE"
set --
if [ "$WESTON_XWAYLAND" = 1 ] || {
  [ "$WESTON_XWAYLAND" = auto ] && [ -x /usr/local/bin/start-s22-x11 ] &&
  [ -x /usr/bin/Xwayland ];
}; then
  mkdir -p /tmp/.X11-unix
  chmod 1777 /tmp/.X11-unix
  set -- --xwayland
fi
"$WESTON" \
  --backend=drm-backend.so \
  --renderer=pixman \
  --drm-device="$DRM_CARD" \
  --seat="$WESTON_SEAT" \
  --socket="$WESTON_SOCKET" \
  --continue-without-input \
  --config="$WESTON_CONFIG" "$@" &
WESTON_PID=$!

ready=0
i=0
while [ "$i" -lt 100 ]; do
  if [ -S "$RUNTIME_DIR/$WESTON_SOCKET" ]; then
    ready=1
    break
  fi
  kill -0 "$WESTON_PID" 2>/dev/null || break
  i=$((i + 1))
  sleep 0.1
done
[ "$ready" = 1 ] || die "Weston did not create $RUNTIME_DIR/$WESTON_SOCKET; see /run/weston-native.log"

# Weston starts the configured input-method client itself.  Starting another
# copy would conflict with the compositor's single input-method seat binding.

printf 'start-weston-native: launching terminal mode=%s; compositor remains after terminal close\n' \
  "$WESTON_TERMINAL_MODE"
terminal_status=0
if [ "$#" -gt 0 ] && [ -x /usr/local/bin/start-s22-x11 ]; then
  DISPLAY=:0 /usr/local/bin/start-s22-x11 || terminal_status=$?
elif [ -x /usr/local/bin/s22-shell ]; then
  WAYLAND_DISPLAY="$WESTON_SOCKET" "$WESTON_TERMINAL" "$WESTON_TERMINAL_MODE" \
    --shell=/usr/local/bin/s22-shell || terminal_status=$?
else
  WAYLAND_DISPLAY="$WESTON_SOCKET" "$WESTON_TERMINAL" "$WESTON_TERMINAL_MODE" || terminal_status=$?
fi
printf 'start-weston-native: terminal exited status=%s; leaving compositor running\n' "$terminal_status"

# Keep the compositor alive after the terminal exits.  Cleanup only runs when
# Weston itself exits or this launcher is stopped.
weston_status=0
wait "$WESTON_PID" || weston_status=$?
exit "$weston_status"
