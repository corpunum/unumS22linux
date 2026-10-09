#!/usr/bin/env python3
"""Host-only tests for passive physical-button event evidence."""
from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import types
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("button-event-evidence.py")
SPEC = importlib.util.spec_from_file_location("button_event_evidence", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"could not load button event evidence tool at {SCRIPT}")
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)


class ButtonEventEvidenceTests(unittest.TestCase):
    DEVICE = "/dev/input/event1"
    OTHER_DEVICE = "/dev/input/event2"

    def capable_node(self, *, device: str = DEVICE, name: str = "sec-pmic-key",
                     codes: tuple[str, ...] = ("KEY_POWER",)) -> dict[str, object]:
        def bitmap(bits: set[int]) -> str:
            if not bits:
                return "0"
            max_word = max(bits) // 64
            words = [sum(1 << (bit % 64) for bit in bits if bit // 64 == index)
                     for index in range(max_word + 1)]
            while len(words) > 1 and words[-1] == 0:
                words.pop()
            return " ".join(f"{word:x}" for word in reversed(words))

        caps = {name: name in codes for name in evidence.KEY_CODES}
        key_codes = {evidence.KEY_CODES[key] for key in codes}
        return {
            "event": device, "name": name,
            "physical_origin": "unknown_from_name_or_capabilities",
            "present": True, "sysfs_dev": "13:65",
            "node_identity": {"status": "matched", "expected": "13:65",
                              "actual": "13:65"},
            "capabilities": {
                "format": "linux-input-input_print_bitmap", "word_bits": 64,
                "ev": {"status": "valid", "raw": "3"},
                "key": {"status": "valid", "raw": bitmap(key_codes)},
            },
            "key_capable": True, "button_capabilities": caps,
            "button_observation_candidate": True,
        }

    def ev(self, event_type: int, code: int, value: int,
           *, device: str = DEVICE) -> dict[str, object]:
        return {"device": device, "name": "sec-pmic-key", "sec": 1,
                "usec": 0, "type": event_type, "code": code, "value": value}

    def power_tap(self, *, device: str = DEVICE) -> list[dict[str, object]]:
        return [
            self.ev(evidence.EV_KEY, evidence.KEY_CODES["KEY_POWER"], 1, device=device),
            self.ev(evidence.EV_SYN, evidence.SYN_REPORT, 0, device=device),
            self.ev(evidence.EV_KEY, evidence.KEY_CODES["KEY_POWER"], 0, device=device),
            self.ev(evidence.EV_SYN, evidence.SYN_REPORT, 0, device=device),
        ]

    def fake_readiness(self, *nodes: dict[str, object]) -> types.SimpleNamespace:
        capability_reader = evidence._readiness_module()
        return types.SimpleNamespace(
            inputs=lambda *_args: list(nodes),
            parse_sysfs_bitmap=capability_reader.parse_sysfs_bitmap,
            EV_MAX=capability_reader.EV_MAX,
            KEY_MAX=capability_reader.KEY_MAX,
        )

    def fake_character_stat(self) -> types.SimpleNamespace:
        return types.SimpleNamespace(
            st_mode=stat.S_IFCHR | 0o660,
            st_rdev=os.makedev(13, 65),
        )

    def assert_descriptors_closed(self, descriptors: list[int]) -> None:
        self.assertEqual(len(descriptors), 1)
        with self.assertRaises(OSError):
            os.fstat(descriptors[0])

    def test_complete_press_release_requires_capability_and_syn_reports(self) -> None:
        inventory = [self.capable_node()]
        result = evidence.analyze_button_events(inventory, self.power_tap())
        power = result["KEY_POWER"]
        self.assertEqual(power["status"], "observed_press_release")
        self.assertEqual(power["capable_nodes"], [self.DEVICE])
        self.assertEqual(power["nodes"][0]["status"], "observed_press_release")
        self.assertEqual(power["built_in_physical_button_acceptance"], "not_established")
        self.assertIn("cannot prove origin", power["physical_origin"])
        self.assertEqual(power["compositor_or_audio_effect"], "not measured by this collector")

    def test_repeat_is_distinct_and_repeat_alone_is_not_a_press_release(self) -> None:
        inventory = [self.capable_node()]
        repeated_tap = [
            self.ev(evidence.EV_KEY, 116, 1), self.ev(evidence.EV_SYN, 0, 0),
            self.ev(evidence.EV_KEY, 116, 2), self.ev(evidence.EV_SYN, 0, 0),
            self.ev(evidence.EV_KEY, 116, 0), self.ev(evidence.EV_SYN, 0, 0),
        ]
        self.assertEqual(evidence.analyze_button_events(inventory, repeated_tap)["KEY_POWER"]["status"],
                         "observed_press_release_with_repeat")
        repeat_only = [self.ev(evidence.EV_KEY, 116, 2), self.ev(evidence.EV_SYN, 0, 0)]
        self.assertEqual(evidence.analyze_button_events(inventory, repeat_only)["KEY_POWER"]["status"],
                         "repeat_without_press_release")

    def test_no_events_and_one_sided_transitions_are_not_acceptance(self) -> None:
        inventory = [self.capable_node()]
        self.assertEqual(evidence.analyze_button_events(inventory, [])["KEY_POWER"]["status"],
                         "no_matching_key_events")
        press_only = [self.ev(evidence.EV_KEY, 116, 1), self.ev(evidence.EV_SYN, 0, 0)]
        release_only = [self.ev(evidence.EV_KEY, 116, 0), self.ev(evidence.EV_SYN, 0, 0)]
        self.assertEqual(evidence.analyze_button_events(inventory, press_only)["KEY_POWER"]["status"],
                         "press_without_release")
        self.assertEqual(evidence.analyze_button_events(inventory, release_only)["KEY_POWER"]["status"],
                         "release_without_prior_press")

    def test_syn_dropped_invalid_values_and_unterminated_frames_are_incomplete(self) -> None:
        inventory = [self.capable_node()]
        dropped = [self.ev(evidence.EV_SYN, evidence.SYN_DROPPED, 0), *self.power_tap()]
        self.assertEqual(evidence.analyze_button_events(inventory, dropped)["KEY_POWER"]["status"],
                         "incomplete_capture")
        unterminated = [self.ev(evidence.EV_KEY, 116, 1)]
        self.assertEqual(evidence.analyze_button_events(inventory, unterminated)["KEY_POWER"]["nodes"][0]["partial_frame"],
                         True)
        self.assertEqual(evidence.analyze_button_events(inventory, unterminated)["KEY_POWER"]["status"],
                         "incomplete_capture")
        invalid = [self.ev(evidence.EV_KEY, 116, 7), self.ev(evidence.EV_SYN, 0, 0)]
        self.assertEqual(evidence.analyze_button_events(inventory, invalid)["KEY_POWER"]["status"],
                         "incomplete_capture")

    def test_partial_record_flood_eof_and_open_failure_never_report_sequence(self) -> None:
        inventory = [self.capable_node()]
        statuses = [{"device": self.DEVICE, "partial_record": True}]
        self.assertEqual(evidence.analyze_button_events(inventory, self.power_tap(), statuses)["KEY_POWER"]["status"],
                         "incomplete_capture")
        statuses = [{"device": self.DEVICE, "event_limit_reached": True}]
        self.assertEqual(evidence.analyze_button_events(inventory, self.power_tap(), statuses)["KEY_POWER"]["status"],
                         "incomplete_capture")
        statuses = [{"device": self.DEVICE, "eof": True}]
        self.assertEqual(evidence.analyze_button_events(inventory, self.power_tap(), statuses)["KEY_POWER"]["status"],
                         "incomplete_capture")
        statuses = [{"device": self.DEVICE, "open_error": "PermissionError"}]
        self.assertEqual(evidence.analyze_button_events(inventory, self.power_tap(), statuses)["KEY_POWER"]["status"],
                         "incomplete_capture")

    def test_capability_and_name_only_sources_cannot_be_promoted_by_events(self) -> None:
        touch = self.capable_node(device=self.OTHER_DEVICE, name="sec_touchscreen", codes=())
        touch["button_observation_candidate"] = False
        touch["key_capable"] = True
        name_only = self.capable_node(device=self.OTHER_DEVICE, name="power-button", codes=())
        name_only["button_observation_candidate"] = False
        events = self.power_tap(device=self.OTHER_DEVICE)
        self.assertEqual(evidence.analyze_button_events([touch], events)["KEY_POWER"]["status"],
                         "no_capable_node")
        self.assertEqual(evidence.analyze_button_events([name_only], events)["KEY_POWER"]["status"],
                         "no_capable_node")

        unrelated_keyboard = self.capable_node(device=self.OTHER_DEVICE, name="external-keyboard",
                                               codes=("KEY_POWER",))
        external_result = evidence.analyze_button_events([unrelated_keyboard], events)
        self.assertEqual(external_result["KEY_POWER"]["status"], "observed_press_release")
        self.assertEqual(external_result["KEY_POWER"]["nodes"][0]["built_in_physical_button_acceptance"],
                         "not_established")
        self.assertEqual(external_result["KEY_POWER"]["built_in_physical_button_acceptance"],
                         "not_established")

    def test_event_code_not_in_this_nodes_capability_bitmap_is_incomplete(self) -> None:
        inventory = [self.capable_node(codes=("KEY_POWER",))]
        volume_up = [
            self.ev(evidence.EV_KEY, evidence.KEY_CODES["KEY_VOLUMEUP"], 1),
            self.ev(evidence.EV_SYN, 0, 0),
            self.ev(evidence.EV_KEY, evidence.KEY_CODES["KEY_VOLUMEUP"], 0),
            self.ev(evidence.EV_SYN, 0, 0),
        ]
        power = evidence.analyze_button_events(inventory, volume_up)["KEY_POWER"]
        self.assertEqual(power["status"], "incomplete_capture")
        self.assertTrue(power["nodes"][0]["capability_violation"])

    def test_malformed_records_for_candidate_nodes_fail_closed(self) -> None:
        malformed = {"device": self.DEVICE, "type": "1", "code": 116,
                     "value": 1, "sec": 1, "usec": 0}
        result = evidence.analyze_button_events([self.capable_node()], [malformed])
        self.assertEqual(result["KEY_POWER"]["status"], "incomplete_capture")
        self.assertTrue(result["KEY_POWER"]["nodes"][0]["invalid_event_record"])

    def test_offline_cli_analyzes_prior_capture_without_opening_devices(self) -> None:
        document = {
            "schema_version": 1,
            "inventory": [self.capable_node()],
            "capture": {"status": "window_completed", "node_statuses": []},
            "events": self.power_tap(),
        }
        with tempfile.TemporaryDirectory(prefix="button-event-analyze-") as directory:
            path = Path(directory) / "capture.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = evidence.main(["--analyze", str(path)])
        self.assertEqual(result, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["button_analysis"]["KEY_POWER"]["status"],
                         "observed_press_release")
        self.assertEqual(report["claim_limits"]["audio_route_effect"], "not measured")

    def test_collector_opens_only_verified_candidate_read_only_and_filters_private_keys(self) -> None:
        with tempfile.TemporaryDirectory(prefix="button-event-evidence-") as directory:
            root = Path(directory)
            candidate_path = root / "event1"
            unrelated_path = root / "event2"
            data = b"".join([
                evidence.EVENT.pack(1, 0, evidence.EV_KEY, 116, 1),
                evidence.EVENT.pack(1, 0, evidence.EV_SYN, 0, 0),
                evidence.EVENT.pack(1, 1, evidence.EV_KEY, 116, 0),
                evidence.EVENT.pack(1, 1, evidence.EV_SYN, 0, 0),
                evidence.EVENT.pack(1, 2, evidence.EV_KEY, 30, 1),
            ])
            candidate_path.write_bytes(data)
            unrelated_path.write_bytes(evidence.EVENT.pack(1, 0, evidence.EV_KEY, 30, 1))
            candidate = self.capable_node(device=str(candidate_path), codes=("KEY_POWER",))
            unrelated = self.capable_node(device=str(unrelated_path), name="external-keyboard",
                                          codes=("KEY_POWER",))
            unrelated["button_observation_candidate"] = False
            capability_reader = evidence._readiness_module()
            fake_readiness = types.SimpleNamespace(
                inputs=lambda *_args: [candidate, unrelated],
                parse_sysfs_bitmap=capability_reader.parse_sysfs_bitmap,
                EV_MAX=capability_reader.EV_MAX,
                KEY_MAX=capability_reader.KEY_MAX,
            )
            real_open = os.open
            opened: list[str] = []

            def readonly_open(path: str, flags: int, *args: object, **kwargs: object) -> int:
                self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
                self.assertFalse(flags & getattr(os, "O_TRUNC", 0))
                opened.append(path)
                return real_open(path, flags, *args, **kwargs)

            fake_stat = types.SimpleNamespace(
                st_mode=stat.S_IFCHR | 0o660,
                st_rdev=os.makedev(13, 65),
            )
            with mock.patch.object(evidence, "_readiness_module", return_value=fake_readiness), \
                    mock.patch.object(evidence.os, "open", side_effect=readonly_open), \
                    mock.patch.object(evidence.os, "fstat", return_value=fake_stat), \
                    mock.patch.object(evidence.os, "write",
                                      side_effect=AssertionError("collector wrote to a node")), \
                    mock.patch.object(fcntl, "ioctl",
                                      side_effect=AssertionError("collector issued an ioctl")):
                result = evidence.collect(0.2, max_events=32)

            self.assertEqual(opened, [str(candidate_path)])
            retained = [(event["type"], event["code"], event["value"])
                        for event in result["events"]]
            self.assertIn((evidence.EV_KEY, 116, 1), retained)
            self.assertIn((evidence.EV_KEY, 116, 0), retained)
            self.assertIn((evidence.EV_SYN, evidence.SYN_REPORT, 0), retained)
            self.assertFalse(any(event["type"] == evidence.EV_KEY and event["code"] == 30
                                 for event in result["events"]))
            self.assertEqual(result["capture"]["status"],
                             "one_or_more_streams_ended_or_failed")
            self.assertEqual(result["button_analysis"]["KEY_POWER"]["status"],
                             "incomplete_capture")

    def test_noncharacter_opened_descriptor_is_closed_without_reading(self) -> None:
        with tempfile.TemporaryDirectory(prefix="button-event-identity-") as directory:
            path = Path(directory) / "event1"
            path.write_bytes(b"")
            candidate = self.capable_node(device=str(path), codes=("KEY_POWER",))
            capability_reader = evidence._readiness_module()
            fake_readiness = types.SimpleNamespace(
                inputs=lambda *_args: [candidate],
                parse_sysfs_bitmap=capability_reader.parse_sysfs_bitmap,
                EV_MAX=capability_reader.EV_MAX,
                KEY_MAX=capability_reader.KEY_MAX,
            )
            opened: list[str] = []
            real_open = os.open

            def record_open(target: str, flags: int, *args: object, **kwargs: object) -> int:
                opened.append(target)
                return real_open(target, flags, *args, **kwargs)

            with mock.patch.object(evidence, "_readiness_module", return_value=fake_readiness), \
                    mock.patch.object(evidence.os, "open", side_effect=record_open), \
                    mock.patch.object(evidence.os, "read",
                                     side_effect=AssertionError("unverified descriptor was read")):
                result = evidence.collect(0.1, max_events=4)
            self.assertEqual(opened, [str(path)])
            self.assertEqual(result["capture"]["status"], "no_nodes_opened")
            self.assertEqual(result["capture"]["node_statuses"][0]["open_error"],
                             "opened_node_not_character_device")

    def test_mismatched_opened_device_number_is_closed_without_reading(self) -> None:
        with tempfile.TemporaryDirectory(prefix="button-event-device-mismatch-") as directory:
            path = Path(directory) / "event1"
            path.write_bytes(b"")
            candidate = self.capable_node(device=str(path), codes=("KEY_POWER",))
            capability_reader = evidence._readiness_module()
            fake_readiness = types.SimpleNamespace(
                inputs=lambda *_args: [candidate],
                parse_sysfs_bitmap=capability_reader.parse_sysfs_bitmap,
                EV_MAX=capability_reader.EV_MAX,
                KEY_MAX=capability_reader.KEY_MAX,
            )
            wrong_stat = types.SimpleNamespace(
                st_mode=stat.S_IFCHR | 0o660,
                st_rdev=os.makedev(13, 66),
            )
            with mock.patch.object(evidence, "_readiness_module", return_value=fake_readiness), \
                    mock.patch.object(evidence.os, "fstat", return_value=wrong_stat), \
                    mock.patch.object(evidence.os, "read",
                                      side_effect=AssertionError("mismatched node was read")):
                result = evidence.collect(0.1, max_events=4)
            self.assertEqual(result["capture"]["status"], "no_nodes_opened")
            self.assertEqual(result["capture"]["node_statuses"][0]["open_error"],
                             "opened_node_device_number_mismatch")

    def test_collect_read_error_is_incomplete_and_closes_open_descriptor(self) -> None:
        with tempfile.TemporaryDirectory(prefix="button-event-read-error-") as directory:
            path = Path(directory) / "event1"
            path.write_bytes(b"")
            candidate = self.capable_node(device=str(path), codes=("KEY_POWER",))
            descriptors: list[int] = []
            real_open = os.open

            def record_open(target: str, flags: int, *args: object, **kwargs: object) -> int:
                fd = real_open(target, flags, *args, **kwargs)
                descriptors.append(fd)
                return fd

            def read_error(_fd: int, _size: int) -> bytes:
                raise OSError(5, "host-test read error")

            with mock.patch.object(evidence, "_readiness_module",
                                   return_value=self.fake_readiness(candidate)), \
                    mock.patch.object(evidence.os, "open", side_effect=record_open), \
                    mock.patch.object(evidence.os, "fstat",
                                      return_value=self.fake_character_stat()), \
                    mock.patch.object(evidence.os, "read", side_effect=read_error) as read, \
                    mock.patch.object(evidence.select, "select",
                                      side_effect=lambda readers, *_args: (readers, [], [])) as select, \
                    mock.patch.object(evidence.time, "monotonic", return_value=0.0):
                result = evidence.collect(5, max_events=8)

            node = result["capture"]["node_statuses"][0]
            self.assertEqual(read.call_count, 1)
            self.assertEqual(select.call_count, 1)
            self.assertEqual(result["capture"]["status"],
                             "one_or_more_streams_ended_or_failed")
            self.assertEqual(node["read_error"], "OSError")
            self.assertEqual(result["events"], [])
            self.assertEqual(result["button_analysis"]["KEY_POWER"]["status"],
                             "incomplete_capture")
            self.assert_descriptors_closed(descriptors)

    def test_collect_partial_record_at_eof_is_incomplete_and_closes_descriptor(self) -> None:
        with tempfile.TemporaryDirectory(prefix="button-event-partial-eof-") as directory:
            path = Path(directory) / "event1"
            path.write_bytes(b"")
            candidate = self.capable_node(device=str(path), codes=("KEY_POWER",))
            descriptors: list[int] = []
            real_open = os.open
            chunks = iter((b"\x01\x02\x03", b""))

            def record_open(target: str, flags: int, *args: object, **kwargs: object) -> int:
                fd = real_open(target, flags, *args, **kwargs)
                descriptors.append(fd)
                return fd

            with mock.patch.object(evidence, "_readiness_module",
                                   return_value=self.fake_readiness(candidate)), \
                    mock.patch.object(evidence.os, "open", side_effect=record_open), \
                    mock.patch.object(evidence.os, "fstat",
                                      return_value=self.fake_character_stat()), \
                    mock.patch.object(evidence.os, "read",
                                      side_effect=lambda _fd, _size: next(chunks)) as read, \
                    mock.patch.object(evidence.select, "select",
                                      side_effect=lambda readers, *_args: (readers, [], [])) as select, \
                    mock.patch.object(evidence.time, "monotonic", return_value=0.0):
                result = evidence.collect(5, max_events=8)

            node = result["capture"]["node_statuses"][0]
            self.assertEqual(read.call_count, 2)
            self.assertEqual(select.call_count, 1)
            self.assertTrue(node["eof"])
            self.assertTrue(node["partial_record"])
            self.assertEqual(node["partial_record_bytes"], 3)
            self.assertEqual(result["capture"]["status"],
                             "one_or_more_streams_ended_or_failed")
            self.assertEqual(result["events"], [])
            self.assertEqual(result["button_analysis"]["KEY_POWER"]["status"],
                             "incomplete_capture")
            self.assert_descriptors_closed(descriptors)

    def test_collect_record_limit_counts_discarded_private_key_and_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="button-event-record-limit-") as directory:
            path = Path(directory) / "event1"
            path.write_bytes(b"")
            candidate = self.capable_node(device=str(path), codes=("KEY_POWER",))
            descriptors: list[int] = []
            real_open = os.open
            records = b"".join((
                evidence.EVENT.pack(1, 0, evidence.EV_KEY, 116, 1),
                evidence.EVENT.pack(1, 0, evidence.EV_SYN, evidence.SYN_REPORT, 0),
                evidence.EVENT.pack(1, 1, evidence.EV_KEY, 116, 0),
                evidence.EVENT.pack(1, 1, evidence.EV_SYN, evidence.SYN_REPORT, 0),
                evidence.EVENT.pack(1, 2, evidence.EV_KEY, 30, 1),
            ))

            def record_open(target: str, flags: int, *args: object, **kwargs: object) -> int:
                fd = real_open(target, flags, *args, **kwargs)
                descriptors.append(fd)
                return fd

            def bounded_read(_fd: int, size: int) -> bytes:
                return records[:size]

            with mock.patch.object(evidence, "_readiness_module",
                                   return_value=self.fake_readiness(candidate)), \
                    mock.patch.object(evidence.os, "open", side_effect=record_open), \
                    mock.patch.object(evidence.os, "fstat",
                                      return_value=self.fake_character_stat()), \
                    mock.patch.object(evidence.os, "read", side_effect=bounded_read) as read, \
                    mock.patch.object(evidence.select, "select",
                                      side_effect=lambda readers, *_args: (readers, [], [])) as select, \
                    mock.patch.object(evidence.time, "monotonic", return_value=0.0):
                result = evidence.collect(5, max_events=5)

            node = result["capture"]["node_statuses"][0]
            self.assertEqual(read.call_count, 1)
            self.assertEqual(select.call_count, 1)
            self.assertEqual(result["capture"]["event_records_read"], 5)
            self.assertEqual(result["capture"]["status"], "event_record_limit_reached")
            self.assertFalse(result["capture"]["complete_window"])
            self.assertTrue(node["event_limit_reached"])
            power = result["button_analysis"]["KEY_POWER"]
            self.assertTrue(power["nodes"][0]["press_seen"])
            self.assertTrue(power["nodes"][0]["release_after_press_seen"])
            self.assertEqual(power["status"], "incomplete_capture")
            self.assertFalse(any(event["type"] == evidence.EV_KEY and event["code"] == 30
                                 for event in result["events"]))
            self.assertNotIn('"code": 30', json.dumps(result))
            self.assert_descriptors_closed(descriptors)

    def test_event_and_time_budgets_are_explicit(self) -> None:
        with self.assertRaises(ValueError):
            evidence.collect(float("nan"))
        with self.assertRaises(ValueError):
            evidence.collect(0, max_events=0)
        with self.assertRaises(ValueError):
            evidence.collect(301)


if __name__ == "__main__":
    unittest.main(verbosity=2)
