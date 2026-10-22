#!/bin/sh
# Rig side of the S22 owner-voice / Greek-speech loop (run on the rig as the desktop user).
#   s22-whisper.service          whisper-server large-v3-turbo on 100.76.5.104:8891 (phone ASR, el/en auto)
#   s22-voxcpm-tailnet.service   100.76.5.104:8890 -> voxcpm-greek 127.0.0.1:8890 (owner "antonis" voice)
# Both bind the Tailscale address only. Checks free memory before starting whisper (shares 128 GB with Halogen).
set -eu
H=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$HOME/.local/lib/s22-voice" "$HOME/.config/systemd/user"
install -m 755 "$H/tcp-forward.py" "$HOME/.local/lib/s22-voice/tcp-forward.py"
install -m 644 "$H/s22-whisper.service" "$H/s22-voxcpm-tailnet.service" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
avail=$(awk '/MemAvailable/ {print int($2/1048576)}' /proc/meminfo)
if [ "$avail" -lt 8 ]; then
  echo "only ${avail} GB available: not starting s22-whisper (needs ~2 GB + headroom)" >&2
else
  systemctl --user enable --now s22-whisper.service
fi
systemctl --user enable --now s22-voxcpm-tailnet.service
