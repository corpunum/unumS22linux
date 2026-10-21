#!/bin/sh
# audio-loopback-check.sh: bounded acoustic loopback on the S22 (native root).
# Plays a short WAV (inside the chroot) through s22-say (RDMA6 -> media-speaker)
# while s22-rec records the main mic (UAIF6 -> NSRC4 -> WDMA4), sampling both
# PCM DMA pointers and the CS35L41 protection telemetry. The amp digital volume
# is raised to LEVEL (s22-buttons scale, 0-10, default 2 = -32 dB) for the test
# only and restored to its previous raw value afterwards.
#   audio-loopback-check.sh WAV_IN_CHROOT REC_OUT_IN_CHROOT [LEVEL]
# S22_LB_PLAY="cmd args" replaces the s22-say player (the WAV path is appended),
# e.g. S22_LB_PLAY="sh /tmp/audio-calliope-play.sh 102" for the firmware graph;
# S22_LB_ARG3 is appended after the WAV (e.g. a VDSP3 route name).
set -u
WAV=$1; REC=$2; LEVEL=${3:-2}
[ "$LEVEL" -le 2 ] || { echo "level capped at 2 (20%)" >&2; exit 2; }
A="chroot /mnt/omarchy-trial /usr/bin/env LD_LIBRARY_PATH=/opt/s22-audio/usr/lib /opt/s22-audio/usr/bin/amixer -c0"
raw_of() { $A cget "name=$1 Digital PCM Volume" | sed -n 's/^  : values=//p'; }
OLDL=$(raw_of Left); OLDR=$(raw_of Right)
RAW=$(( LEVEL == 0 ? 0 : 817 - (10 - LEVEL) * 32 ))
restore() { $A -q cset "name=Left Digital PCM Volume" "$OLDL"; $A -q cset "name=Right Digital PCM Volume" "$OLDR"; echo "restored L=$OLDL R=$OLDR"; }
trap restore EXIT
bd() { for f in max_exc_0 max_exc_1 max_temp_0 max_temp_1 abnm_mute_0 abnm_mute_1; do printf '%s=%s ' $f "$(cat /sys/class/cirrus/cirrus_bd/$f)"; done; echo; }
echo "old raw L=$OLDL R=$OLDR -> test raw $RAW (level $LEVEL)"
echo "bd before: $(bd)"
$A -q cset "name=Left Digital PCM Volume" $RAW; $A -q cset "name=Right Digital PCM Volume" $RAW
s22-rec 5 "/mnt/omarchy-trial$REC" > /tmp/alc-rec.log 2>&1 &
RP=$!
sleep 1.5
${S22_LB_PLAY:-s22-say --wav} "$WAV" ${S22_LB_ARG3:-} > /tmp/alc-say.log 2>&1 &
SP=$!
for i in 1 2 3 4 5 6 7; do
  sleep 0.4
  printf 't%s p6:%s p102:%s c16:%s\n' $i "$(grep hw_ptr /proc/asound/card0/pcm6p/sub0/status | tr -s ' ' || echo closed)" "$(grep hw_ptr /proc/asound/card0/pcm102p/sub0/status | tr -s ' ' || echo closed)" "$(grep hw_ptr /proc/asound/card0/pcm16c/sub0/status | tr -s ' ' || echo closed)"
done
wait $SP; echo "play rc=$? $(grep -E 'aplay rc|abort' /tmp/alc-say.log; head -c 300 /tmp/alc-say.log | grep -v '^t' )"
wait $RP; echo "rec rc=$? $(cat /tmp/alc-rec.log)"
echo "bd after: $(bd)"; echo "say.last: $(cat /run/s22-say.last 2>/dev/null)"
ls -l "/mnt/omarchy-trial$REC"
