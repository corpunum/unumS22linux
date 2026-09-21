"""Offline checks for the Xclipse OpenCL probe.

No test here loads a vendor library or talks to a phone. A supervised target
run is separate because an OpenCL kernel can wedge a driver beyond cancellation.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest


class OpenCLProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="s22-opencl-probe-")
        cls.binary = Path(cls.tmp.name) / "opencl-probe"
        cls.source = Path(__file__).with_name("opencl-probe.c")
        subprocess.run(
            [
                "cc",
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Werror",
                str(cls.source),
                "-ldl",
                "-o",
                str(cls.binary),
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_probe(self, *args):
        env = dict(os.environ)
        env.pop("LD_PRELOAD", None)
        return subprocess.run(
            [str(self.binary), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_self_test_is_offline(self):
        result = self.run_probe("--self-test")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SELF_TEST PASS", result.stdout)
        self.assertNotIn("dlopen", result.stdout.lower())

    def test_invalid_arguments_fail_before_dlopen(self):
        cases = [
            (),
            ("--nope",),
            ("--compute", "17"),
            ("--enumerate", "bad"),
            ("--load-only", "--library"),
        ]
        for case in cases:
            with self.subTest(case=case):
                result = self.run_probe(*case)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("dlopen", result.stdout.lower())
                self.assertNotIn("DLOPEN_ERROR", result.stderr)

    def test_library_path_is_not_loaded_for_bad_compute_count(self):
        result = self.run_probe(
            "--compute", "512", "--library", "/definitely/not/loaded.so"
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("DLOPEN_ERROR", result.stderr)


if __name__ == "__main__":
    unittest.main()
