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
# Neural TTS (default): sherpa-onnx (static aarch64 build) with on-device
# models under /opt/s22-tts in the chroot, CPU only, pinned to the big cores.
# Engines: supertonic (default; int8, RTF ~0.16), kitten (nano int8, ~0.29),
# piper (lessac-medium, ~0.26), kokoro (int8 v0.19, ~1.2, best prosody,
# slower than real time), espeak (formant). If the neural engine fails, it
# falls back to espeak-ng. S22_TTS_ENGINE sets the default.
#
#   s22-say [--out speaker|earpiece|bottom|top] [--engine E] [--sid N] [--amp N] [--diag DIR] TEXT...
#   s22-say [--out ...] --wav /path/inside/chroot.wav
#
# Render only (silent verification): with S22_SAY_OUT=/host/path.wav in the
# environment the speech is synthesized (or the --wav input copied) to that
# file and NOTHING is played: no mixer batch, no route, no amp access, no
# aplay. The phone stays exactly as muted as it was.
set -u
CHROOT=/mnt/omarchy-trial
OPT=/opt/s22-audio
MIX=$CHROOT$OPT/mixer
PCM=6                      # RDMA6: hardware SPUS path (RDMA2/3 need the firmware graph tick, not working yet)
OUT=speaker
AMP=60                     # espeak-ng amplitude (0-200, default 100)
DIAG=
WAV=
ENGINE=${S22_TTS_ENGINE:-supertonic}
SID=${S22_TTS_SID:-0}       # supertonic sid 0-4 = female styles (0 = F1, owner's choice: female), 5-9 = male
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT=$2; shift 2;;
    --amp) AMP=$2; shift 2;;
    --diag) DIAG=$2; shift 2;;
    --wav) WAV=$2; shift 2;;
    --engine) ENGINE=$2; shift 2;;
    --sid) SID=$2; shift 2;;
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

TTS=/opt/s22-tts
neural_tts() {  # $1 = wav path inside the chroot, $2 = text
  M=$TTS
  case "$ENGINE" in
    supertonic) S=$M/sherpa-onnx-supertonic-tts-int8-2026-03-06
      set -- "$1" "$2" --supertonic-duration-predictor=$S/duration_predictor.int8.onnx \
        --supertonic-text-encoder=$S/text_encoder.int8.onnx \
        --supertonic-vector-estimator=$S/vector_estimator.int8.onnx \
        --supertonic-vocoder=$S/vocoder.int8.onnx --supertonic-tts-json=$S/tts.json \
        --supertonic-unicode-indexer=$S/unicode_indexer.bin --supertonic-voice-style=$S/voice.bin;;
    kitten) S=$M/kitten-nano-en-v0_8-int8
      set -- "$1" "$2" --kitten-model=$S/model.int8.onnx --kitten-voices=$S/voices.bin \
        --kitten-tokens=$S/tokens.txt --kitten-data-dir=$S/espeak-ng-data;;
    piper) S=$M/vits-piper-en_US-lessac-medium
      set -- "$1" "$2" --vits-model=$S/en_US-lessac-medium.onnx --vits-tokens=$S/tokens.txt \
        --vits-data-dir=$S/espeak-ng-data;;
    kokoro) S=$M/kokoro-int8-en-v0_19
      set -- "$1" "$2" --kokoro-model=$S/model.int8.onnx --kokoro-voices=$S/voices.bin \
        --kokoro-tokens=$S/tokens.txt --kokoro-data-dir=$S/espeak-ng-data;;
    *) return 1;;
  esac
  out=$1 text=$2; shift 2
  timeout 120 chroot "$CHROOT" taskset -c 4-7 $TTS/bin/sherpa-onnx-offline-tts \
    --num-threads=4 --sid="$SID" "$@" --output-filename="$out" "$text" >/dev/null 2>&1 &&
    [ -s "$CHROOT$out" ]
}

if [ -n "${S22_SAY_OUT:-}" ]; then   # render only: never touches the sound card
  if [ -n "$WAV" ]; then
    cp "$CHROOT$WAV" "$S22_SAY_OUT.tmp" && mv "$S22_SAY_OUT.tmp" "$S22_SAY_OUT"; exit $?
  fi
  R=/tmp/s22-say-render-$$.wav
  if ! neural_tts "$R" "$TEXT"; then
    [ "$ENGINE" = espeak ] || echo "s22-say: $ENGINE failed, falling back to espeak-ng" >&2
    run $OPT/usr/bin/espeak-ng -a "$AMP" -s 150 -w "$R" "$TEXT" || { rm -f "$CHROOT$R"; exit 1; }
  fi
  cp "$CHROOT$R" "$S22_SAY_OUT.tmp" && mv "$S22_SAY_OUT.tmp" "$S22_SAY_OUT"; rc=$?
  rm -f "$CHROOT$R"; exit $rc
fi

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
  if ! neural_tts "$WAV" "$TEXT"; then
    [ "$ENGINE" = espeak ] || echo "s22-say: $ENGINE failed, falling back to espeak-ng" >&2
    run $OPT/usr/bin/espeak-ng -a "$AMP" -s 150 -w "$WAV" "$TEXT" || exit 1
  fi
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
