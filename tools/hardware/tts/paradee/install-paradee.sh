#!/bin/sh
# Install (or roll back) the Paradee TTS engine for s22-say. Runs ON THE PHONE
# as root, native root, like install-s22-tts.sh. Needs no network and no pip:
# copy paradee-s22-aarch64.tar.gz (+ .sha256) from the rig first.
#
#   install-paradee.sh /path/paradee-s22-aarch64.tar.gz   install + render a test WAV (no playback)
#   install-paradee.sh --rollback                          restore the previous dir (or remove)
#
# Layout inside the chroot: /opt/s22-tts/paradee/{bin/paradee-tts, bin/libonnxruntime.so.1,
# paradee_int8.ort, paradee_int8.onnx}. The phonemizer is the chroot's own
# espeak-ng under /opt/s22-audio (dlopen'ed), so nothing else is installed.
set -eu
CHROOT=${S22_CHROOT:-/mnt/omarchy-trial}
D=$CHROOT/opt/s22-tts
OPT=/opt/s22-audio
if [ "${1:-}" = --rollback ]; then
  rm -rf "$D/paradee.failed"
  [ -d "$D/paradee" ] && mv "$D/paradee" "$D/paradee.failed"
  if [ -d "$D/paradee.prev" ]; then mv "$D/paradee.prev" "$D/paradee"; echo "rolled back to previous paradee"
  else echo "paradee removed (moved to $D/paradee.failed); s22-say --engine paradee now falls back to espeak"; fi
  exit 0
fi
T=${1:?usage: install-paradee.sh BUNDLE.tar.gz | --rollback}
if [ -f "$T.sha256" ]; then (cd "$(dirname "$T")" && sha256sum -c "$(basename "$T").sha256"); fi
rm -rf "$D/paradee.new"; mkdir -p "$D/paradee.new"
tar xzf "$T" -C "$D/paradee.new" --strip-components=1
(cd "$D/paradee.new" && sha256sum -c --quiet SHA256SUMS)
chown -R root:root "$D/paradee.new"
rm -rf "$D/paradee.prev"
[ -d "$D/paradee" ] && mv "$D/paradee" "$D/paradee.prev"
mv "$D/paradee.new" "$D/paradee"
M=/opt/s22-tts/paradee/paradee_int8.ort; [ -f "$CHROOT$M" ] || M=/opt/s22-tts/paradee/paradee_int8.onnx
# Render only. The phone stays muted: do NOT play this WAV.
chroot "$CHROOT" /usr/bin/env LD_LIBRARY_PATH=$OPT/usr/lib taskset -c 4-7 \
  /opt/s22-tts/paradee/bin/paradee-tts --num-threads=4 --model=$M \
  --espeak-lib=$OPT/usr/lib/libespeak-ng.so.1 --espeak-data=$OPT/usr/share \
  --output-filename=/tmp/paradee-install-test.wav \
  "Hello Antonis. This is your S22 speaking with a neural voice, running entirely on the phone."
ls -la "$CHROOT/tmp/paradee-install-test.wav"
echo "installed: $D/paradee (previous kept in paradee.prev if any)"
