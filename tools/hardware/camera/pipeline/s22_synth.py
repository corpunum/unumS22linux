"""Synthetic S22 rear raw frames for testing the develop chain.

The real rear frames on the rig carry almost no signal (values 57-123 with
black level 64), so they cannot show colour or shading. This module writes a
frame in the exact dump format (2040x1532 used, stride 4096 bytes, u16 LE,
10-bit codes) with a known sensor cast, a known radial vignette and noise.
"""
from __future__ import annotations

import numpy as np

from s22_develop import HEIGHT, STRIDE, WIDTH

BLACK = 64
SENSOR_CAST = {"R": 0.55, "G": 1.0, "B": 0.70}  # sensor response per channel
VIGNETTE_EDGE = 0.62  # relative light at the corners


def vignette(h: int = HEIGHT, w: int = WIDTH, edge: float = VIGNETTE_EDGE) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cy, cx = (h - 1) / 2, (w - 1) / 2
    r2 = ((yy - cy) / cy) ** 2 + ((xx - cx) / cx) ** 2
    return (1.0 - (1.0 - edge) * r2 / 2.0).astype(np.float32)


def scene_rgb(h: int = HEIGHT, w: int = WIDTH) -> np.ndarray:
    """Neutral-average scene: a grey ramp plus a 3x2 grid of coloured patches."""
    rgb = np.full((h, w, 3), 0.45, dtype=np.float32)
    rgb *= np.linspace(0.8, 1.2, w, dtype=np.float32)[None, :, None]
    patches = [(0.8, 0.2, 0.2), (0.2, 0.7, 0.2), (0.2, 0.3, 0.8),
               (0.7, 0.7, 0.2), (0.7, 0.4, 0.7), (0.2, 0.7, 0.7)]
    rows, cols = 2, 3
    for i, colour in enumerate(patches):
        r, c = divmod(i, cols)
        y0, y1 = int(h * (0.15 + 0.35 * r)), int(h * (0.15 + 0.35 * r + 0.25))
        x0, x1 = int(w * (0.1 + 0.3 * c)), int(w * (0.1 + 0.3 * c + 0.2))
        rgb[y0:y1, x0:x1] = colour
    return rgb


def bayer_plane(rgb: np.ndarray, pattern: str = "GBRG", signal: float = 600.0,
                vig: np.ndarray | None = None, noise: float = 1.5,
                seed: int = 1) -> np.ndarray:
    """Return a (H, W) uint16 Bayer plane with black level, cast, vignette and noise."""
    h, w, _ = rgb.shape
    rng = np.random.default_rng(seed)
    if vig is None:
        vig = vignette(h, w)
    cast = np.zeros((h, w), dtype=np.float32)
    plane = np.zeros((h, w), dtype=np.float32)
    pattern = pattern.upper()
    colour_at = {(0, 0): pattern[0], (0, 1): pattern[1], (1, 0): pattern[2], (1, 1): pattern[3]}
    idx = {"R": 0, "G": 1, "B": 2}
    for (dy, dx), ch in colour_at.items():
        sub = rgb[dy::2, dx::2, idx[ch]]
        plane[dy::2, dx::2] = sub * SENSOR_CAST[ch]
    plane = plane * signal * vig + BLACK
    plane += rng.normal(0.0, noise, size=plane.shape).astype(np.float32)
    return np.clip(np.rint(plane), 0, 1023).astype(np.uint16)


def write_raw_dump(path: str, plane: np.ndarray) -> None:
    """Write the frame in the dump format: rows padded to STRIDE bytes."""
    h, w = plane.shape
    row = np.zeros((h, STRIDE // 2), dtype="<u2")
    row[:, :w] = plane
    with open(path, "wb") as f:
        f.write(row.tobytes())
