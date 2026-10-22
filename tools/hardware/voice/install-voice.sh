#!/bin/sh
# Install the natural-voice pieces on the phone (run from this repo on the host).
#   install-voice.sh [PHONE]   (default root@100.95.191.103)
# Backs up the installed s22-assistant / s22-converse first, copies the scripts, and (re)renders any
# missing filler clips with the phone's own TTS (about 4 minutes the first time; existing clips are kept).
# s22_loudnorm.py (-16 LUFS) is what s22-say and the filler renderer normalise with; s22_filler.py is the shared
# clip player (s22-touchd, s22-filler). To level clips that already exist: s22-render-fillers.py --renorm.
# s22_rigvoice.py + s22-say: the owner's own voice from the rig (engine antonis, Piper el-antonis fallback) and the
# rig's Greek/English whisper ASR; the rig side is voice/rig/install-rig.sh. After a voice change, re-render
# the clips with: s22-render-fillers.py --force (strict: needs the rig).
set -eu
PHONE=${1:-root@100.95.191.103}
H=$(cd "$(dirname "$0")/.." && pwd)
ssh "$PHONE" 'cd /srv/s22/hardware/bin && for f in s22-assistant s22-converse s22-say; do [ -e $f.bak-voice-20261021 ] || cp -p $f $f.bak-voice-20261021; [ -e $f.bak-ownervoice-20261022 ] || cp -p $f $f.bak-ownervoice-20261022; done; mkdir -p /srv/s22/hardware/voice'
scp -q "$H/s22_voice.py" "$H/s22_loudnorm.py" "$H/s22_filler.py" "$H/s22_rigvoice.py" "$PHONE:/srv/s22/hardware/bin/"
scp -q "$H/s22-say.sh" "$PHONE:/srv/s22/hardware/bin/s22-say"
scp -q "$H/s22-assistant.py" "$PHONE:/srv/s22/hardware/bin/s22-assistant"
scp -q "$H/s22-converse.py" "$PHONE:/srv/s22/hardware/bin/s22-converse"
scp -q "$H"/voice/s22-render-fillers.py "$H"/voice/filler-phrases.json "$H"/voice/voice-e2e.sh "$H"/voice/converse-e2e.py "$PHONE:/srv/s22/hardware/voice/"
ssh "$PHONE" 'chmod +x /srv/s22/hardware/bin/s22-say /srv/s22/hardware/bin/s22-assistant /srv/s22/hardware/bin/s22-converse /srv/s22/hardware/voice/*.py /srv/s22/hardware/voice/*.sh
  nohup python3 /srv/s22/hardware/voice/s22-render-fillers.py > /srv/s22/state/render-fillers.log 2>&1 < /dev/null &'
echo "installed; filler render log: /srv/s22/state/render-fillers.log on the phone"
