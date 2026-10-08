#!/bin/bash
# usage: gen_engines.sh THREADS CPUSET  -> out/<engine>-t<THREADS>/NN.wav + timing.txt
set -u
E=/home/corpunum/datasets/paradee-eval; cd $E
TH=$1; CPUS=$2
SH=$E/sherpa/sherpa-onnx-v1.13.8-linux-x64-static/bin/sherpa-onnx-offline-tts
args() { local M=$E/sherpa S
  case $1 in
    supertonic) S=$M/sherpa-onnx-supertonic-tts-int8-2026-03-06
      echo --supertonic-duration-predictor=$S/duration_predictor.int8.onnx --supertonic-text-encoder=$S/text_encoder.int8.onnx \
        --supertonic-vector-estimator=$S/vector_estimator.int8.onnx --supertonic-vocoder=$S/vocoder.int8.onnx --supertonic-tts-json=$S/tts.json \
        --supertonic-unicode-indexer=$S/unicode_indexer.bin --supertonic-voice-style=$S/voice.bin;;
    kitten) S=$M/kitten-nano-en-v0_8-int8; echo --kitten-model=$S/model.int8.onnx --kitten-voices=$S/voices.bin --kitten-tokens=$S/tokens.txt --kitten-data-dir=$S/espeak-ng-data;;
    piper) S=$M/vits-piper-en_US-lessac-medium; echo --vits-model=$S/en_US-lessac-medium.onnx --vits-tokens=$S/tokens.txt --vits-data-dir=$S/espeak-ng-data;;
    kokoro) S=$M/kokoro-int8-en-v0_19; echo --kokoro-model=$S/model.int8.onnx --kokoro-voices=$S/voices.bin --kokoro-tokens=$S/tokens.txt --kokoro-data-dir=$S/espeak-ng-data;;
  esac; }
for eng in paradee supertonic kitten piper kokoro; do
  O=$E/out/$eng-t$TH; mkdir -p $O; : > $O/timing.txt; i=0
  while IFS= read -r s; do i=$((i+1)); n=$(printf %02d $i)
    if [ $eng = paradee ]; then
      taskset -c $CPUS $E/build/paradee-tts-x64 --model=/home/corpunum/models/paradee/onnx/paradee_int8.onnx --num-threads=$TH \
        --output-filename=$O/$n.wav "$s" 2>&1 | grep -o 'elapsed=[0-9.]*s.*' | sed 's/elapsed=\([0-9.]*\)s.*audio=\([0-9.]*\)s.*/\1 \2/' >> $O/timing.txt
    else
      taskset -c $CPUS $SH --num-threads=$TH --sid=0 $(args $eng) --output-filename=$O/$n.wav "$s" 2>&1 \
        | awk '/Elapsed seconds/{e=$3} /Audio duration/{a=$3} END{print e, a}' | tr -d ',' >> $O/timing.txt
    fi
  done < sentences.txt
  awk -v e=$eng -v t=$TH '{el+=$1; au+=$2} END{printf "%-11s threads=%s audio=%6.1fs elapsed=%6.2fs RTF=%.4f\n", e, t, au, el, el/au}' $O/timing.txt
done
