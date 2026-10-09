#!/bin/sh
# Build the S22 Paradee bundle on the rig (x86_64 Ubuntu, cross to aarch64).
# Output: $OUT/paradee-s22-aarch64.tar.gz (+ .sha256), ~20 MB compressed, which
# install-paradee.sh unpacks on the phone. Nothing here touches the phone.
#
#   sh tools/hardware/tts/paradee/build.sh [OUT_DIR]       (default ~/models/paradee/s22-dist)
#   PARADEE_TEST=1 sh .../build.sh   also renders a WAV under qemu-aarch64 with
#                                    Arch Linux ARM's espeak-ng 1.52 (the phone's)
#
# Needs: aarch64-linux-gnu-gcc, curl, sha256sum. Optional: a python with
# onnxruntime (PARADEE_PY, default ~/datasets/paradee-eval/venv/bin/python) to
# pre-convert the model to ORT format (halves session start-up); without it the
# bundle carries only the .onnx and s22-say falls back to it.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${1:-$HOME/models/paradee/s22-dist}
WORK=${PARADEE_WORK:-$HOME/datasets/paradee-eval/build-s22}
PY=${PARADEE_PY:-$HOME/datasets/paradee-eval/venv/bin/python}
ORT_V=1.30.0
ORT_SHA=e16a27a8ed330bbc698df7330b0cf56e722f354e3bcc92118682c74ef3c3e3da
HF=https://huggingface.co/sahilmahendrakar/Paradee-8M-v1.0/resolve/v1.0
MODEL_SHA=60e8f8a1bc7c546488154e9d99ecac6e9c50baf3f4b684c5b0de48ea03b698eb

mkdir -p "$WORK" "$OUT"
cd "$WORK"
fetch() {  # url file sha
  [ -f "$2" ] && echo "$3  $2" | sha256sum -c --status || curl -sSLf -o "$2" "$1"
  echo "$3  $2" | sha256sum -c --quiet
}
fetch https://github.com/microsoft/onnxruntime/releases/download/v$ORT_V/onnxruntime-linux-aarch64-$ORT_V.tgz ort.tgz $ORT_SHA
fetch $HF/onnx/paradee_int8.onnx paradee_int8.onnx $MODEL_SHA
[ -d onnxruntime-linux-aarch64-$ORT_V ] || tar xzf ort.tgz
O=$WORK/onnxruntime-linux-aarch64-$ORT_V

B=$WORK/paradee
rm -rf "$B"; mkdir -p "$B/bin"
aarch64-linux-gnu-gcc -O2 -Wall -s -o "$B/bin/paradee-tts" "$HERE/paradee-tts.c" \
  -I"$O/include" -L"$O/lib" -lonnxruntime -ldl -Wl,-rpath,'$ORIGIN'
cp -L "$O/lib/libonnxruntime.so.1" "$B/bin/" && aarch64-linux-gnu-strip "$B/bin/libonnxruntime.so.1"
cp "$O/LICENSE" "$B/bin/LICENSE.onnxruntime"; cp "$O/ThirdPartyNotices.txt" "$B/bin/" 2>/dev/null || true
cp paradee_int8.onnx "$B/"
curl -sSLf -o "$B/LICENSE" $HF/LICENSE
if "$PY" -c 'import onnxruntime' 2>/dev/null; then
  rm -rf ortfmt; mkdir ortfmt; cp paradee_int8.onnx ortfmt/
  "$PY" -m onnxruntime.tools.convert_onnx_models_to_ort ortfmt --optimization_style Fixed --target_platform arm >/dev/null
  cp ortfmt/paradee_int8.ort "$B/"
else
  echo "build.sh: no python onnxruntime at $PY; bundle has no .ort (slower start-up)" >&2
fi
(cd "$B" && find . -type f ! -name SHA256SUMS | sort | xargs sha256sum > SHA256SUMS)
tar czf "$OUT/paradee-s22-aarch64.tar.gz" -C "$WORK" paradee
(cd "$OUT" && sha256sum paradee-s22-aarch64.tar.gz > paradee-s22-aarch64.tar.gz.sha256)
ls -la "$OUT"/paradee-s22-aarch64.tar.gz*; cat "$OUT/paradee-s22-aarch64.tar.gz.sha256"

if [ "${PARADEE_TEST:-0}" = 1 ]; then
  # qemu smoke test with the phone's espeak-ng (Arch Linux ARM 1.52) and
  # symbol stubs for the audio libs libpcaudio links (never called).
  A=$WORK/alarm; mkdir -p "$A/root" "$A/stubs"
  M=http://mirror.archlinuxarm.org/aarch64/extra
  for p in espeak-ng-1.52.0-1 pcaudiolib-1.3-1 libsonic-0.2.0-2; do
    [ -f "$A/$p.pkg.tar.xz" ] || curl -sSLf --retry 3 -o "$A/$p.pkg.tar.xz" "$M/$p-aarch64.pkg.tar.xz" ||
      curl -sSLf --retry 3 -o "$A/$p.pkg.tar.xz" "http://de.mirror.archlinuxarm.org/aarch64/extra/$p-aarch64.pkg.tar.xz"
    tar xJf "$A/$p.pkg.tar.xz" -C "$A/root" 2>/dev/null
  done
  aarch64-linux-gnu-objdump -T "$A/root/usr/lib/libpcaudio.so.0" | python3 -c '
import sys, re, collections
d = collections.defaultdict(set)
for l in sys.stdin:
    m = re.search(r"\*UND\*\s+\S+\s+\(((?:ALSA|PULSE)[^)]*)\)\s+(\S+)", l)
    if m: d[m.group(1)].add(m.group(2))
fn = lambda ss: "".join("int %s(void){return -1;}\n" % s for s in sorted(ss))
open("'"$A"'/stubs/alsa.c", "w").write(fn(d["ALSA_0.9"] | d["ALSA_0.9.0rc4"]))
open("'"$A"'/stubs/alsa.map", "w").write("ALSA_0.9 { global: %s; local: *; };\nALSA_0.9.0rc4 { global: %s; } ALSA_0.9;\n" % ("; ".join(sorted(d["ALSA_0.9"])), "; ".join(sorted(d["ALSA_0.9.0rc4"]))))
open("'"$A"'/stubs/pulse.c", "w").write(fn(d["PULSE_0"]))
open("'"$A"'/stubs/pulse.map", "w").write("PULSE_0 { global: %s; local: *; };\n" % "; ".join(sorted(d["PULSE_0"])))
'
  cd "$A/stubs"
  aarch64-linux-gnu-gcc -shared -fPIC -Wl,-soname,libasound.so.2 -Wl,--version-script=alsa.map -o libasound.so.2 alsa.c
  for s in libpulse.so.0 libpulse-simple.so.0; do
    aarch64-linux-gnu-gcc -shared -fPIC -Wl,-soname,$s -Wl,--version-script=pulse.map -o $s pulse.c
  done
  m=$B/paradee_int8.ort; [ -f "$m" ] || m=$B/paradee_int8.onnx
  qemu-aarch64-static -L /usr/aarch64-linux-gnu -E LD_LIBRARY_PATH="$A/root/usr/lib:$A/stubs" \
    "$B/bin/paradee-tts" --espeak-data="$A/root/usr/share" --model="$m" \
    --output-filename="$WORK/qemu-test.wav" "Hello Antonis. This is your S22 speaking with a neural voice."
  ls -la "$WORK/qemu-test.wav"
fi
