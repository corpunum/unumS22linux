#!/usr/bin/env bash
# Build a small, key-only Alpine ARM64 userspace for the S22 native-Linux work.
# The downloaded minirootfs is pinned and verified against Alpine's SHA256.
set -Eeuo pipefail

ALPINE_VERSION="${ALPINE_VERSION:-3.24.2}"
ALPINE_BRANCH="${ALPINE_VERSION%.*}"
ARCH="aarch64"
MIRROR="${ALPINE_MIRROR:-https://dl-cdn.alpinelinux.org/alpine}"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
OUT_DIR="$PROJECT_DIR/rootfs/alpine-arm64"
ARCHIVE="$PROJECT_DIR/rootfs/alpine-arm64.tar.gz"
WORK_DIR="${S22_ROOTFS_WORK_DIR:-/tmp/s22-alpine-arm64-build}"
SSH_PUBLIC_KEY="${S22_SSH_PUBLIC_KEY:-/home/corpunum/.ssh/id_ed25519.pub}"
SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1789819200}"
HOST_USER=$(id -un)
HOST_GROUP=$(id -gn)

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "missing host tool: $1"; }
for tool in curl sha256sum tar sudo chroot findmnt mktemp install; do need "$tool"; done
QEMU=$(command -v qemu-aarch64-static) || die "qemu-aarch64-static is required"
[[ $EUID -ne 0 ]] || die "run as the normal host user; sudo is used for rootfs operations"
sudo -n true >/dev/null 2>&1 || die "passwordless sudo is required for chroot/rootfs setup"
[[ ! -e "$OUT_DIR" ]] || die "refusing to overwrite existing output: $OUT_DIR"
[[ ! -e "$ARCHIVE" ]] || die "refusing to overwrite existing archive: $ARCHIVE"
[[ -f "$SSH_PUBLIC_KEY" ]] || die "public SSH key not found: $SSH_PUBLIC_KEY"
[[ "$SSH_PUBLIC_KEY" == *.pub ]] || die "SSH key input must be a .pub file"
grep -Eq '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com)[[:space:]]' "$SSH_PUBLIC_KEY" \
  || die "SSH key input does not look like a public key"

BASE_URL="$MIRROR/v$ALPINE_BRANCH/releases/$ARCH"
MINIROOTFS="alpine-minirootfs-$ALPINE_VERSION-$ARCH.tar.gz"
mkdir -p "$WORK_DIR" "$PROJECT_DIR/rootfs"
DOWNLOAD="$WORK_DIR/$MINIROOTFS"
CHECKSUM="$DOWNLOAD.sha256"

printf '%s\n' "Downloading and verifying $BASE_URL/$MINIROOTFS"
curl --fail --location --retry 3 --proto '=https' --tlsv1.2 -o "$DOWNLOAD" "$BASE_URL/$MINIROOTFS"
curl --fail --location --retry 3 --proto '=https' --tlsv1.2 -o "$CHECKSUM" "$BASE_URL/$MINIROOTFS.sha256"
( cd "$WORK_DIR" && sha256sum --check "$(basename -- "$CHECKSUM")" )

STAGE=$(mktemp -d /tmp/s22-alpine-arm64.XXXXXX)
cleanup() {
  if [[ -n "${STAGE:-}" && -d "$STAGE" ]]; then sudo -n rm -rf -- "$STAGE"; fi
}
trap cleanup EXIT

sudo -n tar --extract --gzip --file "$DOWNLOAD" --directory "$STAGE" --numeric-owner
sudo -n install -D -m 0755 "$QEMU" "$STAGE/usr/bin/qemu-aarch64-static"

# Keep repository selection explicit and stable-series only.
sudo -n tee "$STAGE/etc/apk/repositories" >/dev/null <<EOF
$MIRROR/v$ALPINE_BRANCH/main
$MIRROR/v$ALPINE_BRANCH/community
EOF

# apk package scripts run inside the target architecture. A temporary copy of
# the host resolver is used only while bootstrapping.
sudo -n cp --dereference /etc/resolv.conf "$STAGE/etc/resolv.conf"
sudo -n chroot "$STAGE" /usr/bin/qemu-aarch64-static /sbin/apk update
sudo -n chroot "$STAGE" /usr/bin/qemu-aarch64-static /sbin/apk add \
  openrc openssh iproute2 python3 git curl ca-certificates

sudo -n sh -c "cat > '$STAGE/etc/resolv.conf'" <<'EOF'
nameserver 1.1.1.1
nameserver 9.9.9.9
EOF
sudo -n install -D -m 0644 "$SSH_PUBLIC_KEY" "$STAGE/root/.ssh/authorized_keys"
sudo -n chmod 0700 "$STAGE/root/.ssh"
sudo -n chown -R 0:0 "$STAGE/root/.ssh"

sudo -n sh -c "cat > '$STAGE/etc/hostname'" <<'EOF'
s22-linux
EOF
sudo -n sh -c "cat > '$STAGE/etc/hosts'" <<'EOF'
127.0.0.1 localhost s22-linux
::1       localhost s22-linux
EOF
sudo -n sh -c "cat > '$STAGE/etc/network/interfaces'" <<'EOF'
auto lo
iface lo inet loopback

auto eth0
iface eth0 inet dhcp
EOF
sudo -n sh -c "cat > '$STAGE/etc/ssh/sshd_config'" <<'EOF'
Port 22
Protocol 2
PermitRootLogin prohibit-password
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
AllowUsers root
UseDNS no
X11Forwarding no
PrintMotd yes
Subsystem sftp /usr/lib/ssh/sftp-server
EOF
sudo -n sh -c "cat > '$STAGE/etc/motd'" <<'EOF'
S22 native Linux Alpine ARM64 bring-up
SSH: public-key authentication only; root password login is disabled.
EOF

sudo -n chroot "$STAGE" /usr/bin/qemu-aarch64-static /sbin/rc-update add networking boot
sudo -n chroot "$STAGE" /usr/bin/qemu-aarch64-static /sbin/rc-update add sshd default

printf '%s\n' 'Inspecting mount covering the staged tree before chroot checks:'
findmnt -T "$STAGE" -o TARGET,SOURCE,FSTYPE,OPTIONS

CHECK_OUTPUT="$WORK_DIR/qemu-check.txt"
sudo -n chroot "$STAGE" /usr/bin/qemu-aarch64-static /bin/sh -eu -c '
  test -x /bin/sh
  test -x /usr/bin/python3
  test -x /usr/bin/ssh
  test -x /usr/sbin/sshd
  /usr/bin/python3 -c "import ssl, sys; print(\"python=%s ssl=%s\" % (sys.version.split()[0], ssl.OPENSSL_VERSION.split()[0]))"
  /usr/bin/ssh -V 2>&1
  mkdir -p /tmp/rootfs-sshd-check
  /usr/bin/ssh-keygen -q -t ed25519 -N "" -f /tmp/rootfs-sshd-check/ssh_host_ed25519_key
  /usr/sbin/sshd -t -f /etc/ssh/sshd_config -h /tmp/rootfs-sshd-check/ssh_host_ed25519_key
  rm -rf /tmp/rootfs-sshd-check
  /sbin/openrc --version
' | tee "$CHECK_OUTPUT"

BUILD_UTC=$(date -u -d "@$SOURCE_DATE_EPOCH" +%Y-%m-%dT%H:%M:%SZ)
BASE_SHA256=$(awk 'NR == 1 { print $1 }' "$CHECKSUM")
sudo -n sh -c "cat > '$STAGE/rootfs-build-manifest.txt'" <<EOF
S22 native Linux ARM64 rootfs build
build_utc=$BUILD_UTC
source_date_epoch=$SOURCE_DATE_EPOCH
alpine_version=$ALPINE_VERSION
architecture=$ARCH
minirootfs_url=$BASE_URL/$MINIROOTFS
minirootfs_sha256=$BASE_SHA256
repositories=$MIRROR/v$ALPINE_BRANCH/{main,community}
packages=openrc openssh iproute2 python3 git curl ca-certificates
ssh_public_key_source=$SSH_PUBLIC_KEY
ssh_private_key_included=no
qemu_validation=see qemu-check.txt beside this manifest
native_boot_proven=no
EOF
sudo -n cp "$CHECK_OUTPUT" "$STAGE/qemu-check.txt"
sudo -n rm -f "$STAGE/usr/bin/qemu-aarch64-static"

# Normalize metadata that is not meaningful to userspace bring-up, then archive
# with sorted names and numeric ownership. Package contents/permissions remain.
sudo -n find "$STAGE" -xdev \( -type f -o -type d \) -exec touch --date="@$SOURCE_DATE_EPOCH" {} +
sudo -n mv -- "$STAGE" "$OUT_DIR"
STAGE=

sudo -n tar --create --gzip --file "$ARCHIVE" --directory "$OUT_DIR" \
  --sort=name --mtime="@$SOURCE_DATE_EPOCH" --owner=0 --group=0 --numeric-owner .
ARCHIVE_SHA256=$(sha256sum "$ARCHIVE" | awk '{print $1}')
ROOTFS_BYTES=$(sudo -n du -sb "$OUT_DIR" | awk '{print $1}')
ARCHIVE_BYTES=$(stat -c '%s' "$ARCHIVE")
printf 'archive_sha256=%s\narchive_bytes=%s\nrootfs_bytes=%s\n' \
  "$ARCHIVE_SHA256" "$ARCHIVE_BYTES" "$ROOTFS_BYTES" | sudo -n tee "$OUT_DIR/artifact-manifest.txt"
sudo -n chown "$HOST_USER:$HOST_GROUP" "$ARCHIVE"

printf '%s\n' "Built $OUT_DIR" "Built $ARCHIVE" "SHA256 $ARCHIVE_SHA256"
