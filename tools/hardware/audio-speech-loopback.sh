#!/bin/sh
# audio-speech-loopback.sh: speak TEXT through the real OpenUnum TTS path
# (s22-ui tts --play -> s22-touchd -> s22-say, firmware-graph audio) while
# s22-rec records the main mic; amp preset to LEVEL (<= 2) for the test and
# put back to its previous raw value afterwards.
#   audio-speech-loopback.sh ENGINE REC_SECONDS REC_OUT_IN_CHROOT LEVEL TEXT...
set -u
ENGINE=$1; SECS=$2; REC=$3; LEVEL=$4; shift 4; TEXT="$*"
[ "$LEVEL" -le 2 ] || { echo "level capped at 2" >&2; exit 2; }
A="chroot /mnt/omarchy-trial /usr/bin/env LD_LIBRARY_PATH=/opt/s22-audio/usr/lib /opt/s22-audio/usr/bin/amixer -c0"
raw() { $A cget "name=$1 Digital PCM Volume" | sed -n 's/^  : values=//p'; }
OLDL=$(raw Left); OLDR=$(raw Right)
trap '$A -q cset "name=Left Digital PCM Volume" "$OLDL"; $A -q cset "name=Right Digital PCM Volume" "$OLDR"; echo "restored L=$OLDL R=$OLDR"' EXIT
s22-rec "$SECS" "/mnt/omarchy-trial$REC" > /tmp/asl-rec.log 2>&1 &
RP=$!
sleep 1
R=$((817 - (10 - LEVEL) * 32))
$A -q cset "name=Left Digital PCM Volume" $R; $A -q cset "name=Right Digital PCM Volume" $R
chroot /mnt/omarchy-trial /usr/local/bin/s22-ui tts --engine "$ENGINE" --play "$TEXT"; echo
wait $RP; echo "rec rc=$? $(cat /tmp/asl-rec.log)"
echo "abox=$(cat /proc/abox/failsafe/online)"
