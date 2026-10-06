#!/bin/sh
# s22-say: speak text (espeak-ng) or play a WAV through the S22 speakers.
#
# Runs on the native phone root as root. Arch tools live in the chroot at
# /mnt/omarchy-trial (alsa-utils + espeak-ng unpacked under /opt/s22-audio from
# signature-checked Arch Linux ARM packages). Mixer batches under
# /opt/s22-audio/mixer are generated from the stock vendor mixer_paths.xml by
# tools/hardware/audio/gen-mixer-batches.py: defaults are applied once per
# boot (as the Android HAL does), then the chosen route, then defaults again
# to close the route (only that route's controls: <route>.reset).  Needs sectiongraph_tplg.bin + amp firmware staged by
# start-persistent-desktop (stage_optional_audio_firmware / fallback answer).
#
#   s22-say [--out speaker|earpiece|bottom|top] [--amp N] [--diag DIR] TEXT...
#   s22-say [--out ...] --wav /path/inside/chroot.wav
set -u
CHROOT=/mnt/omarchy-trial
OPT=/opt/s22-audio
MIX=$CHROOT$OPT/mixer
PCM=6                      # RDMA6: hardware SPUS path (RDMA2/3 need the firmware graph tick, not working yet)
OUT=speaker
AMP=60                     # espeak-ng amplitude (0-200, default 100)
DIAG=
WAV=
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT=$2; shift 2;;
    --amp) AMP=$2; shift 2;;
    --diag) DIAG=$2; shift 2;;
    --wav) WAV=$2; shift 2;;
    --) shift; break;;
    *) break;;
  esac
done
TEXT="$*"
[ -n "$WAV" ] || [ -n "$TEXT" ] || { echo "usage: s22-say [--out speaker|earpiece|bottom|top] TEXT" >&2; exit 2; }
case "$OUT" in
  speaker) ROUTE=media-speaker;; earpiece) ROUTE=media-handset;;
  bottom) ROUTE=media-speaker-bottom;; top) ROUTE=media-speaker-top;;
  *) echo "unknown --out $OUT" >&2; exit 2;;
esac

run() { chroot "$CHROOT" /usr/bin/env LD_LIBRARY_PATH=$OPT/usr/lib ESPEAK_DATA_PATH=$OPT/usr/share "$@"; }
batch() { run $OPT/usr/bin/amixer -c0 -q -s < "$MIX/$1.amixer"; }

[ -e /sys/class/sound/card0 ] || { echo "no sound card" >&2; exit 1; }
mkdir -p "$CHROOT/dev/snd"
for n in /sys/class/sound/*; do
  [ -f "$n/dev" ] || continue
  d="$CHROOT/dev/snd/${n##*/}"; [ -e "$d" ] && continue
  IFS=: read ma mi < "$n/dev"; mknod -m 660 "$d" c "$ma" "$mi" && chgrp audio "$d"
done

exec 9>/run/s22-say.lock
flock 9
if [ ! -e /run/s22-audio-defaults ]; then
  batch defaults && touch /run/s22-audio-defaults
fi

if [ -z "$WAV" ]; then
  WAV=/tmp/s22-say-$$.wav
  run $OPT/usr/bin/espeak-ng -a "$AMP" -s 150 -w "$WAV" "$TEXT" || exit 1
  CLEANWAV=1
fi

trap 'batch "$ROUTE.reset"; [ -n "${CLEANWAV:-}" ] && rm -f "$CHROOT$WAV"' EXIT
trap 'exit 130' INT TERM
batch "$ROUTE" || exit 1

if [ -n "$DIAG" ]; then
  mkdir -p "$DIAG"
  ( i=0; while [ $i -lt 40 ]; do
      { echo "t=$i"; cat /proc/asound/card0/pcm${PCM}p/sub0/status; } >> "$DIAG/status.txt" 2>&1
      i=$((i + 1)); sleep 0.25; done ) &
  dmesg > "$DIAG/dmesg-before.txt" 2>&1
fi

# s22spk (alsa-s22.conf) = plug -> hw:0,6 at 48 kHz stereo; RDMA6's own ASRC
# garbles 22.05 kHz mono (espeak's native format).
play() { timeout 60 chroot "$CHROOT" /usr/bin/env LD_LIBRARY_PATH=$OPT/usr/lib \
  ALSA_CONFIG_PATH=$OPT/mixer/alsa-s22.conf $OPT/usr/bin/aplay -q -D s22spk "$@"; }
# ABOX powers Calliope down ~0.5 s after the last close; a short silent
# stream first makes sure the firmware is up before the real trigger.
play -t raw -f S16_LE -r 48000 -c 2 -s 4800 /dev/zero >/dev/null 2>&1
play "$WAV"
rc=$?
if [ -n "$DIAG" ]; then
  wait
  dmesg > "$DIAG/dmesg-after.txt" 2>&1
  echo "aplay_rc=$rc" > "$DIAG/result.txt"
fi
exit $rc
