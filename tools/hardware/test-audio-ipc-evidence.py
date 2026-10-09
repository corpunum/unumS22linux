#!/usr/bin/env python3
"""Host regressions for the bounded ABOX/RDMA evidence summarizer."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/hardware/audio-ipc-evidence.py"
SPEC = importlib.util.spec_from_file_location("audio_ipc_evidence", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load audio-ipc-evidence.py")
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)

PINNED_SOURCE = os.environ.get("S22_AUDIO_IPC_SOURCE_TREE")
PINNED_COMMIT = "4e5c5ad7d950e4de0688b5663965f2075654b2ad"


def receipt_with_samples(states: dict[str, str] | None = None) -> dict:
    dapm_widgets = {
        widget: {"status": "ok", "value": f"{widget}: {state}  in 0 out 0"}
        for widget, state in (states or {}).items()
    }
    running = {
        "alsa_status": "state: RUNNING\n",
        "observations": {"dapm": {"Rainbow-Prince/18c50000.abox": dapm_widgets}},
    }
    return {
        "diagnostic_kind": "audio-zero-node-20260927",
        "assessment": {
            "dma_progress": {"hw_ptr_advance": 0},
            "source_path_assessment": {
                "running_sample_count": 2,
                "pointer_ipc_stage": "pointer_ipc_not_localized_insufficient_or_gapped_samples",
            },
        },
        "result": {
            "progress_samples": [
                running,
                running,
                {"alsa_status": "state: CLOSED\n", "observations": {"dapm": {}}},
            ],
        },
    }


class AudioIpcEvidenceTests(unittest.TestCase):
    def test_async_queue_and_sender_markers_do_not_become_firmware_ack(self) -> None:
        kernel = """
            18c51200.abox-rdma: abox_rdma_trigger(0)
            abox_rdma_trigger(0)
            abox_schedule_ipc(2, 88, 1, 0)
            abox_ipc_send(2, 2, 17, 64, 0)
        """
        report = evidence.build_report(receipt_with_samples(), kernel)
        observed = report["evidence"]
        semantics = report["semantics"]
        self.assertEqual(observed["trigger_api_entries_rdma2_device_prefixed"], 1)
        self.assertEqual(observed["trigger_api_entries_unattributed"], 1)
        self.assertEqual(observed["async_schedule_entries_atomic_nonsync"], 1)
        self.assertEqual(observed["playback_task2_trigger_sender_entries"], 1)
        self.assertEqual(observed["rdma_pointer_handler_entries_rdma2_device_prefixed"], 0)
        self.assertFalse(semantics["queue_or_sender_success_is_firmware_completion"])
        self.assertIn("no IPC payload/task/channel attribution", semantics["async_schedule_entry"])
        self.assertIn("not send return", semantics["playback_task2_trigger_sender_entry"])
        self.assertIn("not attributed to RDMA2", report["limits"]["trigger_api_attribution"])

    def test_firmware_callback_marker_is_separate_from_alsa_pointer_computation(self) -> None:
        kernel = """
            18c51200.abox-rdma: abox_rdma_ipc_handler(18)
            abox_rdma_ipc_handler(18)
            18c51200.abox-rdma: abox_rdma_pointer: pointer=00000000
            abox_rdma_pointer: pointer=00000000
        """
        report = evidence.build_report(receipt_with_samples(), kernel)
        observed = report["evidence"]
        self.assertEqual(observed["rdma_pointer_handler_entries_rdma2_device_prefixed"], 1)
        self.assertEqual(observed["rdma_pointer_handler_entries_unattributed"], 1)
        self.assertEqual(observed["alsa_pointer_function_logs_rdma2_device_prefixed"], 1)
        self.assertEqual(observed["alsa_pointer_function_logs_unattributed"], 1)
        self.assertFalse(report["semantics"]["pointer_callback_is_dma_progress_proof"])
        self.assertIn("not attributed to a device/channel",
                      report["limits"]["pointer_handler_attribution"])

    def test_dapm_trace_counts_only_the_named_software_widget_transitions(self) -> None:
        trace = """
            asoc-10 [000] 1.0: snd_soc_dapm_widget_power: widget=SPUS OUT2-SIFS0 val=1
            asoc-10 [000] 1.1: snd_soc_dapm_widget_power: widget=SIFS0 val=0
            asoc-10 [000] 1.2: snd_soc_dapm_widget_power: widget=SIFS0 OUT val=1
            asoc-10 [000] 1.3: snd_soc_dapm_widget_power: widget=unrelated val=1
        """
        report = evidence.build_report(receipt_with_samples(), "", trace)
        observed = report["evidence"]["dapm_trace_events"]
        self.assertEqual(observed["ABOX SPUS OUT2-SIFS0"], {"on_events": 1, "off_events": 0})
        self.assertEqual(observed["ABOX SIFS0"], {"on_events": 0, "off_events": 1})
        self.assertEqual(observed["ABOX SIFS0 OUT"], {"on_events": 1, "off_events": 0})
        self.assertFalse(report["semantics"]["dapm_state_is_physical_clock_or_audio_proof"])

    def test_receipt_pairs_dapm_widgets_only_with_running_samples(self) -> None:
        widgets = {
            "ABOX SPUS OUT2-SIFS0": "Off",
            "ABOX SIFS0": "Off",
            "ABOX SIFS0 OUT": "Off",
        }
        report = evidence.build_report(receipt_with_samples(widgets), "")
        states = report["evidence"]["receipt_dapm_samples"]
        self.assertEqual(states["ABOX SIFS0"]["states"], {"off": 2})
        self.assertEqual(states["ABOX SIFS0"]["running_samples_observed"], 2)
        self.assertEqual(states["ABOX UAIF1 SPK"]["running_samples_observed"], 0)
        self.assertEqual(report["limits"]["dapm_sample_pairing"].split(";")[0],
                         "only per-sample DAPM values paired with alsa_status state: RUNNING are counted")
        self.assertEqual(report["evidence"]["running_samples"], 2)

    def test_absent_markers_stay_unknown_not_negative_device_evidence(self) -> None:
        report = evidence.build_report(receipt_with_samples(), "")
        self.assertEqual(report["evidence"]["rdma_pointer_handler_entries_rdma2_device_prefixed"], 0)
        self.assertFalse(report["semantics"]["absence_of_log_marker_is_negative_device_evidence"])
        self.assertEqual(report["limits"]["coverage"].split(";")[0],
                         "counts are observations only")

    @unittest.skipUnless(PINNED_SOURCE, "set S22_AUDIO_IPC_SOURCE_TREE for pinned-source checks")
    def test_pinned_source_contracts_only(self) -> None:
        """Static source substring contracts; this does not execute kernel C."""
        source = Path(PINNED_SOURCE)
        head = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(head, PINNED_COMMIT)

        rdma = (source / "sound/soc/samsung/abox/abox_rdma.c").read_text()
        abox = (source / "sound/soc/samsung/abox/abox.c").read_text()
        ipc = (source / "sound/soc/samsung/abox/abox_ipc.c").read_text()
        msg = (source / "sound/soc/samsung/abox/abox_msg.c").read_text()
        dapm = (source / "sound/soc/soc-dapm.c").read_text()
        makefile = (source / "sound/soc/samsung/abox/Makefile").read_text()
        graph = (source / "sound/soc/samsung/abox/abox_cmpnt.c").read_text()
        ipc_header = (source / "include/sound/samsung/abox_ipc.h").read_text()

        self.assertIn("data->enabled = start;", rdma)
        self.assertIn("abox_rdma_request_ipc(data, &msg, atomic, 0)", rdma)
        self.assertIn("ret = abox_ipc_queue_put(data, dev, hw_irq, msg, size);", abox)
        self.assertIn("queue_work(data->ipc_workqueue, &data->ipc_work);", abox)
        self.assertIn("abox_ipc_send(dev, msg, size, NULL, 0)", abox)
        self.assertIn('"%s(%d, %zu, %d, %d)\\n"', abox)
        self.assertIn('"%s(%d, %d, %d, %zu, %zu)\\n"', ipc)
        self.assertIn('abox_dbg(dev, "%s(%d)\\n", __func__, pcmtask_msg->msgtype);', rdma)
        self.assertIn('abox_dbg(dev, "%s: pointer=%08zx\\n", __func__, pointer);', rdma)
        self.assertIn("q_cmd->elem[q_cmd->idx_e] = *cmd;", msg)
        self.assertIn("case PCM_PLTDAI_POINTER:", rdma)
        self.assertIn("data->pointer = pcmtask_msg->param.pointer;", rdma)
        self.assertIn("snd_pcm_period_elapsed(data->substream);", rdma)
        self.assertIn("data->ack_enabled = !!pcmtask_msg->param.trigger;", rdma)
        self.assertIn("trace_snd_soc_dapm_widget_power(w, power);", dapm)
        self.assertIn("CONFIG_SND_SOC_SAMSUNG_ABOX_V4", makefile)
        self.assertIn("abox_soc_4.o abox_soc.o abox_cmpnt.o", makefile)
        self.assertIn('"RESERVED", "SIFS0", "SIFS1"', graph)
        self.assertIn('{"SPUS OUT2-SIFS0", "SIFS0", "SPUS OUT2"}', graph)
        self.assertIn('{"SIFS0 PGA", NULL, "SIFS0"}', graph)
        self.assertIn('{"STMIX", NULL, "SIFS0 PGA"}', graph)
        self.assertIn('{"SIFS0 OUT", "Switch", "STMIX"}', graph)
        self.assertIn('{"UAIF1 SPK", "SIFS0", "SIFS0 OUT"}', graph)
        self.assertIn('{"UAIF1 PLA", "UAIF1 Switch", "UAIF1 SPK"}', graph)
        self.assertIn("PCM_PLTDAI_TRIGGER", ipc_header)
        self.assertIn("PCM_PLTDAI_POINTER", ipc_header)


if __name__ == "__main__":
    unittest.main(verbosity=2)
