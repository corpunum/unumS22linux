#!/bin/sh
# voice-e2e.sh: run ONE push-to-talk turn through the real voice path while the phone's own mic
# records the speaker, and print wall-clock marks so filler/answer onsets can be timed.
# Runs on the phone as root. The speech level is s22-say's default (4/10, restored afterwards).
#   voice-e2e.sh REC_SECONDS (--wav CHROOT_WAV | --text TEXT) OUT_PREFIX
# Writes OUT_PREFIX.rec.wav (mic), OUT_PREFIX.marks (rec_start/assistant_start/assistant_end as epoch seconds)
# and the assistant record is the last line of /srv/s22/buttons/assistant.jsonl.
set -u
SECS=$1; MODE=$2; ARG=$3; OUT=$4
CH=/mnt/omarchy-trial
READY=/run/voice-e2e.ready
rm -f "$READY"
S22_REC_READY=$READY /srv/s22/hardware/bin/s22-rec "$SECS" "$CH/tmp/voice-e2e.wav" >/tmp/voice-e2e-rec.log 2>&1 &
RP=$!
i=0; while [ ! -e "$READY" ] && [ $i -lt 100 ]; do sleep 0.05; i=$((i + 1)); done
REC_T=$(python3 -c "import time;print(round(time.time(),3))")
sleep 0.5
A0=$(python3 -c "import time;print(round(time.time(),3))")
/srv/s22/hardware/bin/s22-assistant "$MODE" "$ARG"
A1=$(python3 -c "import time;print(round(time.time(),3))")
wait $RP
printf 'rec_start=%s\nassistant_start=%s\nassistant_end=%s\n' "$REC_T" "$A0" "$A1" > "$OUT.marks"
cp "$CH/tmp/voice-e2e.wav" "$OUT.rec.wav"
cat "$OUT.marks"
