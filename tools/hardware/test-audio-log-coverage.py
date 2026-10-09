#!/usr/bin/env python3
"""Executable host regression tests for audio-log-coverage.py.

These test Python policy/classification logic and static source contracts, not
kernel C execution. Set S22_AUDIO_LOG_SOURCE_TREE and
S22_AUDIO_LOG_BUILD_TREE to include the retained-source/O-tree integration test.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("audio-log-coverage.py")
SPEC = importlib.util.spec_from_file_location("audio_log_coverage", SCRIPT)
assert SPEC and SPEC.loader
coverage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(coverage)


def metadata(scope: str = "current", *, linked: bool = False,
             objects: dict | None = None, reader: str = "metadata_only") -> dict:
    return {
        "schema": "audio-log-metadata/v1",
        "snapshot": {
            "scope": scope,
            "observed_at": "sanitized-test-snapshot",
            "trial_id": "audio-zero-node-20260927",
            "linked_trial_window": linked,
            "objects": objects or {
                "abox-mem": {"enabled": 1, "level": 2},
                "abox-file": {"enabled": 0, "level": 2},
            },
        },
        "requested_reader": reader,
        "policy_write_requested": False,
    }


class AudioLogCoverageTests(unittest.TestCase):
    def test_current_policy_is_filtered_but_not_historical_coverage(self) -> None:
        snapshot = coverage._snapshot_summary(metadata())
        state = coverage._memlog_debug_state(snapshot)
        self.assertEqual(state["abox_mem_debug_message_state"],
                         "filtered_debug_level_above_object_limit")
        self.assertEqual(state["abox_file_sink_state"], "disabled")
        self.assertEqual(snapshot["scope"], "current")
        self.assertFalse(snapshot["linked_trial_window"])

    def test_linked_historical_metadata_still_does_not_prove_marker_capture(self) -> None:
        snap = metadata("historical_trial", linked=True)
        summary = coverage._snapshot_summary(snap)
        self.assertEqual(summary["scope"], "historical_trial")
        self.assertTrue(summary["linked_trial_window"])
        self.assertEqual(
            coverage._memlog_debug_state(summary)["abox_mem_debug_message_state"],
            "filtered_debug_level_above_object_limit",
        )

    def test_missing_policy_metadata_stays_unknown(self) -> None:
        snap = coverage._snapshot_summary({"snapshot": {
            "scope": "current", "objects": {"abox-file": {"enabled": 0, "level": 2}}
        }})
        state = coverage._memlog_debug_state(snap)
        self.assertEqual(state["abox_mem_debug_message_state"],
                         "unknown_incomplete_abox_mem_metadata")
        self.assertEqual(snap["objects"]["abox-mem"]["state"],
                         "unknown_missing_metadata")

    def test_consuming_readers_are_refused_and_metadata_only_is_allowed(self) -> None:
        self.assertEqual(coverage._reader_decision("metadata_only")["decision"],
                         "metadata_only_no_log_buffer_opened")
        for reader in ("memlog_char_device", "memlog_to_string"):
            result = coverage._reader_decision(reader)
            self.assertFalse(result["payload_read_allowed"])
            self.assertEqual(result["decision"], "refuse_consuming_shared_cursor_reader")

    def test_missing_build_artifact_is_unknown_not_negative_runtime_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            command = coverage._command_record(Path(tmp) / ".abox.cmd", "abox.c")
        self.assertFalse(command["available"])
        self.assertEqual(coverage._dev_dbg_class({}, command, True),
                         "unknown_missing_compiled_command")

    def test_raw_payload_input_is_rejected_recursively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metadata.json"
            path.write_text('{"schema":"audio-log-metadata/v1",'
                            '"snapshot":{"capture":{"raw_log":"private"}}}',
                            encoding="utf-8")
            with self.assertRaises(coverage.CoverageError):
                coverage._read_metadata(str(path))

    def test_dynamic_debug_compile_flag_matrix(self) -> None:
        base = {"available": True, "defines_debug": False,
                "defines_dynamic_debug_module": False,
                "direct_macro_flags_parseable": True}
        self.assertEqual(coverage._dev_dbg_class(
            {"CONFIG_DYNAMIC_DEBUG": "n", "CONFIG_DYNAMIC_DEBUG_CORE": "y"},
            base, True),
            "command_line_config_predicts_no_dev_dbg_effective_macros_unverified")
        self.assertEqual(coverage._dev_dbg_class(
            {"CONFIG_DYNAMIC_DEBUG": "n", "CONFIG_DYNAMIC_DEBUG_CORE": "y"},
            base | {"defines_dynamic_debug_module": True}, True),
            "command_line_config_predicts_dynamic_debug_path_unverified")
        self.assertEqual(coverage._dev_dbg_class(
            {"CONFIG_DYNAMIC_DEBUG": "n", "CONFIG_DYNAMIC_DEBUG_CORE": "n"},
            base | {"defines_debug": True}, True),
            "command_line_config_predicts_direct_debug_path_unverified")
        self.assertEqual(coverage._dev_dbg_class(
            {"CONFIG_DYNAMIC_DEBUG": "n", "CONFIG_DYNAMIC_DEBUG_CORE": "y"},
            base, False), "unknown_compiled_source_differs_from_pinned_audit")

    def test_macro_flags_support_split_attached_and_last_definition_wins(self) -> None:
        macros = coverage._parse_macro_flags([
            "-D", "MODULE", "-DDEBUG=1", "-U", "DEBUG", "-UDYNAMIC_DEBUG_MODULE",
            "-DDYNAMIC_DEBUG_MODULE=1", "-UMODULE",
        ])
        self.assertEqual(macros, {
            "MODULE": False, "DEBUG": False, "DYNAMIC_DEBUG_MODULE": True,
        })
        macros = coverage._parse_macro_flags(["-UDEBUG", "-D", "DEBUG=1"])
        self.assertEqual(macros, {"DEBUG": True})

    def test_malformed_macro_flags_make_dev_dbg_state_unknown(self) -> None:
        self.assertIsNone(coverage._parse_macro_flags(["-DMODULE", "-D"]))
        self.assertIsNone(coverage._parse_macro_flags(["-U", "-DDEBUG"]))
        command = {"available": True, "direct_macro_flags_parseable": False}
        self.assertEqual(coverage._dev_dbg_class(
            {"CONFIG_DYNAMIC_DEBUG": "n", "CONFIG_DYNAMIC_DEBUG_CORE": "y"},
            command, True), "unknown_malformed_command_line_define_flags")

    def test_source_blob_check_rejects_missing_or_modified_pinned_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True,
                           capture_output=True)
            sample = root / "sample.c"
            sample.write_text("int sample;\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "sample.c"], check=True,
                           capture_output=True)
            subprocess.run([
                "git", "-C", str(root), "-c", "user.name=Test",
                "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture",
            ], check=True, capture_output=True)
            commit = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            self.assertTrue(coverage._source_files_match_commit(root, commit, ("sample.c",)))
            self.assertFalse(coverage._source_files_match_commit(root, commit, ("missing.h",)))
            sample.write_text("int changed;\n", encoding="utf-8")
            self.assertFalse(coverage._source_files_match_commit(root, commit, ("sample.c",)))

    def test_source_contract_checks_are_not_c_execution(self) -> None:
        source_tree = os.environ.get("S22_AUDIO_LOG_SOURCE_TREE")
        if not source_tree:
            self.skipTest("set S22_AUDIO_LOG_SOURCE_TREE for pinned-source checks")
        result = coverage.audit_source(Path(source_tree))
        self.assertTrue(result["all_checks_pass"], result["checks"])
        self.assertIn("static source-contract", result["interpretation"])

    def test_retained_o_tree_flags_and_current_snapshot(self) -> None:
        source_tree = os.environ.get("S22_AUDIO_LOG_SOURCE_TREE")
        build_tree = os.environ.get("S22_AUDIO_LOG_BUILD_TREE")
        if not source_tree or not build_tree:
            self.skipTest("set source and build tree environment for O-tree audit")
        report = coverage.build_report(metadata(), Path(source_tree), Path(build_tree))
        self.assertTrue(report["build"]["compiled_source_identity"]
                        ["relevant_files_match_pinned_source"])
        self.assertEqual(report["build"]["config"]["CONFIG_SND_SOC_SAMSUNG_ABOX_V4"], "y")
        self.assertEqual(report["build"]["config"]["CONFIG_DYNAMIC_DEBUG"], "n")
        self.assertEqual(report["build"]["config"]["CONFIG_DYNAMIC_DEBUG_CORE"], "y")
        for obj in ("abox.o", "abox_rdma.o", "abox_ipc.o"):
            command = report["build"]["abox_debug_objects"][obj]
            self.assertTrue(command["available"], obj)
            self.assertTrue(command["source_path_matches_expected"], obj)
            self.assertTrue(command["defines_module"], obj)
            self.assertFalse(command["defines_debug"], obj)
            self.assertFalse(command["defines_dynamic_debug_module"], obj)
            self.assertTrue(command["direct_macro_flags_parseable"], obj)
            self.assertTrue(command["forced_includes_present"], obj)
            self.assertEqual(
                command["dev_dbg"],
                "command_line_config_predicts_no_dev_dbg_effective_macros_unverified",
            )
        self.assertEqual(report["current_policy"]["memlog"]
                         ["abox_mem_debug_message_state"],
                         "filtered_debug_level_above_object_limit")
        self.assertEqual(
            report["current_policy"]["producer_state"],
            "memlog_debug_filtered; dev_dbg_no_macro_prediction_unverified",
        )
        self.assertIn("unknown", report["current_policy"]["historical_capture_coverage"])
        historical_report = coverage.build_report(
            metadata("historical_trial", linked=True), Path(source_tree), Path(build_tree)
        )
        self.assertNotIn("current_policy", historical_report)
        self.assertEqual(historical_report["saved_historical_policy"]["scope"],
                         "historical_trial")
        self.assertIn("unknown", historical_report["saved_historical_policy"]
                      ["historical_capture_coverage"])
        self.assertFalse(report["restoration_requirements"]
                         ["serialized_atomic_metadata_update_proven"])
        self.assertEqual(report["restoration_requirements"]
                         ["exact_original_values_required_per_object"],
                         ["enabled", "level"])
        self.assertTrue(report["restoration_requirements"]
                        ["restore_and_readback_required"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
