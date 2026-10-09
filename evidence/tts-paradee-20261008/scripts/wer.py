"""usage: wer.py SENTENCES DIR... -> whisper base.en transcripts + WER per dir (numbers -> words on both sides)"""
import sys, os, re, subprocess, glob, json, jiwer
from num2words import num2words
W = "/home/corpunum/whisper.cpp"
def norm(s):
    s = s.lower().replace("%", " percent").replace("-", " ")
    s = re.sub(r"\d+", lambda m: num2words(int(m.group())), s)
    s = s.replace("-", " ").replace(",", " ")
    s = re.sub(r"[^a-z' ]", " ", s).replace("'s", "s")
    return " ".join(s.split())
refs = [l.strip() for l in open(sys.argv[1]) if l.strip()]
res = {}
for d in sys.argv[2:]:
    hyps, R = [], []
    for wav in sorted(glob.glob(f"{d}/[0-9][0-9].wav")) + sorted(glob.glob(f"{d}/[0-9]_*.wav")):
        i = int(re.match(r"(\d+)", os.path.basename(wav)).group(1))
        w16 = wav[:-4] + ".16k.wav.tmp"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", wav, "-ar", "16000", "-ac", "1", "-f", "wav", w16], check=True)
        out = subprocess.run([f"{W}/build/bin/whisper-cli", "-m", f"{W}/models/ggml-base.en.bin", "-nt", "-t", "8", "-f", w16],
                             capture_output=True, text=True).stdout
        os.remove(w16)
        h = " ".join(out.split())
        hyps.append(h); R.append(refs[i - 1])
    if not hyps: continue
    wer = jiwer.wer([norm(r) for r in R], [norm(h) for h in hyps])
    res[d] = wer
    with open(f"{d}/transcripts.txt", "w") as f:
        for r, h in zip(R, hyps):
            f.write(f"REF: {r}\nHYP: {h}\n")
    print(f"{os.path.basename(d.rstrip('/')):22s} n={len(hyps):2d} WER={wer*100:5.1f}%")
