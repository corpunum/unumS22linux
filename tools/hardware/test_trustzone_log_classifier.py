#!/usr/bin/env python3
"""Executable, host-only tests for the TrustZone log severity classifier."""
from __future__ import annotations

import unittest

from trustzone_log_classifier import classify_kernel_log


class TrustZoneLogClassifierTests(unittest.TestCase):
    def test_mixed_records_keep_fatal_warning_and_trace_signals_separate(self):
        result = classify_kernel_log(
            "[  10.000] INFO: task tz_kthread_pool:41 blocked for more than 120 seconds.\n"
            "[  10.001] Call trace:\n"
            "[  10.002] BUG: unable to handle kernel NULL pointer dereference\n"
        )

        self.assertEqual(result.assessment, "fatal")
        self.assertTrue(result.has_fatal_indicator)
        self.assertEqual([item.kind for item in result.fatal_indicators], ["bug"])
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
            ("[ 1.0] BUG: unable to handle kernel paging request\n", {"bug"}),
            ("[ 1.0] kernel BUG at drivers/example.c:42!\n", {"bug"}),
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


if __name__ == "__main__":
    unittest.main()
