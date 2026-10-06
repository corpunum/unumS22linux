#!/bin/sh
# Install the on-device neural TTS used by s22-say into the Arch chroot.
# Runs on the phone as root (needs internet). ~350 MB under /opt/s22-tts.
set -eu
D=/mnt/omarchy-trial/opt/s22-tts
B=https://github.com/k2-fsa/sherpa-onnx/releases/download
V=v1.13.8
mkdir -p "$D/bin"
cd "$D"
if [ ! -x bin/sherpa-onnx-offline-tts ]; then
  curl -sSLf "$B/$V/sherpa-onnx-$V-linux-aarch64-static.tar.bz2" | tar xj \
    "sherpa-onnx-$V-linux-aarch64-static/bin/sherpa-onnx-offline-tts"
  mv "sherpa-onnx-$V-linux-aarch64-static/bin/sherpa-onnx-offline-tts" bin/
  rm -rf "sherpa-onnx-$V-linux-aarch64-static"
fi
for m in sherpa-onnx-supertonic-tts-int8-2026-03-06 kitten-nano-en-v0_8-int8 \
         vits-piper-en_US-lessac-medium kokoro-int8-en-v0_19; do
  [ -d "$m" ] || curl -sSLf "$B/tts-models/$m.tar.bz2" | tar xj
done
chown -R root:root "$D"
echo "installed: $(ls "$D")"
