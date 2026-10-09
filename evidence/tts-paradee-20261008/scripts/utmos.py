"""usage: utmos.py DIR... -> mean UTMOS22-strong per dir (16 kHz resample)"""
import sys, glob, os, torch, librosa
torch.set_num_threads(8)
m = torch.hub.load("tarepan/SpeechMOS:v1.2.0", "utmos22_strong", trust_repo=True).eval()
for d in sys.argv[1:]:
    sc = []
    for w in sorted(glob.glob(f"{d}/[0-9][0-9].wav")):
        y, _ = librosa.load(w, sr=16000)
        with torch.no_grad(): sc.append(float(m(torch.from_numpy(y)[None], 16000)))
    print(f"{os.path.basename(d.rstrip('/')):22s} n={len(sc):2d} UTMOS={sum(sc)/len(sc):.3f} min={min(sc):.2f}")
