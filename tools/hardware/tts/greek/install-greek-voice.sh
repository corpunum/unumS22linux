#!/bin/sh
# Install (or roll back) a Greek Piper voice (s22-say --engine el-<voice>). Runs ON THE PHONE as root,
# like install-paradee.sh. Needs no network and no pip: copy the voice tarball from the rig first.
#
#   install-greek-voice.sh /path/vits-piper-el-<voice>.tar.gz   install + render a test WAV (no playback)
#   install-greek-voice.sh --rollback <voice>                    restore the previous dir (or remove)
#
# The tarball holds vits-piper-el-<voice>/{model.onnx, tokens.txt}: a Piper-medium VITS model with
# sherpa-onnx metadata, distilled on the rig from the VoxCPM2 voices. espeak-ng data is shared with
# vits-piper-en_US-lessac-medium. Greek text without --engine goes to el-antonis (S22_TTS_GREEK).
set -eu
CHROOT=${S22_CHROOT:-/mnt/omarchy-trial}
D=$CHROOT/opt/s22-tts
if [ "${1:-}" = --rollback ]; then
  V=vits-piper-el-${2:?usage: install-greek-voice.sh --rollback VOICE}
  rm -rf "$D/$V.failed"
  [ -d "$D/$V" ] && mv "$D/$V" "$D/$V.failed"
  if [ -d "$D/$V.prev" ]; then mv "$D/$V.prev" "$D/$V"; echo "rolled back to previous $V"
  else echo "$V removed (moved to $D/$V.failed)"; fi
  exit 0
fi
T=${1:?usage: install-greek-voice.sh vits-piper-el-VOICE.tar.gz | --rollback VOICE}
if [ -f "$T.sha256" ]; then (cd "$(dirname "$T")" && sha256sum -c "$(basename "$T").sha256"); fi
V=$(basename "$T" .tar.gz)
case "$V" in vits-piper-el-*) ;; *) echo "expected vits-piper-el-<voice>.tar.gz" >&2; exit 2;; esac
rm -rf "$D/$V.new"; mkdir -p "$D/$V.new"
tar -xzf "$T" -C "$D/$V.new" --strip-components=1
[ -s "$D/$V.new/model.onnx" ] && [ -s "$D/$V.new/tokens.txt" ] || { echo "bundle incomplete" >&2; exit 1; }
rm -rf "$D/$V.prev"
[ -d "$D/$V" ] && mv "$D/$V" "$D/$V.prev"
mv "$D/$V.new" "$D/$V"
# Render-only check: the phone is not unmuted.
S22_SAY_OUT=/tmp/$V-test.wav /srv/s22/hardware/bin/s22-say --engine "${V#vits-piper-}" \
  'Καλημέρα! Είμαι το OpenUnum, ο βοηθός σου. Θέλεις να ξεκινήσουμε;'
echo "installed $V; test render /tmp/$V-test.wav ($(wc -c < /tmp/$V-test.wav) bytes)"
