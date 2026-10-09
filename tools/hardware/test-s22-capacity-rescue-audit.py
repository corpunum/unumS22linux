#!/usr/bin/env python3
"""Hardware-free tests for the local capacity audit."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).with_name("s22-capacity-rescue-audit.py")
SPEC = importlib.util.spec_from_file_location("capacity_audit", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load capacity audit module")
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


def fake_space(available_bytes: int = 1000, available_inodes: int = 100):
    return {
        "total_bytes": 5000,
        "available_bytes": available_bytes,
        "used_bytes": 4000,
        "total_inodes": 200,
        "available_inodes": available_inodes,
        "used_inodes": 100,
    }


class MountParsing(unittest.TestCase):
    def test_mountinfo_escapes_and_longest_mountpoint(self):
        mounts = audit.parse_mountinfo(
            "36 25 0:32 / / rw,relatime - overlay overlay rw,upperdir=/safe\\040upper,workdir=/safe\\040work\n"
            "37 36 8:1 / /srv rw,relatime - ext4 /dev/test rw\n"
        )
        self.assertEqual(mounts[0].fs_type, "overlay")
        self.assertIn("upperdir=/safe upper", mounts[0].options)
        self.assertEqual(audit.mount_for(Path("/srv/data"), mounts), mounts[1])
        self.assertEqual(audit.mount_for(Path("/tmp/file"), mounts), mounts[0])


class DestinationAudit(unittest.TestCase):
    def setUp(self):
        self.mounts = (audit.Mount("0:1", "/", "/", "ext4", "rw"),)

    def test_missing_destination_counts_path_inodes_and_does_not_leak_path(self):
        with tempfile.TemporaryDirectory() as temp:
            target_path = Path(temp) / "new-dir" / "candidate.img"
            target = {
                "id": "artifact", "path": target_path, "path_type": "file",
                "required_bytes": 300, "required_inodes": 2,
                "reserve_bytes": 50, "reserve_inodes": 1,
            }
            with mock.patch.object(audit, "_statvfs", return_value=fake_space()):
                result = audit.audit_target(target, self.mounts)
            self.assertEqual(result["destination_status"], "ok")
            self.assertFalse(result["destination_exists"])
            self.assertEqual(result["auto_path_inodes"], 2)
            self.assertEqual(result["needed_bytes"], 350)
            self.assertEqual(result["needed_inodes"], 5)
            self.assertNotIn(str(target_path), json.dumps(result))
            self.assertEqual(result["capacity_status"], "sufficient")

    def test_existing_file_size_is_context_not_a_space_credit(self):
        with tempfile.TemporaryDirectory() as temp:
            target_path = Path(temp) / "existing.img"
            target_path.write_bytes(b"x" * 512)
            target = {
                "id": "image", "path": target_path, "path_type": "file",
                "required_bytes": 800, "required_inodes": 0,
                "reserve_bytes": 0, "reserve_inodes": 0,
            }
            with mock.patch.object(audit, "_statvfs", return_value=fake_space(available_bytes=700)):
                result = audit.audit_target(target, self.mounts)
            self.assertEqual(result["existing_bytes_context_only"], 512)
            self.assertEqual(result["needed_bytes"], 800)
            self.assertEqual(result["capacity_status"], "insufficient")

    def test_symlinked_destination_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            actual = root / "actual"
            actual.mkdir()
            link = root / "link"
            link.symlink_to(actual, target_is_directory=True)
            target = {
                "id": "linked", "path": link / "output", "path_type": "file",
                "required_bytes": 1, "required_inodes": 0,
                "reserve_bytes": 0, "reserve_inodes": 0,
            }
            result = audit.audit_target(target, self.mounts)
            self.assertEqual(result["destination_status"], "symlink")
            self.assertEqual(result["capacity_status"], "not_auditable")
            self.assertTrue(result["audited_path_is_symlink"])

    def test_same_filesystem_targets_are_aggregated_per_operation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "primary").mkdir()
            (root / "temp").mkdir()
            targets = [
                {"id": "primary", "path": root / "primary", "path_type": "directory",
                 "required_bytes": 60, "required_inodes": 0, "reserve_bytes": 0, "reserve_inodes": 0},
                {"id": "temporary-spool", "path": root / "temp", "path_type": "directory",
                 "required_bytes": 60, "required_inodes": 0, "reserve_bytes": 0, "reserve_inodes": 0},
            ]
            operation = [{"id": "build", "targets": targets}]
            with mock.patch.object(audit, "_statvfs", return_value=fake_space(available_bytes=100)):
                result = audit.audit_plan(operation, self.mounts)
            self.assertEqual(result["overall_capacity_status"], "review_required")
            aggregate = result["operations"][0]["filesystem_aggregates"][0]
            self.assertEqual(aggregate["needed_bytes"], 120)
            self.assertEqual(aggregate["capacity_status"], "insufficient")
            self.assertEqual(result["operations"][0]["targets"][1]["id"], "temporary-spool")

    def test_invalid_plan_paths_are_rejected(self):
        for bad_path in ("relative", "/tmp/../secret"):
            with self.subTest(path=bad_path), tempfile.TemporaryDirectory() as temp:
                plan_path = Path(temp) / "plan.json"
                plan_path.write_text(json.dumps({
                    "schema": 1,
                    "operations": [{"id": "op", "targets": [{
                        "id": "out", "path": bad_path, "path_type": "file",
                    }]}],
                }))
                with self.assertRaises(ValueError):
                    audit.load_plan(plan_path)

    def test_valid_plan_keeps_primary_and_incidental_targets_distinct(self):
        with tempfile.TemporaryDirectory() as temp:
            plan_path = Path(temp) / "plan.json"
            plan_path.write_text(json.dumps({
                "schema": 1,
                "operations": [{"id": "build", "targets": [
                    {"id": "primary", "path": str(Path(temp) / "output"),
                     "path_type": "directory", "required_bytes": 10},
                    {"id": "spool", "path": str(Path(temp) / "tmp"),
                     "path_type": "directory", "required_bytes": 20,
                     "required_inodes": 2},
                ]}],
            }))
            plan = audit.load_plan(plan_path)
            self.assertEqual(plan[0]["id"], "build")
            self.assertEqual([target["id"] for target in plan[0]["targets"]],
                             ["primary", "spool"])
            self.assertEqual(plan[0]["targets"][1]["required_inodes"], 2)


if __name__ == "__main__":
    unittest.main()
