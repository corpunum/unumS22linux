#!/usr/bin/env python3
"""Mock-only tests for audio-pcm-prepare-only.py; no ALSA device is opened."""
from __future__ import annotations

import importlib.util
import ctypes
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("pcm_prepare", HERE / "audio-pcm-prepare-only.py")
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class FakeAlsa:
    def __init__(self, open_rc=0, params_rc=0, access_name=b"RW_INTERLEAVED"):
        self.open_rc = open_rc
        self.params_rc = params_rc
        self.access = access_name
        self.calls = []

    def pcm_open(self, pointer, device, stream, mode):
        self.calls.append(("open", device, stream, mode))
        pointer._obj.value = 1234
        return self.open_rc

    def format_value(self, name):
        self.calls.append(("format_value", name))
        return 2

    def access_name(self, access):
        self.calls.append(("access_name", access))
        return self.access

    def set_params(self, *args):
        self.calls.append(("set_params", args[1:]))
        return self.params_rc

    def drop(self, pcm):
        self.calls.append(("drop", pcm.value))
        return 0

    def hw_free(self, pcm):
        self.calls.append(("hw_free", pcm.value))
        return 0

    def close(self, pcm):
        self.calls.append(("close", pcm.value))
        return 0


class PrepareOnlyTest(unittest.TestCase):
    def test_success_prepares_and_always_cleans_up_without_io(self):
        api = FakeAlsa()
        result = module.run(api)
        self.assertTrue(result["prepared"])
        self.assertEqual(result["audio_frames_submitted"], 0)
        self.assertEqual([call[0] for call in api.calls],
                         ["open", "format_value", "access_name", "set_params",
                          "drop", "hw_free", "close"])
        self.assertEqual(api.calls[0][1:], (b"hw:0,2", 0, 1))

    def test_prepare_error_still_drops_frees_and_closes(self):
        api = FakeAlsa(params_rc=-22)
        result = module.run(api)
        self.assertFalse(result["prepared"])
        self.assertEqual([call[0] for call in api.calls][-3:],
                         ["drop", "hw_free", "close"])

    def test_open_error_does_not_cleanup_unopened_handle(self):
        api = FakeAlsa(open_rc=-19)
        result = module.run(api)
        self.assertFalse(result["prepared"])
        self.assertEqual([call[0] for call in api.calls], ["open"])

    def test_access_mismatch_fails_before_set_params(self):
        api = FakeAlsa(access_name=b"MMAP_INTERLEAVED")
        result = module.run(api)
        self.assertFalse(result["prepared"])
        self.assertNotIn("set_params", [call[0] for call in api.calls])
        self.assertEqual([call[0] for call in api.calls][-3:],
                         ["drop", "hw_free", "close"])

    def test_helper_has_no_audio_submission_or_capture_symbols(self):
        source = (HERE / "audio-pcm-prepare-only.py").read_text()
        for forbidden in ("snd_pcm_start", "snd_pcm_write", "snd_pcm_mmap",
                          "snd_pcm_read", "snd_pcm_record"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
