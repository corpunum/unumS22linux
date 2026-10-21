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
# slower than real time), paradee (Paradee-8M int8, Kokoro af_heart distill,
# own runner under /opt/s22-tts/paradee, see tools/hardware/tts/paradee/),
# el-antonis / el-female (Greek Piper-medium voices distilled from the rig's VoxCPM2
# voices, vits-piper-el-<voice> under /opt/s22-tts, espeak-ng data shared with piper),
# espeak (formant). If the neural engine fails, it
# falls back to espeak-ng. S22_TTS_ENGINE sets the default. Greek text with no --engine given
# goes to S22_TTS_GREEK (default el-antonis) when that voice is installed.
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
PCM=${S22_GRAPH_PCM:-100}  # calliope_0 firmware-graph frontend (S22_AUDIO_PATH=direct: RDMA6 = pcm 6)
OUT=speaker
AMP=60                     # espeak-ng amplitude (0-200, default 100)
DIAG=
WAV=
ENGINE=${S22_TTS_ENGINE:-supertonic}
ENGINE_SET=
SID=${S22_TTS_SID:-0}       # supertonic sid 0-4 = female styles (0 = F1, owner's choice: female), 5-9 = male
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT=$2; shift 2;;
    --amp) AMP=$2; shift 2;;
    --diag) DIAG=$2; shift 2;;
    --wav) WAV=$2; shift 2;;
    --engine) ENGINE=$2; ENGINE_SET=1; shift 2;;
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
# Greek letters are U+0370-03FF (UTF-8 lead bytes 0xCE/0xCF). Checked byte-wise with tr: busybox ash matches
# case patterns per character, so a lone lead byte never matches there.
if [ -z "$ENGINE_SET" ] && [ -n "$TEXT" ]; then
  G=${S22_TTS_GREEK:-el-antonis}
  if [ "$(printf '%s' "$TEXT" | tr -d '\316\317')" != "$TEXT" ] && [ -f "$CHROOT$TTS/vits-piper-$G/model.onnx" ]; then
    ENGINE=$G
  fi
fi
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
    el-*) S=$M/vits-piper-$ENGINE
      # Same text rules as the rig server: product names spelled in Greek, and the Greek question mark ";"
      # read as "?" (the voices learned question intonation from "?").
      t=$(printf '%s' "$2" | sed -e 's/OpenUnum/Όπεν Ούνουμ/g; s/OpenLunum/Όπεν Λούνουμ/g' \
        -e 's/UnumSearch/Ούνουμ Σερτς/g; s/Unum/Ούνουμ/g' \
        -e 's/; /? /g; s/;$/?/; s/; /? /g; s/;$/?/')
      # Lower sampling noise than the VITS defaults (0.667/0.8) and longer phonemes (the distilled voices speak
      # ~30% faster than their teacher): Whisper round-trip CER on held-out Greek 13.9% -> 6.7%, speaker
      # similarity 0.505 -> 0.544.
      set -- "$1" "$t" --vits-model=$S/model.onnx --vits-tokens=$S/tokens.txt \
        --vits-data-dir=$M/vits-piper-en_US-lessac-medium/espeak-ng-data \
        --vits-noise-scale=${S22_EL_NOISE:-0.4} --vits-noise-scale-w=${S22_EL_NOISE_W:-0.6} \
        --vits-length-scale=${S22_EL_LENGTH:-1.4};;
    kokoro) S=$M/kokoro-int8-en-v0_19
      set -- "$1" "$2" --kokoro-model=$S/model.int8.onnx --kokoro-voices=$S/voices.bin \
        --kokoro-tokens=$S/tokens.txt --kokoro-data-dir=$S/espeak-ng-data;;
    paradee)  # Paradee-8M (Kokoro af_heart distill), own runner, not sherpa-onnx
      S=$M/paradee; P=$S/paradee_int8.ort
      [ -f "$CHROOT$P" ] || P=$S/paradee_int8.onnx
      timeout 120 chroot "$CHROOT" /usr/bin/env LD_LIBRARY_PATH=$OPT/usr/lib taskset -c 4-7 \
        $S/bin/paradee-tts --num-threads=4 --model=$P --espeak-lib=$OPT/usr/lib/libespeak-ng.so.1 \
        --espeak-data=$OPT/usr/share --output-filename="$1" "$2" >/dev/null 2>&1 &&
        [ -s "$CHROOT$1" ]
      return;;
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
# Shared with s22-rec: apply the once-per-boot defaults under one lock so a
# concurrent first use cannot re-apply them mid-capture (2026-10-21 race).
(
  flock 7
  if [ ! -e /run/s22-audio-defaults ]; then
    batch defaults && touch /run/s22-audio-defaults
  fi
) 7>/run/s22-audio-defaults.lock

if [ -z "$WAV" ]; then
  WAV=/tmp/s22-say-$$.wav
  if ! neural_tts "$WAV" "$TEXT"; then
    [ "$ENGINE" = espeak ] || echo "s22-say: $ENGINE failed, falling back to espeak-ng" >&2
    run $OPT/usr/bin/espeak-ng -a "$AMP" -s 150 -w "$WAV" "$TEXT" || exit 1
  fi
  CLEANWAV=1
fi

# Speech level (2026-10-21, owner wants the phone to talk): the resting state
# stays muted (s22-keepalive enforce_mute; it skips the check while this
# script holds /run/s22-say.lock). If the amps are at 0, raise them to
# S22_SAY_LEVEL (s22-buttons scale 0-10, default 4 = -24 dB) for this playback
# only and put the previous raw value back on exit. A non-zero level chosen
# with the volume keys is left as it is. S22_SAY_LEVEL=0 plays silently.
amx() { run $OPT/usr/bin/amixer -c0 -q "$@"; }
amp_raw() { run $OPT/usr/bin/amixer -c0 cget "name=$1 Digital PCM Volume" | sed -n 's/^  : values=//p'; }
OLDL=$(amp_raw Left); OLDR=$(amp_raw Right); VOLSET=
restore_vol() { [ -n "$VOLSET" ] && { amx cset 'name=Left Digital PCM Volume' "${OLDL:-0}"; amx cset 'name=Right Digital PCM Volume' "${OLDR:-0}"; }; }
trap 'restore_vol; batch "$ROUTE.reset"; [ -n "${CLEANWAV:-}" ] && rm -f "$CHROOT$WAV"' EXIT
trap 'exit 130' INT TERM
batch "$ROUTE" || exit 1
LVL=${S22_SAY_LEVEL:-4}
if [ "${OLDL:-0}" = 0 ] && [ "${OLDR:-0}" = 0 ] && [ "$LVL" -gt 0 ] 2>/dev/null; then
  [ "$LVL" -le 10 ] || LVL=10
  RAW=$((817 - (10 - LVL) * 32))
  amx cset 'name=Left Digital PCM Volume' $RAW && amx cset 'name=Right Digital PCM Volume' $RAW && VOLSET=1
fi

if [ -n "$DIAG" ]; then
  mkdir -p "$DIAG"
  ( i=0; while [ $i -lt 40 ]; do
      { echo "t=$i"; cat /proc/asound/card0/pcm${PCM}p/sub0/status; } >> "$DIAG/status.txt" 2>&1
      i=$((i + 1)); sleep 0.25; done ) &
  dmesg > "$DIAG/dmesg-before.txt" 2>&1
fi

# s22spk (alsa-s22.conf) = plug -> hw:0,6 at 48 kHz stereo; RDMA6's own ASRC
# garbles 22.05 kHz mono (espeak's native format).
# Default (2026-10-21): play like the stock AudioProxy into a firmware-graph
# frontend: calliope_0 (pcm 100 = stock primary playback) -> VPCMOUT_DAI0 ->
# BD_MIXER (VDSP0 bdmixer-normal, set by every media-* route) -> RDMA3 -> SPUS.
# S22_GRAPH_PCM=102 selects calliope_2 (-> VDSP3 -> RDMA2). HAL-sized periods
# are required (6000-frame periods lost audio and could crash Calliope).
# NEVER put an alsa plug in front of a calliope device: plug/rate drive the
# slave through MMAP, and the vdma ION mmap leaks the substream's mmap_count
# (every later open then fails HW_PARAMS with EBADFD until reboot). So the WAV
# is converted offline to 48 kHz stereo S16 (s22conv) and written with RW access.
# A faulted Calliope (failsafe OFFLINE) is reset like the stock HAL does and the
# playback retried once; then RDMA6 (S22_AUDIO_PATH=direct) is the fallback.
# /run/s22-say.last records which path actually played.
GRAPH=1; [ "${S22_AUDIO_PATH:-graph}" = direct ] && { GRAPH=; PCM=6; }
ALSACONF=$OPT/mixer/alsa-s22.conf
abox_recover() {
  [ "$(cat /proc/abox/failsafe/online 2>/dev/null)" = OFFLINE ] || return 0
  echo "s22-say: ABOX OFFLINE, failsafe reset" >&2
  printf CALLIOPE > /proc/abox/failsafe/reset
  i=0; while [ $i -lt 20 ] && [ "$(cat /proc/abox/failsafe/online)" = OFFLINE ]; do sleep 0.25; i=$((i + 1)); done
}
aplay_c() { timeout 60 chroot "$CHROOT" /usr/bin/env LD_LIBRARY_PATH=$OPT/usr/lib \
  ALSA_CONFIG_PATH=$ALSACONF $OPT/usr/bin/aplay -q "$@"; }
play_direct() { aplay_c -D s22spk "$@"; }
RAW=/tmp/s22-say-$$.48k.raw
play_graph() {   # $1 = WAV inside the chroot
  rm -f "$CHROOT$RAW"
  aplay_c -D "s22conv:$RAW" "$1" >/dev/null 2>&1 && [ -s "$CHROOT$RAW" ] || { echo "s22-say: conversion failed" >&2; return 1; }
  aplay_c -D hw:0,$PCM --period-size=480 --buffer-size=1920 -t raw -f S16_LE -r 48000 -c 2 "$RAW"
}
trap 'rm -f "$CHROOT$RAW"; restore_vol; batch "$ROUTE.reset"; [ -n "${CLEANWAV:-}" ] && rm -f "$CHROOT$WAV"' EXIT
abox_recover
USED=direct
if [ -n "$GRAPH" ]; then
  # ABOX powers Calliope down ~0.5 s after the last close; a short silent
  # stream first makes sure the firmware is up before the real trigger.
  aplay_c -D hw:0,$PCM --period-size=480 --buffer-size=1920 -t raw -f S16_LE -r 48000 -c 2 -s 4800 /dev/zero >/dev/null 2>&1
  play_graph "$WAV"; rc=$?; USED=graph
  if [ $rc -ne 0 ]; then
    abox_recover; echo "s22-say: graph playback rc=$rc, retrying" >&2
    play_graph "$WAV"; rc=$?
    if [ $rc -ne 0 ]; then
      abox_recover; echo "s22-say: graph failed again, falling back to RDMA6" >&2
      play_direct "$WAV"; rc=$?; USED=direct-fallback
    fi
  fi
else
  play_direct -t raw -f S16_LE -r 48000 -c 2 -s 4800 /dev/zero >/dev/null 2>&1
  play_direct "$WAV"; rc=$?
fi
printf '%s pcm=%s rc=%s t=%s\n' "$USED" "$PCM" "$rc" "$(date +%s)" > /run/s22-say.last
if [ -n "$DIAG" ]; then
  wait
  dmesg > "$DIAG/dmesg-after.txt" 2>&1
  echo "aplay_rc=$rc" > "$DIAG/result.txt"
fi
exit $rc
