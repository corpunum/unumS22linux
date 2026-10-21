#!/bin/sh
# s22-rec: record the main (bottom) microphone to a mono 48 kHz 16-bit WAV.
#
# Native phone root, as root.  Default (2026-10-21): the stock firmware-graph
# path, as the stock AudioProxy records: media-mic route (UAIF6 -> NSRC1 ->
# WDMA1, VDSP4 = wdma1-tdmdemux0-normal-capture -> VPCMIN_DAI0) captured from
# the calliope_10 frontend (pcm 110), 4 x S16_LE channels, 480-frame periods.
# S22_AUDIO_PATH=direct uses the hardware-direct UAIF6 (DMIC TDM, 4 slots) ->
# NSRC4 -> WDMA4 (pcm 16) path.  Either way the raw stream is 4 x 16-bit
# slots and the main mic is slot 0.  A faulted Calliope (failsafe OFFLINE) is
# reset the way the stock HAL does before recording and after a failure.
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
# The once-per-boot defaults batch resets NSRC4/WDMA4 among 320 controls; s22-say
# checks the same marker under its own lock, so serialize on a shared lock
# (2026-10-21: a concurrent first use re-applied defaults mid-capture -> EIO).
(
  flock 7
  if [ ! -e /run/s22-audio-defaults ]; then
    run $OPT/usr/bin/amixer -c0 -q -s < "$MIX/defaults.amixer" && touch /run/s22-audio-defaults
  fi
) 7>/run/s22-audio-defaults.lock
# S22_REC_RAW=/path (inside the chroot) names the raw capture file, so a
# caller can read the 4-slot S16_LE stream live while it is recorded
# (s22-converse runs its voice-activity detection on it).
TMP=${S22_REC_RAW:-/tmp/s22-rec-$$.raw}
GRAPH=1; [ "${S22_AUDIO_PATH:-graph}" = direct ] && GRAPH=
abox_recover() {
  [ "$(cat /proc/abox/failsafe/online 2>/dev/null)" = OFFLINE ] || return 0
  echo "s22-rec: ABOX OFFLINE, failsafe reset" >&2
  printf CALLIOPE > /proc/abox/failsafe/reset
  i=0; while [ $i -lt 20 ] && [ "$(cat /proc/abox/failsafe/online)" = OFFLINE ]; do sleep 0.25; i=$((i + 1)); done
}
cleanup() {
  run $OPT/usr/bin/amixer -c0 -q -s < "$MIX/media-mic.reset.amixer"
  [ -n "$GRAPH" ] || { amx cset 'name=ABOX NSRC4' RESERVED; amx cset 'name=ABOX WDMA4 Channel' 2; }
  rm -f "$CHROOT$TMP"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
abox_recover
run $OPT/usr/bin/amixer -c0 -q -s < "$MIX/media-mic.amixer"
if [ -n "$GRAPH" ]; then
  DEV=110; AOPTS="--period-size=480 --buffer-size=1920"
else
  DEV=16; AOPTS=
  amx cset 'name=ABOX NSRC4' UAIF6
  amx cset 'name=ABOX UAIF6 Width' 16
  amx cset 'name=ABOX WDMA4 Width' 16
  amx cset 'name=ABOX WDMA4 Channel' 4
fi
[ -n "${S22_REC_READY:-}" ] && : > "$S22_REC_READY"
# Raw capture: a stopped (SIGINT) recording needs no WAV header fix-up.
timeout $((SECS + 10)) chroot "$CHROOT" /usr/bin/env LD_LIBRARY_PATH=$OPT/usr/lib \
  $OPT/usr/bin/arecord -q -D hw:0,$DEV $AOPTS -f S16_LE -r 48000 -c 4 -t raw -d "$SECS" "$TMP" &
APID=$!
STOPPED=
if [ -n "$STOP" ]; then
  while kill -0 "$APID" 2>/dev/null; do
    if [ -e "$STOP" ]; then STOPPED=1; kill -INT "$APID" 2>/dev/null; break; fi
    sleep 0.05
  done
fi
wait "$APID"; rc=$?
[ "$rc" -eq 0 ] || { [ -n "$STOPPED" ] && [ -s "$CHROOT$TMP" ]; } || { abox_recover; exit 1; }
run /usr/bin/python3 -c '
import sys, wave, array
raw = open(sys.argv[1], "rb").read()
a = array.array("h", raw[:len(raw) // 8 * 8]); mono = a[0::4]
dst = wave.open(sys.argv[2], "wb"); dst.setnchannels(1); dst.setsampwidth(2)
dst.setframerate(48000); dst.writeframes(mono.tobytes()); dst.close()' "$TMP" "$TMP.mono" || exit 1
cp "$CHROOT$TMP.mono" "$OUT.tmp" && mv "$OUT.tmp" "$OUT"; rm -f "$CHROOT$TMP.mono"
