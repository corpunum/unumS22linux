#!/bin/sh
# s22-sip-voice: keep the SIP voice-loop agent answering calls (s22-sip listen
# handles one call and exits). Tailscale only (--allow-from 100.64.0.0/10).
# Software audio only: no speaker, no mic; the phone stays muted.
LOG=/srv/s22/state/sip/voice-loop.log
mkdir -p /srv/s22/state/sip /mnt/omarchy-trial/tmp/s22-sip
while :; do
  TS=$(ip -4 -o addr show tailscale0 2>/dev/null | awk "{print \$4}" | cut -d/ -f1)
  if [ -n "$TS" ]; then
    python3 /srv/s22/hardware/bin/s22-sip listen --bind "$TS" --sip-port 5060 --voice-loop \
      --preset s22-outer --work-dir /mnt/omarchy-trial/tmp/s22-sip --allow-from 100.64.0.0/10 >> "$LOG" 2>&1 < /dev/null
  fi
  sleep 2
done
