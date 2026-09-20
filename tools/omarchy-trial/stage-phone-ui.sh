#!/usr/bin/env bash
# Host-side, fresh-RAM-root UI staging helper.  This script deliberately does
# not start Hyprland, reboot, flash, install packages, or invoke a service.
# Usage: run once from the host repository while the phone is on its verified
# USB-Ethernet SSH endpoint.  It requires the fresh-root phone-trial helper and
# refuses an already-mounted /mnt/omarchy-trial.  After it prints staged-ready,
# the separate phone-direct-trial.sh may be selected explicitly; model-bench
# preparation remains a separate tools/model-bench/stage-phone.sh action.
set -euo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$project_dir"

known_hosts="$project_dir/evidence/native-linux-20260919/native-v2-known-hosts"
[[ -s "$known_hosts" ]] || { echo "Missing verified host-key file: $known_hosts" >&2; exit 1; }

ssh_phone() {
  "$project_dir/tools/s22-ssh" "$@"
}
scp_phone() {
  scp -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$known_hosts" \
    -o ConnectTimeout=5 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
    "$1" "root@10.55.0.2:$2"
}

arch_archive=rootfs/arch-omarchy-trial.tar.gz
arch_sha=ecb5ab0c75abb0bbb5e41cb1b9c017229974b87ec061c51110a580435439104d
payload=tools/omarchy-trial/omarchy-ui-candidate/s22-ui-file-payload.tar
payload_sha=695807f42efbafcd6db8d35ffc0c81696ffd528a78dfa2c550b6ce5192ca46c0
supplement=tools/omarchy-trial/omarchy-ui-candidate/s22-osk-supplement.tar
supplement_sha=4d08785c056594ee8bdec6d5d9978ae20d06d51a029a7b7d97bff45a55185173
aquamarine=rootfs/aquamarine-s22-lib-gcc16/libaquamarine.so.0.15.1
aquamarine_sha=6fb3f4377ac3d14e4d74a18bdc5316e0acc23779e6894a1dab449cdb294d42ca

check_hash() {
  local file=$1 expected=$2 actual
  actual=$(sha256sum "$file" | awk '{print $1}')
  [[ "$actual" == "$expected" ]] || { echo "Host hash mismatch: $file" >&2; exit 1; }
}
check_hash "$arch_archive" "$arch_sha"
check_hash "$payload" "$payload_sha"
check_hash "$supplement" "$supplement_sha"
check_hash "$aquamarine" "$aquamarine_sha"

for f in tools/omarchy-trial/hyprland-minimal.lua \
  tools/omarchy-trial/hyprland-display-patch.lua \
  tools/omarchy-trial/hyprland-trial.lua \
  tools/omarchy-trial/hyprland-omarchy-display.lua \
  tools/omarchy-trial/hyprland-omarchy-ui.lua \
  tools/omarchy-trial/phone-direct-trial.sh \
  tools/omarchy-trial/omarchy-ui-candidate/launch-ui.sh \
  tools/omarchy-trial/omarchy-ui-candidate/shell-trial.json \
  tools/linux-rootfs/s22-chat-http.py; do
  [[ -f "$f" ]] || { echo "Missing staging input: $f" >&2; exit 1; }
done

# The phone-side helper is copied and run only for the fresh prepare action.
scp_phone tools/omarchy-trial/phone-trial.sh /tmp/s22-phone-trial.sh
ssh_phone 'chmod 0755 /tmp/s22-phone-trial.sh; if mountpoint -q /mnt/omarchy-trial; then echo "Refusing active trial mount" >&2; exit 1; fi; /bin/sh /tmp/s22-phone-trial.sh prepare'
ssh_phone 'mountpoint -q /mnt/omarchy-trial && awk '\''$2 == "/mnt/omarchy-trial" && $3 == "tmpfs" {ok=1} END {exit !ok}'\'' /proc/mounts'

copy_checked() {
  local local_file=$1 remote_tmp=$2 expected=$3 actual
  ssh_phone 'mountpoint -q /mnt/omarchy-trial && awk '\''$2 == "/mnt/omarchy-trial" && $3 == "tmpfs" {ok=1} END {exit !ok}'\'' /proc/mounts'
  scp_phone "$local_file" "$remote_tmp"
  actual=$(ssh_phone "sha256sum '$remote_tmp' | cut -d ' ' -f 1")
  [[ "$actual" == "$expected" ]] || { echo "Phone hash mismatch before use: $remote_tmp" >&2; exit 1; }
}

copy_checked "$arch_archive" /mnt/omarchy-trial/.s22-arch-omarchy-trial.tar.gz "$arch_sha"
ssh_phone "tar -xzf /mnt/omarchy-trial/.s22-arch-omarchy-trial.tar.gz -C /mnt/omarchy-trial && rm -f /mnt/omarchy-trial/.s22-arch-omarchy-trial.tar.gz"

copy_checked "$payload" /mnt/omarchy-trial/.s22-ui-file-payload.tar "$payload_sha"
copy_checked "$supplement" /mnt/omarchy-trial/.s22-osk-supplement.tar "$supplement_sha"
ssh_phone 'set -eu; tar -xf /mnt/omarchy-trial/.s22-ui-file-payload.tar -C /mnt/omarchy-trial; tar -xf /mnt/omarchy-trial/.s22-osk-supplement.tar -C /mnt/omarchy-trial; rm -f /mnt/omarchy-trial/.s22-ui-file-payload.tar /mnt/omarchy-trial/.s22-osk-supplement.tar'

copy_checked "$aquamarine" /mnt/omarchy-trial/.libaquamarine.so.0.15.1 "$aquamarine_sha"
ssh_phone 'install -d -m 0755 /mnt/omarchy-trial/opt/s22-aquamarine; install -m 0755 /mnt/omarchy-trial/.libaquamarine.so.0.15.1 /mnt/omarchy-trial/opt/s22-aquamarine/libaquamarine.so.0.15.1; ln -sfn libaquamarine.so.0.15.1 /mnt/omarchy-trial/opt/s22-aquamarine/libaquamarine.so.14; ln -sfn libaquamarine.so.14 /mnt/omarchy-trial/opt/s22-aquamarine/libaquamarine.so; rm -f /mnt/omarchy-trial/.libaquamarine.so.0.15.1'

for f in tools/omarchy-trial/hyprland-minimal.lua \
  tools/omarchy-trial/hyprland-display-patch.lua \
  tools/omarchy-trial/hyprland-trial.lua \
  tools/omarchy-trial/hyprland-omarchy-display.lua \
  tools/omarchy-trial/hyprland-omarchy-ui.lua \
  tools/omarchy-trial/phone-direct-trial.sh; do
  copy_checked "$f" "/mnt/omarchy-trial/.$(basename "$f")" "$(sha256sum "$f" | awk '{print $1}')"
  ssh_phone "install -m 0644 '/mnt/omarchy-trial/.$(basename "$f")' '/mnt/omarchy-trial/root/$(basename "$f")'; rm -f '/mnt/omarchy-trial/.$(basename "$f")'"
done

copy_checked tools/omarchy-trial/omarchy-ui-candidate/launch-ui.sh /mnt/omarchy-trial/.launch-ui.sh "$(sha256sum tools/omarchy-trial/omarchy-ui-candidate/launch-ui.sh | awk '{print $1}')"
copy_checked tools/omarchy-trial/omarchy-ui-candidate/shell-trial.json /mnt/omarchy-trial/.shell-trial.json "$(sha256sum tools/omarchy-trial/omarchy-ui-candidate/shell-trial.json | awk '{print $1}')"
copy_checked tools/linux-rootfs/s22-chat-http.py /mnt/omarchy-trial/.s22-chat.py "$(sha256sum tools/linux-rootfs/s22-chat-http.py | awk '{print $1}')"
ssh_phone 'set -eu; install -d -m 0755 /mnt/omarchy-trial/opt/s22-ui /mnt/omarchy-trial/usr/local/bin; install -m 0755 /mnt/omarchy-trial/.launch-ui.sh /mnt/omarchy-trial/opt/s22-ui/launch-ui.sh; install -m 0644 /mnt/omarchy-trial/.shell-trial.json /mnt/omarchy-trial/opt/s22-ui/shell-trial.json; ln -sfn /opt/omarchy-source/shell /mnt/omarchy-trial/opt/s22-ui/shell; install -m 0755 /mnt/omarchy-trial/.s22-chat.py /mnt/omarchy-trial/usr/local/bin/s22-chat; rm -f /mnt/omarchy-trial/.launch-ui.sh /mnt/omarchy-trial/.shell-trial.json /mnt/omarchy-trial/.s22-chat.py'

ssh_phone 'set -eu; mkdir -p /mnt/omarchy-trial/usr/share/fonts/s22-dejavu; cp /usr/share/fonts/dejavu/DejaVuSans.ttf /usr/share/fonts/dejavu/DejaVuSansMono.ttf /mnt/omarchy-trial/usr/share/fonts/s22-dejavu/; sh /tmp/s22-phone-trial.sh mount-runtime; chroot /mnt/omarchy-trial /usr/bin/fc-cache -f /usr/share/fonts; chroot /mnt/omarchy-trial /usr/bin/glib-compile-schemas /usr/share/glib-2.0/schemas; cp /mnt/omarchy-trial/root/phone-direct-trial.sh /tmp/phone-direct-trial.sh; touch /mnt/omarchy-trial/root/.s22-ui-staged-ready'
ssh_phone 'set -eu; mountpoint -q /mnt/omarchy-trial; cp /etc/resolv.conf /mnt/omarchy-trial/etc/resolv.conf.s22-next; mv /mnt/omarchy-trial/etc/resolv.conf.s22-next /mnt/omarchy-trial/etc/resolv.conf'
echo 'Staged-ready marker created at /mnt/omarchy-trial/root/.s22-ui-staged-ready.'
echo 'No GUI, reboot, flash, package manager, or model-bench staging was invoked.'
echo 'Model-bench prerequisite remains a separate explicit tools/model-bench/stage-phone.sh action.'
echo 'The desktop chat additionally needs bash tools/model-bench/start-phone-server.sh and a ready health endpoint.'
