#!/bin/sh
# Install the on-device speech recognition used by s22-assistant into the
# Arch chroot (/opt/s22-asr, ~190 MB) plus the push-to-talk earcons.
# Runs on the phone as root (needs internet).  Every archive is checked
# against the SHA-256 published on the k2-fsa/sherpa-onnx GitHub release
# (whisper/moonshine-v1 assets carry no digest there; they are not used).
set -eu
D=/mnt/omarchy-trial/opt/s22-asr
B=https://github.com/k2-fsa/sherpa-onnx/releases/download
V=v1.13.8
mkdir -p "$D/bin"
cd "$D"
fetch() {  # url sha256 -> verified archive on stdout path
  f=/tmp/s22-asr-$$.tar.bz2
  curl -sSLf "$1" -o "$f"
  echo "$2  $f" | sha256sum -c - >/dev/null || { rm -f "$f"; echo "checksum mismatch: $1" >&2; exit 1; }
  echo "$f"
}
if [ ! -x bin/sherpa-onnx-offline ]; then
  f=$(fetch "$B/$V/sherpa-onnx-$V-linux-aarch64-static.tar.bz2" \
    d1c621ed68f2ff77738505ba0137b947141c27e431c12f266a6b4ba218e78a4e)
  tar xjf "$f" "sherpa-onnx-$V-linux-aarch64-static/bin/sherpa-onnx-offline"
  mv "sherpa-onnx-$V-linux-aarch64-static/bin/sherpa-onnx-offline" bin/
  rm -rf "sherpa-onnx-$V-linux-aarch64-static" "$f"
fi
while read -r m sum; do
  [ -d "$m" ] && continue
  f=$(fetch "$B/asr-models/$m.tar.bz2" "$sum"); tar xjf "$f"; rm -f "$f"
done <<LIST
sherpa-onnx-nemo-parakeet_tdt_ctc_110m-en-36000-int8 17f945007b52ccd8b7200ffc7c5652e9e8e961dfdf479cefcabd06cf5703630b
sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27 9ec31b342d8fa3240c3b81b8f82e1cf7e3ac467c93ca5a999b741d5887164f8d
LIST
# Earcons: rising two-tone = listening, falling = got it (48 kHz stereo).
chroot /mnt/omarchy-trial /usr/bin/python3 - <<'PY'
import array, math, wave
def tone(freqs, path, d=0.11, amp=9000, sr=48000):
    a = array.array('h')
    for f in freqs:
        n = int(sr * d)
        for i in range(n):
            v = int(amp * min(1, i / 240, (n - i) / 480) * math.sin(2 * math.pi * f * i / sr))
            a.extend((v, v))
    w = wave.open(path, 'wb'); w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
    w.writeframes(a.tobytes()); w.close()
tone([880, 1320], '/opt/s22-asr/listen.wav')
tone([1320, 880], '/opt/s22-asr/done.wav')
PY
chown -R root:root "$D"
echo "installed: $(ls "$D")"
