#!/bin/sh
# s22-rec: record the main (bottom) microphone to a mono 48 kHz 16-bit WAV.
#
# Native phone root, as root.  Uses the hardware-direct capture path
# UAIF6 (DMIC TDM, 4 slots) -> NSRC4 -> WDMA4 (pcm 16): the firmware-graph
# WDMA1 path of the stock media-mic route does not run natively yet.  The
# DMA delivers 4 x 16-bit slots; the main mic is slot 0.
#
#   s22-rec SECONDS OUT.wav
set -u
[ $# -eq 2 ] || { echo "usage: s22-rec SECONDS OUT.wav" >&2; exit 2; }
SECS=$1; OUT=$2
CHROOT=/mnt/omarchy-trial; OPT=/opt/s22-audio; MIX=$CHROOT$OPT/mixer
run() { chroot "$CHROOT" /usr/bin/env LD_LIBRARY_PATH=$OPT/usr/lib "$@"; }
amx() { run $OPT/usr/bin/amixer -c0 -q "$@"; }
mkdir -p "$CHROOT/dev/snd"
for n in /sys/class/sound/*; do
  [ -f "$n/dev" ] || continue
  d="$CHROOT/dev/snd/${n##*/}"; [ -e "$d" ] && continue
  IFS=: read ma mi < "$n/dev"; mknod -m 660 "$d" c "$ma" "$mi" && chgrp audio "$d"
done
exec 8>/run/s22-rec.lock; flock 8
if [ ! -e /run/s22-audio-defaults ]; then
  run $OPT/usr/bin/amixer -c0 -q -s < "$MIX/defaults.amixer" && touch /run/s22-audio-defaults
fi
TMP=/tmp/s22-rec-$$.wav
cleanup() {
  run $OPT/usr/bin/amixer -c0 -q -s < "$MIX/media-mic.reset.amixer"
  amx cset 'name=ABOX NSRC4' RESERVED; amx cset 'name=ABOX WDMA4 Channel' 2
  rm -f "$CHROOT$TMP"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
run $OPT/usr/bin/amixer -c0 -q -s < "$MIX/media-mic.amixer"
amx cset 'name=ABOX NSRC4' UAIF6
amx cset 'name=ABOX UAIF6 Width' 16
amx cset 'name=ABOX WDMA4 Width' 16
amx cset 'name=ABOX WDMA4 Channel' 4
timeout $((SECS + 10)) chroot "$CHROOT" /usr/bin/env LD_LIBRARY_PATH=$OPT/usr/lib \
  $OPT/usr/bin/arecord -q -D hw:0,16 -f S16_LE -r 48000 -c 4 -d "$SECS" "$TMP" || exit 1
run /usr/bin/python3 -c '
import sys, wave, array
src = wave.open(sys.argv[1]); n = src.getnframes()
a = array.array("h", src.readframes(n)); mono = a[0::4]
dst = wave.open(sys.argv[2], "wb"); dst.setnchannels(1); dst.setsampwidth(2)
dst.setframerate(48000); dst.writeframes(mono.tobytes()); dst.close()' "$TMP" "$TMP.mono" || exit 1
cp "$CHROOT$TMP.mono" "$OUT.tmp" && mv "$OUT.tmp" "$OUT"; rm -f "$CHROOT$TMP.mono"
