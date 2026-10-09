#!/usr/bin/env python3
"""Host-only tests for the pinned camera modpost dependency filter."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/hardware/prepare-camera-modpost-symvers.py"
SPEC = importlib.util.spec_from_file_location("camera_modpost_symvers", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load camera modpost symvers helper")
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)

OWNER = "drivers/media/platform/exynos/camera/fimc-is"
SAMPLE = (
    b"0x0e3c515c\tmodule_layout\tvmlinux\tEXPORT_SYMBOL\t\n"
    b"0x12345678\tcamera_internal\t" + OWNER.encode() + b"\tEXPORT_SYMBOL_GPL\t\n"
    b"0xabcdef01\tother_export\tdrivers/example/module\tEXPORT_SYMBOL\t\n"
)


def pinned_arguments(data: bytes = SAMPLE) -> dict[str, object]:
    excluded = b"0x12345678\tcamera_internal\t" + OWNER.encode() + b"\tEXPORT_SYMBOL_GPL\t\n"
    return {
        "exclude_owner": OWNER,
        "expected_input_sha256": hashlib.sha256(data).hexdigest(),
        "expected_excluded_count": 1,
        "expected_excluded_sha256": hashlib.sha256(excluded).hexdigest(),
        "expected_module_layout_crc": "0x0e3c515c",
    }


class CameraModpostSymversTests(unittest.TestCase):
    def test_filter_removes_only_exact_owner_and_preserves_other_bytes(self) -> None:
        filtered, receipt = HELPER.filter_symvers(SAMPLE, **pinned_arguments())
        expected = (
            b"0x0e3c515c\tmodule_layout\tvmlinux\tEXPORT_SYMBOL\t\n"
            b"0xabcdef01\tother_export\tdrivers/example/module\tEXPORT_SYMBOL\t\n"
        )
        self.assertEqual(filtered, expected)
        self.assertEqual(receipt["input_rows"], 3)
        self.assertEqual(receipt["excluded_rows"], 1)
        self.assertEqual(receipt["dependency_rows"], 2)
        self.assertEqual(receipt["module_layout_crc"], "0x0e3c515c")

    def test_input_digest_excluded_rows_and_module_layout_are_pinned(self) -> None:
        arguments = pinned_arguments()
        arguments["expected_input_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "input Module.symvers SHA-256"):
            HELPER.filter_symvers(SAMPLE, **arguments)

        arguments = pinned_arguments()
        arguments["expected_excluded_count"] = 2
        with self.assertRaisesRegex(ValueError, "excluded row count"):
            HELPER.filter_symvers(SAMPLE, **arguments)

        arguments = pinned_arguments()
        arguments["expected_module_layout_crc"] = "0x00000000"
        with self.assertRaisesRegex(ValueError, "module_layout"):
            HELPER.filter_symvers(SAMPLE, **arguments)

    def test_cli_writes_new_output_and_refuses_to_replace_existing_output(self) -> None:
        arguments = pinned_arguments()
        with tempfile.TemporaryDirectory(prefix="camera-modpost-symvers-") as temp:
            root = Path(temp)
            source = root / "Module.symvers"
            output = root / "dependencies.symvers"
            source.write_bytes(SAMPLE)
            command = [
                sys.executable, "-I", "-B", str(SCRIPT),
                "--input", str(source), "--output", str(output),
                "--exclude-owner", OWNER,
                "--expected-input-sha256", str(arguments["expected_input_sha256"]),
                "--expected-excluded-count", str(arguments["expected_excluded_count"]),
                "--expected-excluded-sha256", str(arguments["expected_excluded_sha256"]),
                "--expected-module-layout-crc", str(arguments["expected_module_layout_crc"]),
            ]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads(result.stdout)
            self.assertEqual(output.read_bytes(), (
                b"0x0e3c515c\tmodule_layout\tvmlinux\tEXPORT_SYMBOL\t\n"
                b"0xabcdef01\tother_export\tdrivers/example/module\tEXPORT_SYMBOL\t\n"
            ))
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(receipt["dependency_sha256"],
                             hashlib.sha256(output.read_bytes()).hexdigest())

            output.write_bytes(b"preserve-existing-output")
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(output.read_bytes(), b"preserve-existing-output")

    def test_symlink_destination_is_refused_without_changing_target(self) -> None:
        with tempfile.TemporaryDirectory(prefix="camera-modpost-symlink-") as temp:
            root = Path(temp)
            target = root / "target"
            output = root / "dependencies.symvers"
            target.write_bytes(b"protected-target")
            output.symlink_to(target)
            with self.assertRaises(FileExistsError):
                HELPER.write_new_file(output, SAMPLE)
            self.assertEqual(target.read_bytes(), b"protected-target")

    def test_partial_write_is_preserved_and_cli_emits_no_success_receipt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="camera-modpost-partial-") as temp:
            root = Path(temp)
            source = root / "Module.symvers"
            output = root / "dependencies.symvers"
            source.write_bytes(SAMPLE)
            arguments = pinned_arguments()
            command = [
                "prepare-camera-modpost-symvers.py",
                "--input", str(source), "--output", str(output),
                "--exclude-owner", OWNER,
                "--expected-input-sha256", str(arguments["expected_input_sha256"]),
                "--expected-excluded-count", str(arguments["expected_excluded_count"]),
                "--expected-excluded-sha256", str(arguments["expected_excluded_sha256"]),
                "--expected-module-layout-crc", str(arguments["expected_module_layout_crc"]),
            ]

            class PartialWriter:
                def __init__(self, descriptor: int) -> None:
                    self.descriptor = descriptor

                def __enter__(self) -> "PartialWriter":
                    return self

                def __exit__(self, *_args: object) -> None:
                    HELPER.os.close(self.descriptor)

                def write(self, _data: bytes) -> None:
                    HELPER.os.write(self.descriptor, b"partial-output")
                    raise OSError("simulated write failure")

            stdout, stderr = StringIO(), StringIO()
            with mock.patch.object(sys, "argv", command), \
                    mock.patch.object(HELPER.os, "fdopen", side_effect=lambda fd, _mode: PartialWriter(fd)), \
                    redirect_stdout(stdout), redirect_stderr(stderr):
                result = HELPER.main()

            self.assertEqual(result, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("simulated write failure", stderr.getvalue())
            self.assertEqual(output.read_bytes(), b"partial-output")


if __name__ == "__main__":
    unittest.main()
