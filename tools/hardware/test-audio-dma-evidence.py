#!/usr/bin/env python3
"""Host-only regressions for audio-dma-evidence.py; no device access."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("audio-dma-evidence.py")
SPEC = importlib.util.spec_from_file_location("audio_dma_evidence", SCRIPT)
assert SPEC and SPEC.loader
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)


def sample(sequence: int, *, hw_ptr: int = 0, status_raw: int = 0,
           clock_domain: str = "CLOCK_MONOTONIC", sync_complete: bool = True,
           alsa_ok: bool = True, dma_ok: bool = True, registers_match: bool = True):
    window_start = 10_000_000 + sequence * 1_000_000
    window_end = window_start + 100_000
    regs = {"1200": 0, "1230": status_raw, "1238": 0}
    observed_regs = dict(regs)
    status_text = f"state: RUNNING\nhw_ptr: {hw_ptr}\nappl_ptr: 8192"
    if not registers_match:
        observed_regs["1230"] = status_raw ^ 1
    return {
        "trial_id": "audio-test-window",
        "sequence": sequence,
        "sample_monotonic_ns": window_start + 50_000,
        "alsa_status": status_text,
        "runtime_status": "active", "cache_only": "N", "service": "1",
        "synchronization": {
            "clock_domain": clock_domain,
            "all_observations_timed": sync_complete,
            "observation_count": 5,
            "timed_observation_count": 5 if sync_complete else 4,
            "observation_window_start_monotonic_ns": window_start,
            "observation_window_end_monotonic_ns": window_end,
        },
        "observations": {
            "alsa_status": {
                "status": "ok" if alsa_ok else "not_sampled",
                "start_monotonic_ns": window_start + 10_000,
                "end_monotonic_ns": window_start + 20_000,
                "value": status_text,
            },
            "rdma2_registers": {
                "status": "ok" if dma_ok else "not_sampled",
                "start_monotonic_ns": window_start + 80_000,
                "end_monotonic_ns": window_start + 90_000,
                "registers": observed_regs,
            },
            "abox_runtime_status": {
                "status": "ok", "value": "active",
                "start_monotonic_ns": window_start + 21_000,
                "end_monotonic_ns": window_start + 22_000,
            },
            "abox_cache_only": {
                "status": "ok", "value": "N",
                "start_monotonic_ns": window_start + 23_000,
                "end_monotonic_ns": window_start + 24_000,
            },
            "abox_service": {
                "status": "ok", "value": "1",
                "start_monotonic_ns": window_start + 25_000,
                "end_monotonic_ns": window_start + 26_000,
            },
        },
        "alsa_counters": {"state": "RUNNING", "hw_ptr": hw_ptr},
        "registers": regs,
        "rdma2_ctrl": {"raw": 0, "enable": False},
        "rdma2_status": {
            "raw": status_raw, "progress": bool(status_raw & 0x80000000),
            "rbuf_offset": (status_raw >> 20) & 0xff,
            "rbuf_count": status_raw & 0xfffff,
        },
        "rdma2_status_add": {"raw": 0, "current_address": 0},
    }


def receipt(*samples):
    return {"result": {"progress_samples": list(samples)}}


def coverage_report(state: str):
    return {
        "schema": "audio-log-coverage/v1",
        "current_policy": {"scope": "current", "memlog": {
            "abox_mem_debug_message_state": state,
        }},
    }


class AudioDmaEvidenceTests(unittest.TestCase):
    def test_same_sample_timed_reads_pair_and_flat_sampled_values_are_not_failure_proof(self):
        result = evidence.build_report(receipt(sample(0), sample(1)), "")
        dma = result["sampled_dma"]
        self.assertEqual(dma["running_sample_count"], 2)
        self.assertEqual(dma["same_sample_alsa_rdma_pair_count"], 2)
        self.assertEqual(result["trial_id"], "audio-test-window")
        self.assertTrue(result["sample_trial_ids_consistent"])
        self.assertEqual(dma["sampled_values"]["hw_ptr"], [0])
        self.assertEqual(dma["sampled_values"]["status_progress"], [False])
        self.assertEqual(dma["rdma2_registers_vs_hw_ptr"]["rdma"]["state"],
                         "no_change_between_adjacent_samples")
        self.assertEqual(dma["rdma2_registers_vs_hw_ptr"]["alsa_hw_ptr"]["state"],
                         "no_change_between_adjacent_samples")
        self.assertFalse(result["semantics"]["stationary_sampled_registers_prove_dsp_failure"])

    def test_moving_register_and_hw_pointer_are_sampled_motion_not_callback_proof(self):
        result = evidence.build_report(
            receipt(sample(0), sample(1, hw_ptr=512, status_raw=0x80000002)), "")
        comparison = result["sampled_dma"]["rdma2_registers_vs_hw_ptr"]
        self.assertEqual(comparison["rdma"]["state"],
                         "movement_observed_between_adjacent_samples")
        self.assertEqual(comparison["alsa_hw_ptr"]["state"],
                         "movement_observed_between_adjacent_samples")
        self.assertFalse(result["semantics"]["pointer_handler_marker_is_dma_progress_proof"])

    def test_missing_or_unaligned_running_observations_stay_unknown(self):
        for bad in (
            sample(0, clock_domain="unknown"),
            sample(0, sync_complete=False),
            sample(0, alsa_ok=False),
            sample(0, dma_ok=False),
            sample(0, registers_match=False),
        ):
            with self.subTest(sync=bad["synchronization"]):
                result = evidence.build_report(receipt(bad), "")
                dma = result["sampled_dma"]
                self.assertEqual(dma["running_sample_pair_gap_count"], 1)
                self.assertEqual(dma["rdma2_registers_vs_hw_ptr"]["rdma"]["state"],
                                 "unknown_insufficient_adjacent_pairs")

    def test_cross_trial_or_unidentified_samples_are_not_joined(self):
        first = sample(0)
        second = sample(1)
        second["trial_id"] = "different-trial"
        result = evidence.build_report(receipt(first, second), "")
        self.assertFalse(result["sample_trial_ids_consistent"])
        self.assertEqual(result["sampled_dma"]["same_sample_alsa_rdma_pair_count"], 0)
        self.assertEqual(result["sampled_dma"]["pairing_completeness"],
                         "unknown_no_valid_running_pairs")
        del second["trial_id"]
        result = evidence.build_report(receipt(first, second), "")
        self.assertFalse(result["sample_trial_ids_consistent"])
        self.assertEqual(result["sampled_dma"]["same_sample_alsa_rdma_pair_count"], 0)

    def test_current_filtered_policy_and_missing_markers_do_not_prove_historical_absence(self):
        result = evidence.build_report(
            receipt(sample(0), sample(1)), "",
            coverage_report("filtered_debug_level_above_object_limit"),
        )
        marker = result["aggregate_ipc_markers"]
        self.assertEqual(marker["counts"]["playback_task2_trigger_sender_entries"], 0)
        self.assertEqual(marker["capture_coverage"]["current_abox_mem_debug_state"],
                         "filtered_debug_level_above_object_limit")
        self.assertEqual(marker["capture_coverage"]["historical_capture_coverage"],
                         "unknown_current_policy_does_not_prove_trial_capture")
        self.assertEqual(marker["capture_coverage"]["missing_marker_interpretation"],
                         "unknown_not_negative_evidence")
        self.assertFalse(result["semantics"]["missing_marker_proves_boundary_not_reached"])
        self.assertFalse(result["semantics"]["current_filtered_policy_proves_historical_absence"])

    def test_present_trigger_api_marker_is_not_sender_or_firmware_completion(self):
        marker = "[ 10.050] 18c51200.abox-rdma: abox_rdma_trigger(1)\n"
        result = evidence.build_report(receipt(sample(0), sample(1)), marker)
        counts = result["aggregate_ipc_markers"]["counts"]
        self.assertEqual(counts["trigger_api_entries_rdma2_device_prefixed"], 1)
        self.assertEqual(counts["playback_task2_trigger_sender_entries"], 0)
        self.assertFalse(result["semantics"]["queue_or_sender_marker_is_firmware_completion"])
        self.assertFalse(result["aggregate_ipc_markers"]["time_joined_to_each_running_sample"])

    def test_output_contains_no_raw_marker_or_receipt_payload(self):
        raw_marker = "PRIVATE_RAW_MARKER_PAYLOAD"
        result = evidence.build_report(receipt(sample(0), sample(1)), raw_marker)
        serialized = json.dumps(result)
        self.assertNotIn(raw_marker, serialized)
        self.assertNotIn("appl_ptr", serialized)
        self.assertNotIn("hw_ptr: 0", serialized)


if __name__ == "__main__":
    unittest.main(verbosity=2)
