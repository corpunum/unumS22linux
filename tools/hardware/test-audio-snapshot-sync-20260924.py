#!/usr/bin/env python3
"""Host fake-filesystem tests for synchronized audio snapshot accounting."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).with_name("audio-progress-snapshot.py")
SPEC = importlib.util.spec_from_file_location("audio_snapshot", SOURCE)
SNAPSHOT = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(SNAPSHOT)


class SnapshotSynchronizationTests(unittest.TestCase):
    def capture(self, untimed_path=None):
        paths = (
            SNAPSHOT.PCM / "status",
            SNAPSHOT.ABOX / "power/runtime_status",
            SNAPSHOT.MAP / "cache_only",
            SNAPSHOT.ABOX / "service",
            SNAPSHOT.ABOX / "reset_count",
        )
        values = (
            "state: RUNNING\nowner_pid: 1\nhw_ptr: 0\nappl_ptr: 0\n",
            "active\n", "N\n", "1\n", "0\n",
        )
        records = {}
        for index, (path, value) in enumerate(zip(paths, values)):
            if untimed_path == str(path):
                records[str(path)] = {"status": "not_present", "value": None}
            else:
                start = 1000 + index * 100
                records[str(path)] = {
                    "status": "ok", "value": value,
                    "start_monotonic_ns": start,
                    "end_monotonic_ns": start + 10,
                }

        def bounded(path, _limit):
            path = str(path)
            if path == "/proc/1/comm":
                return "native-guardian\n"
            if path == str(SNAPSHOT.MAP / "range"):
                return "0-8\n1200-1230\n1238-1238\n"
            if path == str(SNAPSHOT.MAP / "access"):
                return "0000: y y n n\n1200: y y y n\n1230: y n y n\n1238: y n y n\n"
            raise AssertionError(f"unexpected bounded read: {path}")

        def timed_text(path, *_args, **_kwargs):
            return dict(records[str(path)])

        ticks = iter((100, 200, 210, 900))
        with patch.object(SNAPSHOT, "bounded", side_effect=bounded), \
             patch.object(SNAPSHOT, "plans", return_value=[]), \
             patch.object(SNAPSHOT, "timed_text", side_effect=timed_text), \
             patch.object(SNAPSHOT.time, "monotonic_ns", side_effect=lambda: next(ticks)):
            return SNAPSHOT.snapshot(read_status=False)

    def test_snapshot_aggregate_marks_every_timed_observation(self):
        result = self.capture()
        synchronization = result["synchronization"]
        self.assertEqual(synchronization["clock_domain"], "CLOCK_MONOTONIC")
        self.assertEqual(synchronization["observation_count"], 5)
        self.assertEqual(synchronization["timed_observation_count"], 5)
        self.assertTrue(synchronization["all_observations_timed"])
        self.assertEqual(synchronization["observation_window_span_ns"], 410)

    def test_missing_observation_interval_cannot_be_counted_as_synchronized(self):
        untimed = str(SNAPSHOT.ABOX / "power/runtime_status")
        result = self.capture(untimed_path=untimed)
        synchronization = result["synchronization"]
        self.assertEqual(synchronization["observation_count"], 5)
        self.assertEqual(synchronization["timed_observation_count"], 4)
        self.assertFalse(synchronization["all_observations_timed"])


if __name__ == "__main__":
    unittest.main()
