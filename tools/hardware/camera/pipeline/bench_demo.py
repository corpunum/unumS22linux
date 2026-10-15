"""Make before/after PNGs and time the develop chain.

  python3 -B bench_demo.py --out-dir DIR [--repeat 3] [--real RAW]

Writes synthetic before/after PNGs at full 2040x1532, and optionally the
before/after of a real raw frame (e.g. the rig's dark sample). Prints JSON.
"""
import argparse
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import s22_develop as dev  # noqa: E402
import s22_synth as syn  # noqa: E402


def timed(fn, repeat):
    best = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
    return round(best * 1000, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--real", default=None)
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    out = {}

    scratch = os.path.join(args.out_dir, "_scratch")
    os.makedirs(scratch, exist_ok=True)
    flat_raw = os.path.join(scratch, "flat.raw")
    syn.write_raw_dump(flat_raw, syn.bayer_plane(
        np.ones((dev.HEIGHT, dev.WIDTH, 3), np.float32), "GBRG", seed=21))
    scene_raw = os.path.join(scratch, "synth.raw")
    syn.write_raw_dump(scene_raw, syn.bayer_plane(syn.scene_rgb(), "GBRG", seed=22))

    from PIL import Image
    raw = dev.load_raw(scene_raw)
    before = os.path.join(args.out_dir, "synth-before.png")
    Image.fromarray(dev.naive_rgb8(raw)).save(before)
    after = os.path.join(args.out_dir, "synth-after.png")
    res = dev.develop(scene_raw, after, flat_path=flat_raw, pattern="GBRG",
                      out_dng=os.path.join(scratch, "synth.dng"))
    out["synthetic"] = res
    out["synthetic"]["before_png"] = before

    t_gain = timed(lambda: dev.estimate_gain_map(dev.load_raw(flat_raw)), args.repeat)
    gain = dev.estimate_gain_map(dev.load_raw(flat_raw))
    t_shade = timed(lambda: dev.apply_lens_shading(raw, gain), args.repeat)
    dng_path = os.path.join(scratch, "bench.dng")
    t_dng = timed(lambda: open(dng_path, "wb").write(
        dev.s22_dng.build_dng(raw, pattern="GBRG")), args.repeat)
    out["stage_ms_best_of_%d" % args.repeat] = {
        "gain_map_from_flat": t_gain,
        "apply_shading": t_shade,
        "dng_build_write": t_dng,
    }
    for algo in ("LINEAR", "AHD", "DHT"):
        t_lr = timed(lambda a=algo: dev.libraw_develop(dng_path, 1.8, 1.4, a), args.repeat)
        out["stage_ms_best_of_%d" % args.repeat]["libraw_%s" % algo.lower()] = t_lr

    if args.real and os.path.exists(args.real):
        rb = os.path.join(args.out_dir, "real-before.png")
        Image.fromarray(dev.naive_rgb8(dev.load_raw(args.real))).save(rb)
        ra = os.path.join(args.out_dir, "real-after.png")
        rres = dev.develop(args.real, ra, pattern="GBRG",
                           out_dng=os.path.join(scratch, "real.dng"))
        out["real"] = rres
        out["real"]["before_png"] = rb

    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
