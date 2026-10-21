#!/bin/sh
# audio-calliope-rec.sh: record the main mic through the firmware graph the way
# the stock AudioProxy does: stock media-mic route (UAIF6 -> NSRC1 -> WDMA1,
# VDSP4 = wdma1-tdmdemux0-normal-capture -> VPCMIN_DAI0) and capture from the
# calliope_10 frontend (card 0 device 110). Raw S16_LE, CH channels.
#   audio-calliope-rec.sh SECONDS RAW_OUT_IN_CHROOT [CH] [DEV]
set -u
SECS=$1; OUT=$2; CHN=${3:-2}; DEV=${4:-110}
CH=/mnt/omarchy-trial; OPT=/opt/s22-audio; MIX=$CH$OPT/mixer
run() { chroot $CH /usr/bin/env LD_LIBRARY_PATH=$OPT/usr/lib "$@"; }
exec 8>/run/s22-rec.lock; flock 8
( flock 7; [ -e /run/s22-audio-defaults ] || { run $OPT/usr/bin/amixer -c0 -q -s < $MIX/defaults.amixer && touch /run/s22-audio-defaults; } ) 7>/run/s22-audio-defaults.lock
trap 'run $OPT/usr/bin/amixer -c0 -q -s < $MIX/media-mic.reset.amixer' EXIT
run $OPT/usr/bin/amixer -c0 -q -s < $MIX/media-mic.amixer
run $OPT/usr/bin/arecord -q -D hw:0,$DEV -f S16_LE -r 48000 -c $CHN --period-size=480 --buffer-size=1920 -t raw -d "$SECS" "$OUT" &
P=$!
for i in 1 2 3; do sleep 0.5; printf 'c%s pcm%sc: %s\n' $i $DEV "$(grep -E 'state|hw_ptr' /proc/asound/card0/pcm${DEV}c/sub0/status | tr -s ' ' | tr '\n' ' ')"; done
wait $P; echo "arecord rc=$?"
ls -l "$CH$OUT" 2>/dev/null || ls -l "$OUT"
