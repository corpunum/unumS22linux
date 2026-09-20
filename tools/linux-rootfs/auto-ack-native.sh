#!/usr/bin/env bash
# Host-side native guardian acknowledgement loop for the isolated USB ECM link.
#
# This intentionally does not use adb, reset USB, reboot the phone, or modify a
# partition.  It only acknowledges a reachable native guardian over SSH.
set -Eeuo pipefail

PHONE_IP=${NATIVE_PHONE_IP:-10.55.0.2}
HOST_IP=${NATIVE_HOST_IP:-10.55.0.1}
INTERFACE=${NATIVE_USB_INTERFACE:-enx027322000001}
EXPECTED_MAC=${NATIVE_USB_MAC:-02:73:22:00:00:01}
USB_ID=${NATIVE_USB_ID:-1d6b:0104}
SSH_USER=${NATIVE_SSH_USER:-root}
SSH_KEY=${NATIVE_SSH_KEY:-"$HOME/.ssh/id_ed25519"}

for tool in ip lsusb ssh mktemp awk; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'auto-ack-native: missing required host tool: %s\n' "$tool" >&2
    exit 1
  }
done
[[ -r "$SSH_KEY" ]] || {
  printf 'auto-ack-native: SSH key is not readable: %s\n' "$SSH_KEY" >&2
  exit 1
}

KNOWN_HOSTS=$(mktemp "${TMPDIR:-/tmp}/s22-native-ack-known-hosts.XXXXXX")
chmod 600 "$KNOWN_HOSTS"
cleanup() {
  rm -f -- "$KNOWN_HOSTS"
}
trap cleanup EXIT

# The remote shell checks PID 1 before touching the readiness marker.  The
# marker is deliberately written only after the exact native guardian check.
REMOTE_ACK_COMMAND='test "$(readlink /proc/1/exe 2>/dev/null)" = "/system/bin/native-guardian" || exit 42; touch /run/native-ready; set -- $(cat /proc/uptime); printf "%s\n" "$1"'

route_uses_expected_path() {
  local route=$1
  local -a words=()
  local i have_dev=1 have_src=1
  read -r -a words <<< "$route"
  for ((i = 0; i + 1 < ${#words[@]}; i++)); do
    [[ ${words[i]} == dev && ${words[i + 1]} == "$INTERFACE" ]] && have_dev=0
    [[ ${words[i]} == src && ${words[i + 1]} == "$HOST_IP" ]] && have_src=0
  done
  (( have_dev == 0 && have_src == 0 ))
}

usb_path_ready() {
  local mac route
  [[ -r "/sys/class/net/$INTERFACE/address" ]] || return 1
  mac=$(<"/sys/class/net/$INTERFACE/address")
  mac=${mac,,}
  [[ $mac == "${EXPECTED_MAC,,}" ]] || return 1

  route=$(ip -4 route get "$PHONE_IP" 2>/dev/null || true)
  [[ -n $route ]] || return 1
  route_uses_expected_path "$route" || return 1

  lsusb -d "$USB_ID" >/dev/null 2>&1
}

uptime_decreased() {
  local current=$1 previous=$2
  awk -v current="$current" -v previous="$previous" \
    'BEGIN { exit !((current + 0) < (previous + 0)) }'
}

ACKED=0
LAST_PHONE_UPTIME=''
LAST_PROBE_SECONDS=0
USB_PATH_WAS_READY=0

while :; do
  if ! usb_path_ready; then
    if (( USB_PATH_WAS_READY == 1 )); then
      # A new USB ECM session gets a fresh, process-local host-key file.
      : > "$KNOWN_HOSTS"
      USB_PATH_WAS_READY=0
    fi
    sleep 3
    continue
  fi
  USB_PATH_WAS_READY=1

  if (( ACKED == 1 && SECONDS - LAST_PROBE_SECONDS < 15 )); then
    sleep 3
    continue
  fi
  LAST_PROBE_SECONDS=$SECONDS

  # This is an isolated USB-only link with no untrusted network route.  Strict
  # host-key checking is intentionally disabled here, while UserKnownHostsFile
  # remains an ephemeral per-USB-session file that is deleted on exit.
  phone_uptime=$(ssh -q \
    -o BatchMode=yes \
    -o ConnectTimeout=2 \
    -o ConnectionAttempts=1 \
    -o IdentitiesOnly=yes \
    -o PreferredAuthentications=publickey \
    -o PasswordAuthentication=no \
    -o StrictHostKeyChecking=no \
    -o GlobalKnownHostsFile=/dev/null \
    -o UserKnownHostsFile="$KNOWN_HOSTS" \
    -i "$SSH_KEY" \
    "$SSH_USER@$PHONE_IP" "$REMOTE_ACK_COMMAND" 2>/dev/null || true)

  if [[ ! $phone_uptime =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    sleep 3
    continue
  fi

  reason=initial
  if (( ACKED == 1 )); then
    if [[ -n $LAST_PHONE_UPTIME ]] && uptime_decreased "$phone_uptime" "$LAST_PHONE_UPTIME"; then
      reason=uptime-reset
    else
      LAST_PHONE_UPTIME=$phone_uptime
      continue
    fi
  fi

  printf '%s native_ack success phone_uptime=%s reason=%s\n' \
    "$(date -Is)" "$phone_uptime" "$reason"
  ACKED=1
  LAST_PHONE_UPTIME=$phone_uptime
done
