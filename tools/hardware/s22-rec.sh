#!/bin/sh
# s22-rec: record the main (bottom) microphone to a mono 48 kHz 16-bit WAV.
#
# Native phone root, as root.  Uses the hardware-direct capture path
# UAIF6 (DMIC TDM, 4 slots) -> NSRC4 -> WDMA4 (pcm 16): the firmware-graph
# WDMA1 path of the stock media-mic route does not run natively yet.  The
# DMA delivers 4 x 16-bit slots; the main mic is slot 0.
#
#   s22-rec [--stop-file FILE] SECONDS OUT.wav
#
# With --stop-file, SECONDS is the maximum: recording ends early as soon as
# FILE exists (push-to-talk: the button daemon writes it on key release).
# With S22_REC_READY=FILE in the environment, FILE is created when capture
# actually starts (route setup takes ~0.7 s), so a caller can cue the user.
set -u
STOP=
if [ "${1:-}" = --stop-file ]; then STOP=${2:-}; shift 2 || exit 2; fi
[ $# -eq 2 ] || { echo "usage: s22-rec [--stop-file FILE] SECONDS OUT.wav" >&2; exit 2; }
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
# S22_REC_RAW=/path (inside the chroot) names the raw capture file, so a
# caller can read the 4-slot S16_LE stream live while it is recorded
# (s22-converse runs its voice-activity detection on it).
TMP=${S22_REC_RAW:-/tmp/s22-rec-$$.raw}
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
[ -n "${S22_REC_READY:-}" ] && : > "$S22_REC_READY"
# Raw capture: a stopped (SIGINT) recording needs no WAV header fix-up.
timeout $((SECS + 10)) chroot "$CHROOT" /usr/bin/env LD_LIBRARY_PATH=$OPT/usr/lib \
  $OPT/usr/bin/arecord -q -D hw:0,16 -f S16_LE -r 48000 -c 4 -t raw -d "$SECS" "$TMP" &
APID=$!
STOPPED=
if [ -n "$STOP" ]; then
  while kill -0 "$APID" 2>/dev/null; do
    if [ -e "$STOP" ]; then STOPPED=1; kill -INT "$APID" 2>/dev/null; break; fi
    sleep 0.05
  done
fi
wait "$APID"; rc=$?
[ "$rc" -eq 0 ] || { [ -n "$STOPPED" ] && [ -s "$CHROOT$TMP" ]; } || exit 1
run /usr/bin/python3 -c '
import sys, wave, array
raw = open(sys.argv[1], "rb").read()
a = array.array("h", raw[:len(raw) // 8 * 8]); mono = a[0::4]
dst = wave.open(sys.argv[2], "wb"); dst.setnchannels(1); dst.setsampwidth(2)
dst.setframerate(48000); dst.writeframes(mono.tobytes()); dst.close()' "$TMP" "$TMP.mono" || exit 1
cp "$CHROOT$TMP.mono" "$OUT.tmp" && mv "$OUT.tmp" "$OUT"; rm -f "$CHROOT$TMP.mono"
