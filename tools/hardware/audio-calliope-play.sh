#!/bin/sh
# audio-calliope-play.sh: play a WAV through a firmware-graph frontend PCM
# (calliope_N, card 0 device 100+N) with the stock media-speaker route, the way
# the stock AudioProxy does (get_pcm_device_number: 100/102/105 for playback),
# sampling the frontend and RDMA2/RDMA3 status. Amp volume is NOT touched
# (whatever s22-buttons set; 0 = muted).
#   [S22_APLAY_OPTS="--period-size=N --buffer-size=M"] audio-calliope-play.sh DEV WAV_IN_CHROOT [VDSP3_ROUTE]
set -u
DEV=$1; WAV=$2; V3=${3:-rdma2-sbll-vpcmout}
CH=/mnt/omarchy-trial; OPT=/opt/s22-audio; MIX=$CH$OPT/mixer
run() { chroot $CH /usr/bin/env LD_LIBRARY_PATH=$OPT/usr/lib "$@"; }
A="run $OPT/usr/bin/amixer -c0 -q"
exec 9>/run/s22-say.lock; flock 9
( flock 7; [ -e /run/s22-audio-defaults ] || { run $OPT/usr/bin/amixer -c0 -q -s < $MIX/defaults.amixer && touch /run/s22-audio-defaults; } ) 7>/run/s22-audio-defaults.lock
trap 'run $OPT/usr/bin/amixer -c0 -q -s < $MIX/media-speaker.reset.amixer' EXIT
run $OPT/usr/bin/amixer -c0 -q -s < $MIX/media-speaker.amixer
$A cset "name=ABOX VDSP3" "$V3"
T0=$(cut -d' ' -f1 /proc/uptime)
run $OPT/usr/bin/aplay -q ${S22_APLAY_OPTS:-} -D hw:0,$DEV "$WAV" > /tmp/acp-aplay.log 2>&1 &
P=$!
for i in 1 2 3 4 5 6; do
  sleep 0.35
  for d in ${DEV}p 2p 3p; do
    printf 't%s pcm%s: %s\n' $i $d "$(grep -E 'state|hw_ptr' /proc/asound/card0/pcm$d/sub0/status 2>/dev/null | tr -s ' ' | tr '\n' ' ' || true)"
  done
done
wait $P; echo "aplay rc=$? $(cat /tmp/acp-aplay.log)"
echo "T0=$T0"
dmesg | awk -v t0="$T0" '{ s=$0; sub(/^\[ */,"",s); split(s,a,"]"); if (a[1]+0 >= t0-0.5) print }' | grep -iE 'abox|rdma|calliope|vdma|tplg|err|fail' | grep -v -E 'wdt|rtc' | head -80
