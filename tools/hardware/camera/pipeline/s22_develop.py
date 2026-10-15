"""Raw frame -> DNG -> LibRaw (via rawpy) -> PNG for the S22 cameras.

Pipeline (prototype, CPU only):
  1. load the 10-bit Bayer plane from the stride-padded raw dump;
  2. optional lens-shading correction from a flat-field frame (gain map);
  3. gray-world AWB multipliers from the mosaic (per colour mean);
  4. write a CFA DNG (black, white, colour matrix) with the corrected data;
  5. LibRaw demosaic + colour + gamma through rawpy; write an 8-bit sRGB PNG.

Usage (rig):
  python3 -I -B s22_develop.py develop RAW --out-png X.png [--out-dng X.dng]
      [--flat FLAT_RAW] [--pattern GBRG] [--demosaic AHD] [--json]
  python3 -I -B s22_develop.py naive RAW --out-png X.png
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import numpy as np

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import s22_dng  # noqa: E402

WIDTH, HEIGHT, STRIDE = 2040, 1532, 4096
BLACK, WHITE = 64, 1023
PATTERN = "GBRG"  # GN3, from the kernel's OTF_INPUT_ORDER_BAYER_GB_RG
EXPOSURE_MIN_US, EXPOSURE_MAX_US = 100, 30000
# AWB needs real signal. Below this mean (codes above black) the gray-world
# estimate is noise, so the gains stay at 1.0 and the result says so.
MIN_SIGNAL_CODES = 8.0


def load_raw(path: str, width: int = WIDTH, height: int = HEIGHT,
             stride: int = STRIDE) -> np.ndarray:
    """Return a (height, width) uint16 array; each row is 'stride' bytes."""
    buf = np.fromfile(path, dtype="<u2", count=(stride // 2) * height)
    if buf.size != (stride // 2) * height:
        raise ValueError("raw file too short for %dx%d stride %d" % (width, height, stride))
    return buf.reshape(height, stride // 2)[:, :width].copy()


def channel_index(pattern: str) -> np.ndarray:
    """(H, W) map of colour index 0=R, 1=G, 2=B for the 2x2 pattern, tiled later."""
    pattern = pattern.upper()
    idx = {"R": 0, "G": 1, "B": 2}
    return np.array([[idx[pattern[0]], idx[pattern[1]]],
                     [idx[pattern[2]], idx[pattern[3]]]])


def channel_masks(shape, pattern: str):
    h, w = shape
    tile = channel_index(pattern)
    full = np.tile(tile, (h // 2 + 1, w // 2 + 1))[:h, :w]
    return [full == c for c in range(3)]


def gray_world_gains(raw: np.ndarray, pattern: str, black: float = BLACK):
    """Return (r_gain, b_gain) with G fixed at 1.0. Uses per-colour means."""
    r_mask, g_mask, b_mask = channel_masks(raw.shape, pattern)
    means = [max(raw[m].astype(np.float64).mean() - black, 1e-6)
             for m in (r_mask, g_mask, b_mask)]
    return means[1] / means[0], means[1] / means[2]


def estimate_gain_map(flat: np.ndarray, pattern: str = PATTERN, black: float = BLACK,
                      grid: int = 32) -> np.ndarray:
    """Per-pixel lens-shading gain (>= 1) from a flat-field frame.

    The flat frame is averaged on a 'grid' x 'grid' block layout per colour
    channel, bilinearly upsampled, and each channel is normalised to its
    brightest block (so the gain is >= 1 everywhere, 1.0 at the peak).
    """
    from PIL import Image

    h, w = flat.shape
    f = flat.astype(np.float32) - black
    masks = channel_masks(flat.shape, pattern)
    gain = np.ones((h, w), dtype=np.float32)
    bh, bw = h // grid, w // grid
    for mask in masks:
        plane = np.where(mask, f, np.nan)
        blocks = np.empty((grid, grid), dtype=np.float32)
        for gy in range(grid):
            for gx in range(grid):
                blk = plane[gy * bh:(gy + 1) * bh, gx * bw:(gx + 1) * bw]
                blocks[gy, gx] = np.nanmean(blk) if np.isfinite(blk).any() else 0.0
        blocks = np.maximum(blocks, 1.0)
        blocks = blocks.max() / blocks  # >= 1 everywhere
        up = np.asarray(Image.fromarray(blocks, mode="F").resize((w, h), Image.BILINEAR))
        gain = np.where(mask, up, gain)
    return gain


def apply_lens_shading(raw: np.ndarray, gain: np.ndarray, black: float = BLACK,
                       white: float = WHITE) -> np.ndarray:
    out = (raw.astype(np.float32) - black) * gain + black
    return np.clip(np.rint(out), 0, white).astype(np.uint16)


def naive_rgb8(raw: np.ndarray, black: float = BLACK) -> np.ndarray:
    """The crude develop: grey mosaic, no WB, no shading, 99th-percentile stretch, gamma 2.2."""
    x = raw.astype(np.float32) - black
    white = max(float(np.percentile(x, 99)), 1.0)
    lin = np.clip(x / white, 0, 1)
    g8 = (255 * lin ** (1 / 2.2)).astype(np.uint8)
    return np.stack([g8, g8, g8], axis=-1)


def libraw_develop(dng_path: str, r_gain: float, b_gain: float,
                   demosaic: str = "AHD", half_size: bool = False) -> np.ndarray:
    """Demosaic + sRGB + gamma with LibRaw. Returns (H, W, 3) uint16."""
    import rawpy

    algo = getattr(rawpy.DemosaicAlgorithm, demosaic)
    with rawpy.imread(dng_path) as rp:
        return rp.postprocess(
            demosaic_algorithm=algo,
            use_camera_wb=False,
            user_wb=[r_gain, 1.0, b_gain, 1.0],
            output_color=rawpy.ColorSpace.sRGB,
            output_bps=16,
            no_auto_bright=False,
            half_size=half_size,
        )


def ae_step(mean_linear: float, exposure_us: float, target: float = 0.18,
            max_factor: float = 2.0, tolerance: float = 0.05) -> float:
    """One proportional AE update, clamped to the CIS limits.

    mean_linear is the black-corrected mean of the linear raw, normalised to
    [0, 1] (WHITE = 1.0). The step is damped (max_factor) and skipped within
    'tolerance' of the target (ratio of target to mean).
    """
    mean_linear = max(mean_linear, 1e-4)
    ratio = target / mean_linear
    if abs(ratio - 1.0) < tolerance:
        return float(exposure_us)
    factor = min(max(ratio, 1.0 / max_factor), max_factor)
    return float(min(max(exposure_us * factor, EXPOSURE_MIN_US), EXPOSURE_MAX_US))


def write_png16(path: str, rgb16: np.ndarray) -> None:
    from PIL import Image

    if rgb16.dtype != np.uint16:
        raise ValueError("expected uint16 RGB")
    # 8-bit sRGB PNG for review. The 16-bit LibRaw array is not kept.
    Image.fromarray((rgb16 >> 8).astype(np.uint8)).save(path)


def develop(raw_path: str, out_png: str, *, out_dng: str | None = None,
            flat_path: str | None = None, pattern: str = PATTERN,
            demosaic: str = "AHD") -> dict:
    t = {}
    t0 = time.perf_counter()
    raw = load_raw(raw_path)
    t["load"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    gain = None
    if flat_path:
        gain = estimate_gain_map(load_raw(flat_path), pattern, BLACK)
        raw = apply_lens_shading(raw, gain, BLACK, WHITE)
    t["shading"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    signal = float(raw.astype(np.float64).mean() - BLACK)
    if signal >= MIN_SIGNAL_CODES:
        r_gain, b_gain = gray_world_gains(raw, pattern, BLACK)
        awb = "gray_world"
    else:
        r_gain, b_gain = 1.0, 1.0
        awb = "skipped_underexposed"
    t["awb_calc"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    dng = s22_dng.build_dng(raw, pattern=pattern, black=BLACK, white=WHITE)
    dng_path = out_dng or (out_png.rsplit(".", 1)[0] + ".dng")
    with open(dng_path, "wb") as f:
        f.write(dng)
    t["dng_write"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    rgb = libraw_develop(dng_path, r_gain, b_gain, demosaic)
    t["libraw"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    write_png16(out_png, rgb)
    t["png"] = time.perf_counter() - t0
    t["total"] = sum(t.values())

    lin = np.clip((raw.astype(np.float64) - BLACK) / (WHITE - BLACK), 0, 1)
    return {
        "ok": True, "png": out_png, "dng": dng_path, "dng_bytes": len(dng),
        "pattern": pattern, "demosaic": demosaic, "awb": awb, "signal_codes": round(signal, 2),
        "r_gain": round(r_gain, 4),
        "b_gain": round(b_gain, 4), "mean_linear": round(float(lin.mean()), 4),
        "shading": flat_path is not None, "ms": {k: round(v * 1000, 1) for k, v in t.items()},
        "size": [int(rgb.shape[1]), int(rgb.shape[0])],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("develop")
    d.add_argument("raw")
    d.add_argument("--out-png", required=True)
    d.add_argument("--out-dng")
    d.add_argument("--flat")
    d.add_argument("--pattern", default=PATTERN)
    d.add_argument("--demosaic", default="AHD")
    d.add_argument("--json", action="store_true")
    n = sub.add_parser("naive")
    n.add_argument("raw")
    n.add_argument("--out-png", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "naive":
        from PIL import Image
        Image.fromarray(naive_rgb8(load_raw(args.raw))).save(args.out_png)
        print(json.dumps({"ok": True, "png": args.out_png}))
        return 0
    res = develop(args.raw, args.out_png, out_dng=args.out_dng, flat_path=args.flat,
                  pattern=args.pattern, demosaic=args.demosaic)
    print(json.dumps(res) if args.json else json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
