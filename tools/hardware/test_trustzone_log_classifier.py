#!/usr/bin/env python3
"""Executable, host-only tests for the TrustZone log severity classifier."""
from __future__ import annotations

import unittest
import importlib.util
from pathlib import Path
import sys

_SOURCE = Path(__file__).with_name("trustzone_log_classifier.py")
_SPEC = importlib.util.spec_from_file_location("trustzone_log_classifier", _SOURCE)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"could not load classifier at {_SOURCE}")
classifier = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = classifier
_SPEC.loader.exec_module(classifier)

PINNED_KERNEL_SOURCE_COMMIT = classifier.PINNED_KERNEL_SOURCE_COMMIT
classify_kernel_log = classifier.classify_kernel_log
classify_source_wait_stacks = classifier.classify_source_wait_stacks


class TrustZoneLogClassifierTests(unittest.TestCase):
    def test_mixed_records_keep_fatal_warning_and_trace_signals_separate(self):
        result = classify_kernel_log(
            "[  10.000] INFO: task tz_kthread_pool:41 blocked for more than 120 seconds.\n"
            "[  10.001] Call trace:\n"
            "[  10.002] BUG: unable to handle kernel NULL pointer dereference\n"
        )

        self.assertEqual(result.assessment, "fatal")
        self.assertTrue(result.has_fatal_indicator)
        self.assertEqual(
            [item.kind for item in result.fatal_indicators],
            ["bug", "unable_to_handle_kernel"],
        )
        self.assertEqual(len(result.hung_task_warnings), 1)
        self.assertEqual(result.hung_task_warnings[0].task_name, "tz_kthread_pool")
        self.assertEqual(result.hung_task_warnings[0].pid, 41)
        self.assertEqual(result.hung_task_warnings[0].blocked_seconds, 120)
        self.assertEqual(result.call_trace_lines, (2,))
        self.assertTrue(result.material_liveness_unresolved)
        self.assertTrue(result.coverage_complete)

    def test_bare_call_trace_is_trace_only_not_a_fault(self):
        result = classify_kernel_log(
            "[  20.000] Call trace:\n"
            "[  20.001]  dump_backtrace+0x0/0x1a0\n"
        )

        self.assertEqual(result.assessment, "trace_only")
        self.assertFalse(result.has_fatal_indicator)
        self.assertEqual(result.hung_task_warnings, ())
        self.assertEqual(result.call_trace_lines, (1,))
        self.assertFalse(result.material_liveness_unresolved)

    def test_recognized_fatal_indicators(self):
        cases = (
            ("[ 1.0] Kernel panic - not syncing: fatal exception\n", {"kernel_panic"}),
            ("[ 1.0] Internal error: Oops: 96000004 [#1] PREEMPT SMP\n", {"oops"}),
            ("[ 1.0] BUG: unable to handle kernel paging request\n",
             {"bug", "unable_to_handle_kernel"}),
            ("[ 1.0] kernel BUG at drivers/example.c:42!\n", {"bug"}),
            ("[ 1.0] Unable to handle kernel NULL pointer dereference\n",
             {"unable_to_handle_kernel"}),
            ("[ 1.0] general protection fault, probably for address 0x0\n",
             {"general_protection_fault"}),
            ("[ 1.0] Out of memory: Killed process 123 (example)\n", {"out_of_memory"}),
            ("[ 1.0] oom-kill:constraint=CONSTRAINT_NONE,nodemask=(null)\n", {"oom_kill"}),
            ("[ 1.0] watchdog: BUG: soft lockup - CPU#0 stuck for 22s!\n",
             {"bug", "soft_lockup"}),
            ("[ 1.0] watchdog: Watchdog detected hard LOCKUP on cpu 0\n", {"hard_lockup"}),
            ("[ 1.0] watchdog: soft lockup - CPU#1 stuck for 22s!\n", {"soft_lockup"}),
        )
        for log, expected_kinds in cases:
            with self.subTest(expected_kinds=expected_kinds):
                result = classify_kernel_log(log)
                self.assertEqual(result.assessment, "fatal")
                self.assertEqual(
                    {item.kind for item in result.fatal_indicators}, expected_kinds
                )

    def test_fatal_lines_keep_call_trace_as_a_separate_signal(self):
        result = classify_kernel_log(
            "[ 1.0] Out of memory: Killed process 123 (example)\n"
            "[ 1.1] Call trace:\n"
        )

        self.assertEqual(result.assessment, "fatal")
        self.assertEqual([item.kind for item in result.fatal_indicators], ["out_of_memory"])
        self.assertEqual(result.call_trace_lines, (2,))

    def test_known_and_unknown_thread_names_do_not_downgrade_hung_tasks(self):
        known = classify_kernel_log(
            "[ 2.0] INFO: task tz_iwlog_thread:390 blocked for more than 120 seconds.\n"
        )
        unknown = classify_kernel_log(
            "[ 2.0] INFO: task unrelated_worker:391 blocked for more than 120 seconds.\n"
        )

        for result in (known, unknown):
            self.assertEqual(result.assessment, "hung_task_warning")
            self.assertEqual(len(result.hung_task_warnings), 1)
            self.assertTrue(result.material_liveness_unresolved)
        self.assertEqual(known.hung_task_warnings[0].task_name, "tz_iwlog_thread")
        self.assertEqual(unknown.hung_task_warnings[0].task_name, "unrelated_worker")

    def test_progress_evidence_must_be_explicit_and_does_not_hide_warning(self):
        warning = "[ 3.0] INFO: task tz_iwlog_thread:390 blocked for more than 120 seconds.\n"
        without_progress = classify_kernel_log(warning)
        with_progress = classify_kernel_log(warning, relevant_progress_evidence=True)

        self.assertTrue(without_progress.material_liveness_unresolved)
        self.assertFalse(with_progress.material_liveness_unresolved)
        self.assertEqual(with_progress.assessment, "hung_task_warning")
        self.assertEqual(with_progress.hung_task_warnings, without_progress.hung_task_warnings)
        with self.assertRaises(TypeError):
            classify_kernel_log(warning, relevant_progress_evidence="yes")

    def test_malformed_and_truncated_logs_fail_closed(self):
        partial = (
            b"[ 4.0] INFO: task unknown_worker:12 blocked for more than 120 seconds.\n"
            b"[ 5.0"
            b"\xff"
        )
        result = classify_kernel_log(partial)

        self.assertEqual(result.assessment, "hung_task_warning")
        self.assertEqual(len(result.hung_task_warnings), 1)
        self.assertTrue(result.material_liveness_unresolved)
        self.assertFalse(result.coverage_complete)
        self.assertTrue(result.encoding_errors)
        self.assertTrue(result.unterminated_final_line)
        self.assertEqual(result.malformed_line_numbers, (2,))

        missing = classify_kernel_log(None)
        self.assertEqual(missing.assessment, "incomplete")
        self.assertFalse(missing.input_available)
        self.assertFalse(missing.coverage_complete)

        capped = classify_kernel_log("[ 6.0] ordinary kernel message\n", capture_complete=False)
        self.assertEqual(capped.assessment, "incomplete")
        self.assertFalse(capped.coverage_complete)

    def test_unterminated_final_line_is_not_claimed_as_complete(self):
        result = classify_kernel_log("[ 7.0] ordinary kernel message")

        self.assertTrue(result.unterminated_final_line)
        self.assertFalse(result.coverage_complete)
        self.assertEqual(result.assessment, "incomplete")

    def test_exact_source_wait_stack_is_explained_but_warning_and_no_progress_remain(self):
        log = (
            "[ 10.0] INFO: task tz_worker_threa:41 blocked for more than 120 seconds.\n"
            "[ 10.1] Call trace:\n"
            "[ 10.2] __switch_to+0x120/0x1d0\n"
            "[ 10.3] __schedule+0x390/0x7a0\n"
            "[ 10.4] schedule+0x70/0x110\n"
            "[ 10.5] tz_worker_handler+0x20/0x40\n"
            "[ 10.6] smpboot_thread_fn+0x1a0/0x260\n"
            "[ 10.7] kthread+0x110/0x140\n"
            "[ 10.8] ret_from_fork+0x10/0x20\n"
        )
        warning = classify_kernel_log(log, capture_complete=False)
        review = classify_source_wait_stacks(log)

        self.assertEqual(warning.assessment, "hung_task_warning")
        self.assertEqual(len(warning.hung_task_warnings), 1)
        self.assertTrue(warning.material_liveness_unresolved)
        self.assertEqual(review["source_commit"], PINNED_KERNEL_SOURCE_COMMIT)
        self.assertEqual(review["matched_count"], 1)
        self.assertEqual(review["unmatched_count"], 0)
        self.assertFalse(review["progress_measured"])

    def test_thread_name_without_exact_source_wait_stack_remains_unmatched(self):
        log = (
            "[ 11.0] INFO: task tz_worker_threa:41 blocked for more than 120 seconds.\n"
            "[ 11.1] Call trace:\n"
            "[ 11.2] schedule+0x70/0x110\n"
            "[ 11.3] unrelated_worker+0x20/0x40\n"
        )
        review = classify_source_wait_stacks(log)
        self.assertEqual(review["warning_count"], 1)
        self.assertEqual(review["matched_count"], 0)
        self.assertEqual(review["unmatched_count"], 1)
        self.assertEqual(review["unmatched_task_names"], ["tz_worker_threa"])

    def test_symbols_from_separate_traces_cannot_complete_one_wait_stack(self):
        # This reproduces the reviewer-found bypass: the warning's own trace
        # is partial, while a later unrelated trace supplies its missing
        # symbols before another hung-task warning appears.
        log = (
            "[ 12.0] INFO: task tz_worker_threa:41 blocked for more than 120 seconds.\n"
            "[ 12.1] Call trace:\n"
            "[ 12.2] schedule+0x70/0x110\n"
            "[ 12.3] tz_worker_handler+0x20/0x40\n"
            "[ 12.4] Call trace:\n"
            "[ 12.5] __schedule+0x390/0x7a0\n"
            "[ 12.6] smpboot_thread_fn+0x1a0/0x260\n"
            "[ 12.7] kthread+0x110/0x140\n"
            "[ 12.8] ret_from_fork+0x10/0x20\n"
        )

        review = classify_source_wait_stacks(log)

        self.assertEqual(review["warning_count"], 1)
        self.assertEqual(review["matched_count"], 0)
        self.assertEqual(review["unmatched_count"], 1)
        self.assertEqual(review["unmatched_task_names"], ["tz_worker_threa"])

    def test_later_warning_stack_cannot_complete_earlier_warning(self):
        log = (
            "[ 13.0] INFO: task tz_worker_threa:41 blocked for more than 120 seconds.\n"
            "[ 13.1] Call trace:\n"
            "[ 13.2] schedule+0x70/0x110\n"
            "[ 14.0] INFO: task unrelated_worker:42 blocked for more than 120 seconds.\n"
            "[ 14.1] Call trace:\n"
            "[ 14.2] __schedule+0x390/0x7a0\n"
            "[ 14.3] tz_worker_handler+0x20/0x40\n"
            "[ 14.4] smpboot_thread_fn+0x1a0/0x260\n"
            "[ 14.5] kthread+0x110/0x140\n"
            "[ 14.6] ret_from_fork+0x10/0x20\n"
        )

        review = classify_source_wait_stacks(log)

        self.assertEqual(review["warning_count"], 2)
        self.assertEqual(review["matched_count"], 0)
        self.assertEqual(review["unmatched_count"], 2)
        self.assertEqual(review["unmatched_task_names"],
                         ["tz_worker_threa", "unrelated_worker"])


if __name__ == "__main__":
    unittest.main()
