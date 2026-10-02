#!/usr/bin/env python3
"""Host-only tests for the S22 ABOX capture-gap profile."""

from __future__ import annotations

import importlib.util
import io
import json
import shutil
import tempfile
import unittest
from unittest import mock
from pathlib import Path


SCRIPT = Path(__file__).with_name("audio-log-capture-profile.py")
SPEC = importlib.util.spec_from_file_location("audio_log_capture_profile", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load audio-log-capture-profile.py")
profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile)


def trial19_summary() -> dict:
    return {
        "schema": profile.TRIAL_SCHEMA,
        "trial_id": "muted-trial19",
        "sample_count": 19,
        "status_counts": {"RUNNING": 19},
        "running_hw_ptr_counts": {"0": 19},
        "running_rdma_enable_counts": {"false": 19},
        "retained_boundary_marker_counts": {
            "request_queue_result": 0,
            "mailbox_sender": 0,
            "selected_pointer_handler": 0,
        },
        "capture_coverage": "unknown",
        "log_policy": {
            "scope": "current",
            "linked_to_trial": False,
            "debug_level": 5,
            "objects": {
                "abox-mem": {"enabled": True, "level": 2},
                "abox-file": {"enabled": False, "level": 2},
            },
        },
        "memlog_payload_reader_opened": False,
    }


class CaptureProfileTests(unittest.TestCase):
    def test_trial_label_is_bounded_and_has_no_private_delimiters(self) -> None:
        for label in ("", "a" * 65, "a\nsecret", "user@host", "/private/path", " label"):
            with self.subTest(label=label), self.assertRaises(profile.ProfileError):
                summary = trial19_summary()
                summary["trial_id"] = label
                profile.validate_trial_summary(summary)

    def test_summary_reader_caps_actual_file_and_stdin_reads(self) -> None:
        raw = json.dumps(trial19_summary()).encode()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "summary.json"
            path.write_bytes(raw)
            self.assertEqual(profile._read_summary(str(path)), trial19_summary())
            path.write_bytes(b" " * (profile.MAX_SUMMARY_BYTES + 1))
            with self.assertRaises(profile.ProfileError):
                profile._read_summary(str(path))
        stream = io.BytesIO(b" " * (profile.MAX_SUMMARY_BYTES + 200))
        with mock.patch.object(profile.sys, "stdin", mock.Mock(buffer=stream)):
            with self.assertRaises(profile.ProfileError):
                profile._read_summary("-")
        self.assertEqual(stream.tell(), profile.MAX_SUMMARY_BYTES + 1)

    def test_trial19_flats_are_observations_not_failure_localization(self) -> None:
        trial = profile.validate_trial_summary(trial19_summary())
        gap = profile.summarize_capture_gap(trial)
        self.assertEqual(gap["observed"]["running_samples"], 19)
        self.assertTrue(gap["observed"]["all_running_hw_ptr_values_zero"])
        self.assertTrue(gap["observed"]["all_running_rdma_enable_values_false"])
        self.assertEqual(
            gap["historical_policy_assessment"],
            "current_policy_filters_debug_but_trial_policy_unlinked",
        )
        self.assertTrue(all(
            row["status"] == "unknown_no_marker_with_unproven_coverage"
            for row in gap["producer_boundaries"].values()
        ))
        self.assertFalse(gap["absence_is_negative_evidence"])

    def test_summary_rejects_raw_trace_data_and_claimed_coverage(self) -> None:
        summary = trial19_summary()
        summary["trace_payload"] = "private trace text"
        with self.assertRaises(profile.ProfileError):
            profile.validate_trial_summary(summary)

        summary = trial19_summary()
        summary["capture_coverage"] = "proven"
        with self.assertRaises(profile.ProfileError):
            profile.validate_trial_summary(summary)

        summary = trial19_summary()
        summary["unrecognized_metadata"] = "ignored fields are not accepted"
        with self.assertRaises(profile.ProfileError):
            profile.validate_trial_summary(summary)

    def test_unlinked_historical_policy_is_not_reported_as_current(self) -> None:
        for linked in (False, True):
            with self.subTest(linked=linked):
                summary = trial19_summary()
                summary["log_policy"].update(scope="historical_trial", linked_to_trial=linked)
                gap = profile.summarize_capture_gap(profile.validate_trial_summary(summary))
                self.assertEqual(gap["supplied_policy_scope"], "historical_trial")
                self.assertTrue(gap["supplied_abox_mem_debug_policy_filters_level"])
                self.assertEqual(gap["historical_policy_assessment"],
                                 "linked_historical_policy_filters_debug" if linked else
                                 "unlinked_historical_policy_filters_debug")
                self.assertNotIn("current_abox_mem_debug_policy_filters_level", gap)

    def test_summary_requires_payload_reader_state_and_complete_pairs(self) -> None:
        summary = trial19_summary()
        summary.pop("memlog_payload_reader_opened")
        with self.assertRaises(profile.ProfileError):
            profile.validate_trial_summary(summary)

        summary = trial19_summary()
        summary["running_hw_ptr_counts"] = {"0": 18}
        with self.assertRaises(profile.ProfileError):
            profile.validate_trial_summary(summary)

    def test_trace_snapshot_refuses_unknown_owner_or_pause_state(self) -> None:
        unknown = profile.trace_read_decision({
            "trace_owner_verified": False,
            "pause_on_trace": "unknown",
            "snd_pcm_hwptr_event_enabled": "unknown",
            "capture_window_linked_to_trace_buffer": False,
        })
        self.assertFalse(unknown["read_only_trace_snapshot_ready"])
        self.assertEqual(
            unknown["decision"],
            "refuse_until_owner_and_trace_state_are_verified",
        )

        reviewed = profile.trace_read_decision({
            "trace_owner_verified": True,
            "pause_on_trace": False,
            "snd_pcm_hwptr_event_enabled": True,
            "capture_window_linked_to_trace_buffer": True,
        })
        self.assertTrue(reviewed["read_only_trace_snapshot_ready"])

    def test_public_header_fragment_is_exact_and_tampering_fails_closed(self) -> None:
        fixture_header = (
            profile.PUBLIC_DEV_DBG_FRAGMENT
            + "\n#ifdef CONFIG_PRINTK\n#define dev_level_once\n"
        )
        self.assertEqual(
            profile._extract_dev_dbg_fragment(fixture_header),
            profile.PUBLIC_DEV_DBG_FRAGMENT,
        )
        tampered_header = fixture_header.replace("#elif defined(DEBUG)", "#elif defined(OTHER)")
        with self.assertRaises(profile.ProfileError):
            profile._extract_dev_dbg_fragment(tampered_header)

    @unittest.skipUnless(shutil.which("cc") or shutil.which("gcc"),
                         "host C preprocessor unavailable")
    def test_cc_e_preprocesses_exact_public_fragment_with_controlled_defines(self) -> None:
        result = profile.preprocess_dev_dbg_fragment()
        self.assertEqual(result["state"], "executed_exact_public_header_fragment")
        self.assertEqual(result["target_build_macro_state"],
                         "unknown_no_retained_o_tree_or_cmd_files")
        branches = {name: row["selected_branch"]
                    for name, row in result["cases"].items()}
        self.assertEqual(branches, {
            "no_enabling_macros": "compiled_out",
            "DEBUG": "printk_debug",
            "CONFIG_DYNAMIC_DEBUG": "dynamic_debug",
            "CONFIG_DYNAMIC_DEBUG_CORE_only": "compiled_out",
            "CONFIG_DYNAMIC_DEBUG_CORE_and_module": "dynamic_debug",
        })


if __name__ == "__main__":
    unittest.main()
