# Paradee-8M evaluation for the S22 (2026-10-08, claude-tts, rig only)

Model: [sahilmahendrakar/Paradee-8M-v1.0](https://huggingface.co/sahilmahendrakar/Paradee-8M-v1.0),
tag v1.0, Apache-2.0. It is an 8.07M-parameter distillation of Kokoro-82M with one voice,
`af_heart` (female, American English). The int8 ONNX file is 9.0 MB
(sha256 `60e8f8a1…98eb`). Nothing in this evaluation touched the phone.

## How Paradee runs

```
text -> G2P (misaki spelling) -> Kokoro vocab ids (178-symbol table, 0 pad at each end, <=512)
     -> ONE onnx graph (input_ids int64 [1,T], speed float [1]) -> waveform float [1,N] @ 24 kHz
```

* There is no `tokenizer.json` magic. `tokenizer.json` is just Kokoro's phoneme
  vocabulary, the same one as in `config.json`.
* G2P: the reference package uses **misaki** (Kokoro's own G2P). Misaki needs
  spaCy, num2words and phonemizer, and falls back to espeak. That cannot be
  installed on the phone (pip hangs the kernel).
* On-device path: **eSpeak NG IPA → misaki spelling**. This is a C port of
  upstream `web/misaki.js`, which is misaki's own `EspeakFallback` table plus
  four fixes. The upstream authors report that raw eSpeak phonemes give 31% WER,
  and that this mapping brings it to about 2%. Our measurement is 1.8%, shown below.
* The decoder injects noise, as Kokoro's does, so two renders of the same text
  are not bit-identical. Both have the same length.

## Results (rig, Ryzen AI MAX+ 395, cores pinned, 15 sentences, ~93 s of audio)

The sentences are in `sentences.txt`: the 5 held-out sentences from the model
card plus 10 assistant- and phone-style lines. The sherpa-onnx engines are the
exact models and the v1.13.8 runtime that the S22 uses, in the x64 build.
Paradee is the new C runner. "paradee (misaki)" is the upstream Python package.

| Engine | Size on disk | RTF 1 thr | RTF 4 thr | WER (whisper base.en) | UTMOS22 | S22 RTF (4 thr) |
|---|---:|---:|---:|---:|---:|---:|
| **paradee** (espeak→misaki, C runner) | 9 MB model (+25 MB ORT) | **0.040–0.085** (median 0.066) | **0.021** | 1.8% | **4.42** | est. **0.04–0.10** |
| paradee (misaki, Python reference) | same | 0.064 | – | 0.7% | 4.39 | – |
| supertonic int8 (current default) | 80–92 MB | 0.072–0.22 (median 0.084) | 0.033 | 3.7% | 4.37 | 0.16 (measured) |
| kitten nano int8 | 29–45 MB | 0.28 | 0.141 | 2.2% | 4.17 | 0.29 (measured) |
| piper lessac-medium | 64–79 MB | 0.086 | 0.028 | 2.6% | 4.34 | ~0.26 (measured) |
| kokoro v0.19 int8 (`af`) | 98–152 MB | 1.01 | 0.64 | 1.5% | 4.17 | 1.24 (measured) |
| *HF samples: Paradee* (n=5) | | | | 1.6% | 4.41 | |
| *HF samples: Kokoro-82M af_heart, teacher* (n=5) | | | | 0.8% | 4.54 | |

* The rig was under background load (load average ~5), so 1-thread RTF varied
  between runs. The table gives the range over 4–5 runs. Paradee was faster
  than supertonic in every paired run.
* Our UTMOS setup (SpeechMOS utmos22_strong) reproduces the model card on the
  HF samples: 4.41 for Paradee and 4.54 for the teacher. The C runner's output
  scores the same as the Python reference.
* Per-utterance wall time on the rig for a short sentence at 4 threads,
  including model load: paradee .onnx 0.60 s, paradee .ort 0.45–0.50 s,
  supertonic 0.42–0.59 s, kokoro 1.7 s. Paradee's session start-up (~0.4–0.7 s,
  4,816-node graph) dominates a short sentence. The ORT-format model halves it.
  `--serve` mode removes it entirely.
* Peak RSS for one long sentence on x64: paradee 128 MB, supertonic 217 MB.
* WER errors are almost all proper nouns, for every engine ("Hurlbut",
  "Type Ia", "wicket"). Paradee's espeak path read "2015" as "two thousand fifteen".
  Misaki reads it as "twenty fifteen".

### Listening notes
I could not listen to the clips. The notes below come from objective proxies
and the model card. UTMOS ranks Paradee above every engine the S22 ships today,
including sherpa's Kokoro v0.19 `af` voice, which is an older checkpoint without
`af_heart`. Paradee sits about 0.1 UTMOS below its teacher. The model card warns
of a faint buzz on some voiced sounds; a parameter-free phase-lock filter in the
graph reduces it.

Clips for the owner to listen to are on the rig:
* `~/s22-tts-samples/paradee.wav` reads the same "Hello Antonis…" sentence as the
  existing `supertonic.wav`, `kokoro.wav` and the other clips in that folder.
* All engines × 15 sentences are in `~/datasets/paradee-eval/out/<engine>-t1/NN.wav`.
* The model card's paired samples are in `~/models/paradee/samples/`.

### S22 RTF estimate
The S22's measured RTFs (4 threads on cores 4–7, with the resident 4B model
running) divided by the rig's 4-thread RTFs give these slow-down factors:

| Engine | S22 ÷ rig |
|---|---:|
| kokoro (int8, StyleTTS2 family, same as Paradee) | 1.9× |
| kitten (int8, StyleTTS2 family, same as Paradee) | 2.1× |
| supertonic | 4.9× |
| piper (fp32 VITS, which favours AVX-512) | 9.4× |

Applying the same-family ratios to Paradee gives **≈0.04**, and the supertonic
ratio gives **≈0.10**. Expect **RTF 0.04–0.10 at 4 threads**, which is 10–25×
real time. One thread would land around 0.1–0.2. Model start-up should be about
1 s per call (.ort). These figures are estimates; the S22 lead measures the
real ones (see the deploy note).

### aarch64 verification (qemu-aarch64)
* The runner was cross-compiled with `aarch64-linux-gnu-gcc` against onnxruntime
  1.30.0 aarch64. Its glibc floor is 2.34; the ORT library's is 2.28.
* It ran under qemu-aarch64 with **Arch Linux ARM's espeak-ng 1.52.0**, the same
  package family as the phone's `/opt/s22-audio`. whisper transcribed the qemu
  output exactly ("Hello, Antonis. This is your S22 speaking with a neural voice,
  running entirely on the phone.").
* The phonemes from espeak-ng 1.52 (aarch64) and from the rig's 1.51 (x64) were
  identical for all 19 chunks (`phonemes-espeak152-aarch64.txt`).

## Reproduce
```
python3 -m venv ~/datasets/paradee-eval/venv && ~/datasets/paradee-eval/venv/bin/pip install <paradee repo> soundfile jiwer
scripts/gen_misaki.py sentences.txt out/paradee-misaki 1
scripts/gen_engines.sh 1 24 ; scripts/gen_engines.sh 4 24-27
scripts/wer.py sentences.txt out/*         # whisper.cpp base.en, numbers -> words on both sides
scripts/utmos.py out/*                     # separate CPU-torch venv, SpeechMOS utmos22_strong
```

## Recommendation
* **S22:** add `paradee` as an engine now (runner + deploy note in `tools/hardware/tts/paradee/`).
  Make it the default after the lead confirms the on-device RTF from a muted render
  and the owner has listened to `~/s22-tts-samples/paradee.wav`. On every measure we
  have, it is better than supertonic, which is what Kokoro was supposed to give,
  and it runs well under real time. Use `--serve` for the assistant and for calls.
* **OpenUnum on the rig/Ally:** this does not replace Orpheus (expressive) or
  multi-voice Kokoro. It is worth adding as a CPU-only tier. It needs about 9 MB,
  no GPU or NPU, and runs about 15–25× real time on one Zen 5 thread. It uses the
  same af_heart voice as Kokoro, so it can serve as a no-voice-change fallback when
  the GPU/NPU is busy (ComfyUI/LTX jobs) and as the default on hardware-agnostic
  installs that have no accelerator. The same C runner builds for x86_64
  (`gcc ... -lonnxruntime`), and the Python package works too.
