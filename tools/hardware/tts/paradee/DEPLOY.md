# Deploy note: Paradee TTS engine for s22-say (for the S22 lead)

The evaluation is in `evidence/tts-paradee-20261008/README.md`. In short,
Paradee had the best UTMOS (4.42) of all the S22 engines and a lower WER than
supertonic (1.8% vs 3.7%). It was faster than supertonic on the rig and about
20–30× faster than sherpa's kokoro.

**Keep the phone MUTED. Verify by rendering a WAV only. Never play it.**

## Files
On the rig, built by `tools/hardware/tts/paradee/build.sh`:
```
~/models/paradee/s22-dist/paradee-s22-aarch64.tar.gz          (~29 MB, not in git)
~/models/paradee/s22-dist/paradee-s22-aarch64.tar.gz.sha256
```
The tarball unpacks to `/mnt/omarchy-trial/opt/s22-tts/paradee/` (`/opt/s22-tts/paradee` inside the chroot):

| Path | What |
|---|---|
| `bin/paradee-tts` | 67 KB aarch64 runner (C, glibc ≥ 2.34, rpath `$ORIGIN`) |
| `bin/libonnxruntime.so.1` | onnxruntime 1.30.0 aarch64, official release, stripped |
| `paradee_int8.ort` | ORT-format model, preferred (half the start-up time) |
| `paradee_int8.onnx` | original model, fallback |
| `SHA256SUMS`, licences | |

The phonemizer is the chroot's existing **espeak-ng 1.52** in
`/opt/s22-audio/usr/lib/libespeak-ng.so.1`, loaded with dlopen. The runner
converts its IPA to misaki spelling internally. No Python, no pip, no new packages.

## Install
1. Copy both tarball files to the phone (any path), using your usual transfer.
2. On the phone, as root:
   ```
   sh tools/hardware/tts/paradee/install-paradee.sh /path/paradee-s22-aarch64.tar.gz
   ```
   The script checks the sha256, unpacks to `paradee.new`, and swaps it in
   (keeping `paradee.prev`). It then renders
   `/mnt/omarchy-trial/tmp/paradee-install-test.wav` and **does not play it**.
   The stderr line shows `load=… first-audio=… RTF=…`. Record those numbers.
3. Deploy the updated `tools/hardware/s22-say.sh`. It adds `--engine paradee`.
   The default is unchanged (supertonic).

## Verify (muted)
```
# render only, through the same command line s22-say uses
chroot /mnt/omarchy-trial /usr/bin/env LD_LIBRARY_PATH=/opt/s22-audio/usr/lib taskset -c 4-7 \
  /opt/s22-tts/paradee/bin/paradee-tts --num-threads=4 --model=/opt/s22-tts/paradee/paradee_int8.ort \
  --espeak-lib=/opt/s22-audio/usr/lib/libespeak-ng.so.1 --espeak-data=/opt/s22-audio/usr/share \
  --output-filename=/tmp/p.wav "Hello Antonis. This is your S22 speaking with a neural voice."
```
Pull `/mnt/omarchy-trial/tmp/p.wav` to the rig and run
`whisper-cli -m ~/whisper.cpp/models/ggml-base.en.bin -f` on it (resample to 16 kHz with ffmpeg first).
The transcript should be exact.

`s22-say --engine paradee` always plays the audio. Do not use it while the phone
must stay muted. Use the direct render above.

## Expected numbers on the S22 (estimates; please measure)
- RTF at 4 threads (cores 4–7, with the 4B model resident): **0.04–0.10**, which is 10–25× real time.
  With 1 thread: about 0.1–0.2.
- Model start-up: about 1 s per call with `.ort`, about 1.5–2 s with `.onnx`.
- For a short line, s22-say's total time should be about the same as supertonic's.
  For long text Paradee should be faster.
- RAM: about 130 MB peak, against about 220 MB for supertonic.

## Options
- `--raw-stdout` streams s16le 24 kHz mono, one sentence at a time, for an aplay pipe
  (`aplay -t raw -f S16_LE -r 24000 -c 1`). The phone's PCM path wants 48 kHz,
  so keep using the `s22spk` plug device. First audio is ready after the first sentence.
- `--serve` keeps the model loaded. Send `OUT.wav<TAB>text` lines on stdin and it
  replies `ok OUT.wav audio=…s elapsed=…s`. Use this for the voice assistant and,
  later, phone calls, because it removes the per-call start-up. Calls will need
  resampling from 24 kHz to 8/16 kHz.
- `--speed=1.1` speaks faster. `--phonemes-only` is for debugging G2P.

## Switching the default (only after the owner listens)
Set `S22_TTS_ENGINE=paradee` (environment) or change `ENGINE=${S22_TTS_ENGINE:-supertonic}`.
Paradee has only one voice (af_heart, female), which matches the owner's
"female" choice. `--sid` is ignored.

## Rollback
```
sh tools/hardware/tts/paradee/install-paradee.sh --rollback   # restores paradee.prev, or moves paradee aside
```
If the `paradee` directory is missing or broken, `s22-say --engine paradee` already
falls back to espeak-ng. The other engines are not touched. To remove everything:
`rm -rf /mnt/omarchy-trial/opt/s22-tts/paradee*`.

## Rebuild
On the rig: `PARADEE_TEST=1 sh tools/hardware/tts/paradee/build.sh`. It pins the
ORT and model sha256 values, cross-compiles, converts to `.ort`, and runs a qemu
smoke test with Arch ARM espeak-ng 1.52.
