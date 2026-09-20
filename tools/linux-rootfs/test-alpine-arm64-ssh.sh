#!/usr/bin/env bash
# Loopback-only SSH acceptance check using a temporary key.
set -Eeuo pipefail
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
ROOTFS="$PROJECT_DIR/rootfs/alpine-arm64"
QEMU=$(command -v qemu-aarch64-static) || { echo 'missing qemu-aarch64-static' >&2; exit 1; }
for tool in chroot ssh ssh-keygen sudo mktemp; do
  command -v "$tool" >/dev/null || { echo "missing $tool" >&2; exit 1; }
done
[[ -d "$ROOTFS" ]] || { echo "missing rootfs: $ROOTFS" >&2; exit 1; }
[[ ! -e "$ROOTFS/usr/bin/qemu-aarch64-static" ]] || { echo 'qemu path already exists' >&2; exit 1; }
sudo -n true >/dev/null 2>&1 || { echo 'passwordless sudo is required' >&2; exit 1; }

TEST_DIR=$(mktemp -d /tmp/s22-alpine-ssh.XXXXXX)
SSHD_PID=''
cleanup() {
  if [[ -n "$SSHD_PID" ]]; then sudo -n kill "$SSHD_PID" 2>/dev/null || true; fi
  if [[ -f "$TEST_DIR/authorized_keys.original" ]]; then
    sudo -n cp "$TEST_DIR/authorized_keys.original" "$ROOTFS/root/.ssh/authorized_keys" || true
  fi
  sudo -n rm -f -- "$ROOTFS/usr/bin/qemu-aarch64-static" "$ROOTFS/tmp/ssh-test.conf" \
    "$ROOTFS/tmp/ssh-test-host-key" "$ROOTFS/tmp/ssh-test-host-key.pub" \
    "$ROOTFS/tmp/ssh-test.pid" || true
  rm -rf -- "$TEST_DIR"
}
trap cleanup EXIT

ssh-keygen -q -t ed25519 -N '' -f "$TEST_DIR/id_ed25519"
sudo -n cp "$ROOTFS/root/.ssh/authorized_keys" "$TEST_DIR/authorized_keys.original"
sudo -n install -m 0755 "$QEMU" "$ROOTFS/usr/bin/qemu-aarch64-static"
sudo -n sh -c "cat '$TEST_DIR/id_ed25519.pub' >> '$ROOTFS/root/.ssh/authorized_keys'"
sudo -n chroot "$ROOTFS" /usr/bin/qemu-aarch64-static /usr/bin/ssh-keygen \
  -q -t ed25519 -N '' -f /tmp/ssh-test-host-key
sudo -n sh -c "cat > '$ROOTFS/tmp/ssh-test.conf'" <<'EOF'
Port 22222
ListenAddress 127.0.0.1
HostKey /tmp/ssh-test-host-key
PidFile /tmp/ssh-test.pid
AuthorizedKeysFile /root/.ssh/authorized_keys
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
UseDNS no
StrictModes yes
LogLevel VERBOSE
EOF

sudo -n chroot "$ROOTFS" /usr/bin/qemu-aarch64-static /usr/sbin/sshd \
  -D -e -f /tmp/ssh-test.conf >"$TEST_DIR/sshd.log" 2>&1 &
SSHD_PID=$!
SSH_OUTPUT=''
for _ in $(seq 1 20); do
  if ! kill -0 "$SSHD_PID" 2>/dev/null; then
    cat "$TEST_DIR/sshd.log" >&2
    exit 1
  fi
  if SSH_OUTPUT=$(ssh -q -o BatchMode=yes -o ConnectTimeout=1 \
      -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o IdentitiesOnly=yes -i "$TEST_DIR/id_ed25519" -p 22222 \
      root@127.0.0.1 'printf "uid=%s shell=%s python=" "$(id -u)" "$SHELL"; python3 -c "import sys; print(sys.version.split()[0])"' 2>/dev/null); then
    break
  fi
  sleep 0.25
done
[[ "$SSH_OUTPUT" == uid=0\ shell=/bin/sh\ python=* ]] || {
  echo "SSH acceptance failed: $SSH_OUTPUT" >&2
  cat "$TEST_DIR/sshd.log" >&2
  exit 1
}
printf 'loopback_ssh_acceptance=%s\n' "$SSH_OUTPUT"
