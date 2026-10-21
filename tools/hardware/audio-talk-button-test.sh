#!/bin/sh
# audio-talk-button-test.sh: end-to-end test of the unum-fastshell Talk button
# (it runs s22-assistant like the volume-up hook). Taps Talk, speaks QUESTION
# through the phone speaker into the open mic window, then records the filler +
# answer with s22-rec. Speech level is capped at LEVEL via /run/s22-say.level.
#   audio-talk-button-test.sh X Y LEVEL "QUESTION"   (X Y in screen pixels, 1080x2340;
#   fs-inject.py takes normalised 0..1 coordinates)
set -u
X=$1; Y=$2; LEVEL=$3; Q=$4
CH=/mnt/omarchy-trial
echo "$LEVEL" > /run/s22-say.level; rm -f /run/s22-say.history /run/s22-assistant.rec-ready
S22_SAY_OUT=$CH/tmp/talk-q.wav s22-say --engine piper "$Q" || exit 1
N0=$(wc -l < /srv/s22/buttons/assistant.jsonl 2>/dev/null || echo 0)
python3 /srv/s22/state/fastshell/tools/fs-inject.py tap "$(awk -v v="$X" 'BEGIN{printf "%.4f", v/1080}')" "$(awk -v v="$Y" 'BEGIN{printf "%.4f", v/2340}')" 120
i=0; while [ ! -e /run/s22-assistant.rec-ready ] && [ $i -lt 100 ]; do sleep 0.1; i=$((i+1)); done
[ -e /run/s22-assistant.rec-ready ] || { echo "assistant did not start recording"; rm -f /run/s22-say.level; exit 1; }
echo "rec-ready after $((i*100)) ms"
sleep 0.8
s22-say --wav /tmp/talk-q.wav; echo "question played rc=$?"
sleep 5.5
s22-rec 40 $CH/tmp/talk-answer.wav > /tmp/talk-rec.log 2>&1 &
RP=$!
j=0; while ps -o args | grep -v grep | grep -q "s22-assistant" && [ $j -lt 120 ]; do sleep 1; j=$((j+1)); done
echo "assistant finished after ~${j}s"
sleep 1; kill -INT $RP 2>/dev/null; wait $RP
rm -f /run/s22-say.level
echo "== s22-say history (newest first)"; cat /run/s22-say.history
echo "== new assistant.jsonl entries"; tail -n +$((N0+1)) /srv/s22/buttons/assistant.jsonl
ls -l $CH/tmp/talk-answer.wav
