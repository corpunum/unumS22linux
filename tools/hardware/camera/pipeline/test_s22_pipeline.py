"""Tests for the S22 raw -> DNG -> LibRaw prototype.

Run on the rig with the scratch venv (numpy, pillow, rawpy):
  python3 -B -m unittest test_s22_pipeline -v
"""
import os
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import s22_develop as dev  # noqa: E402
import s22_dng  # noqa: E402
import s22_synth as syn  # noqa: E402

REAL_DARK = "/home/corpunum/openunum-briefs/s22-camera-20261009/c2.raw"


def _tmp(name):
    d = tempfile.mkdtemp(prefix="s22pipe-")
    return os.path.join(d, name)


class DngTags(unittest.TestCase):
    def test_dng_reads_back_through_libraw(self):
        import rawpy

        plane = syn.bayer_plane(syn.scene_rgb(), "GBRG", seed=3)
        path = _tmp("t.dng")
        s22_dng.write_dng(path, plane, pattern="GBRG", black=64, white=1023)
        with rawpy.imread(path) as rp:
            self.assertEqual(rp.sizes.raw_width, dev.WIDTH)
            self.assertEqual(rp.sizes.raw_height, dev.HEIGHT)
            # LibRaw labels the second green as 3; G B / R G = [[G,B],[R,G]]
            pat = [[1 if v == 3 else v for v in row] for row in rp.raw_pattern.tolist()]
            self.assertEqual(pat, [[1, 2], [0, 1]])
            self.assertEqual(list(rp.black_level_per_channel), [64, 64, 64, 64])
            self.assertEqual(rp.white_level, 1023)
            self.assertTrue(np.array_equal(rp.raw_image, plane))

    def test_bad_pattern_rejected(self):
        with self.assertRaises(ValueError):
            s22_dng.cfa_bytes("GGGG")


class ShadingAndWhiteBalance(unittest.TestCase):
    def test_gain_map_recovers_vignette(self):
        h, w = dev.HEIGHT, dev.WIDTH
        flat_rgb = np.ones((h, w, 3), dtype=np.float32)
        flat = syn.bayer_plane(flat_rgb, "GBRG", signal=600.0, noise=1.0, seed=5)
        gain = dev.estimate_gain_map(flat, "GBRG", black=64, grid=32)
        corner = gain[5, 5]
        centre = gain[h // 2, w // 2]
        truth = (1 / syn.vignette()[5, 5]) / (1 / syn.vignette()[h // 2, w // 2])
        self.assertLess(abs(corner / centre - truth) / truth, 0.03)
        corrected = dev.apply_lens_shading(flat, gain, 64, 1023).astype(np.float64)
        # per colour channel: the cast is intentional, the falloff must be gone
        for mask in dev.channel_masks(flat.shape, "GBRG"):
            v = corrected[mask]
            self.assertLess(v.std() / v.mean(), 0.05)

    def test_gray_world_on_cast_frame(self):
        plane = syn.bayer_plane(syn.scene_rgb(), "GBRG", seed=7)
        r, b = dev.gray_world_gains(plane, "GBRG", 64)
        # sensor cast: R 0.55, B 0.70 with G 1.0 -> ideal gains 1.82, 1.43
        self.assertGreater(r, 1.4)
        self.assertLess(r, 2.1)
        self.assertGreater(b, 1.2)
        self.assertLess(b, 1.7)


class EndToEnd(unittest.TestCase):
    def test_developed_grey_region_is_neutral(self):
        from PIL import Image

        plane = syn.bayer_plane(syn.scene_rgb(), "GBRG", signal=600.0, seed=11)
        raw_path = _tmp("synth.raw")
        syn.write_raw_dump(raw_path, plane)
        flat_path = _tmp("flat.raw")
        syn.write_raw_dump(flat_path, syn.bayer_plane(np.ones((dev.HEIGHT, dev.WIDTH, 3),
                                                              np.float32), "GBRG",
                                                      seed=12))
        out = _tmp("dev.png")
        res = dev.develop(raw_path, out, flat_path=flat_path, pattern="GBRG")
        self.assertEqual(res["awb"], "gray_world")
        img = np.asarray(Image.open(out).convert("RGB")).astype(np.float64)
        # grey strip above the patch grid, in the centre third (both neutral)
        region = img[20:100, 800:1200]
        m = region.reshape(-1, 3).mean(axis=0)
        self.assertLess(abs(m[0] / m[1] - 1), 0.05, m)
        self.assertLess(abs(m[2] / m[1] - 1), 0.05, m)
        # the corner of the same grey strip must not be darker than centre after shading
        corner = img[20:100, 20:120].reshape(-1, 3).mean(axis=0).mean()
        centre = img[20:100, 800:1200].reshape(-1, 3).mean(axis=0).mean()
        self.assertLess(abs(corner / centre - 1), 0.12)

    @unittest.skipUnless(os.path.exists(REAL_DARK), "sample raw not on this rig")
    def test_real_dark_frame_skips_awb(self):
        out = _tmp("dark.png")
        res = dev.develop(REAL_DARK, out, pattern="GBRG")
        self.assertEqual(res["awb"], "skipped_underexposed")
        self.assertEqual((res["r_gain"], res["b_gain"]), (1.0, 1.0))


class AutoExposure(unittest.TestCase):
    def test_underexposed_doubles_exposure_within_limit(self):
        self.assertAlmostEqual(dev.ae_step(0.09, 10000.0), 20000.0)

    def test_within_tolerance_holds(self):
        self.assertEqual(dev.ae_step(0.18, 5000.0), 5000.0)
        self.assertEqual(dev.ae_step(0.185, 5000.0), 5000.0)

    def test_clamped_to_cis_limits(self):
        self.assertEqual(dev.ae_step(0.001, 25000.0), dev.EXPOSURE_MAX_US)
        self.assertEqual(dev.ae_step(1.0, 150.0), dev.EXPOSURE_MIN_US)


if __name__ == "__main__":
    unittest.main()
