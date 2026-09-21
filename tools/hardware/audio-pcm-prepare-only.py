#!/usr/bin/env python3
"""Prepare-only ALSA probe for the reviewed RDMA2 playback PCM.

This deliberately has no write/start/mmap/record path.  It is a diagnostic
for open/set-params/prepare and cleanup behavior; it does not submit audio.
The device is fixed to hw:0,2 so a caller cannot accidentally probe another
PCM through this helper.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import sys
from pathlib import Path
from typing import Any


DEVICE = "hw:0,2"
STREAM_PLAYBACK = 0
MODE_NONBLOCK = 0x1
ACCESS_RW_INTERLEAVED = 3
CHANNELS = 2
RATE = 48000
LATENCY_US = 100000


class AlsaBindings:
    def __init__(self) -> None:
        self.lib = ctypes.CDLL("libasound.so.2")
        pcm_p = ctypes.c_void_p
        self.pcm_open = self.lib.snd_pcm_open
        self.pcm_open.argtypes = [ctypes.POINTER(pcm_p), ctypes.c_char_p,
                                  ctypes.c_int, ctypes.c_int]
        self.pcm_open.restype = ctypes.c_int
        self.format_value = self.lib.snd_pcm_format_value
        self.format_value.argtypes = [ctypes.c_char_p]
        self.format_value.restype = ctypes.c_int
        self.access_name = self.lib.snd_pcm_access_name
        self.access_name.argtypes = [ctypes.c_int]
        self.access_name.restype = ctypes.c_char_p
        self.set_params = self.lib.snd_pcm_set_params
        self.set_params.argtypes = [pcm_p, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_uint, ctypes.c_uint, ctypes.c_int,
                                    ctypes.c_uint]
        self.set_params.restype = ctypes.c_int
        self.drop = self.lib.snd_pcm_drop
        self.drop.argtypes = [pcm_p]
        self.drop.restype = ctypes.c_int
        self.hw_free = self.lib.snd_pcm_hw_free
        self.hw_free.argtypes = [pcm_p]
        self.hw_free.restype = ctypes.c_int
        self.close = self.lib.snd_pcm_close
        self.close.argtypes = [pcm_p]
        self.close.restype = ctypes.c_int


def _name(api: Any, access: int) -> str | None:
    value = api.access_name(access)
    if not value:
        return None
    return value.decode("ascii", errors="replace")


def run(api: Any) -> dict[str, Any]:
    """Run the prepare-only lifecycle against a bindings-like API."""
    result: dict[str, Any] = {
        "device": DEVICE,
        "stream": "playback",
        "mode": "O_NONBLOCK",
        "mode_value": MODE_NONBLOCK,
        "format_name": "S16_LE",
        "access_requested": "RW_INTERLEAVED",
        "channels": CHANNELS,
        "rate": RATE,
        "latency_us": LATENCY_US,
        "audio_frames_submitted": 0,
        "write_start_mmap_record_calls": 0,
    }
    pcm = ctypes.c_void_p()
    result["open_rc"] = int(api.pcm_open(ctypes.byref(pcm), DEVICE.encode(),
                                          STREAM_PLAYBACK, MODE_NONBLOCK))
    if result["open_rc"] < 0:
        result["cleanup"] = []
        result["prepared"] = False
        return result
    cleanup: list[dict[str, int]] = []
    try:
        fmt = int(api.format_value(b"S16_LE"))
        result["format_value"] = fmt
        result["access_value"] = ACCESS_RW_INTERLEAVED
        result["access_name"] = _name(api, ACCESS_RW_INTERLEAVED)
        if fmt < 0 or result["access_name"] != "RW_INTERLEAVED":
            result["set_params_rc"] = -22
        else:
            result["set_params_rc"] = int(api.set_params(
                pcm, fmt, ACCESS_RW_INTERLEAVED, CHANNELS, RATE, 0,
                LATENCY_US))
    finally:
        cleanup.append({"operation": "drop", "rc": int(api.drop(pcm))})
        cleanup.append({"operation": "hw_free", "rc": int(api.hw_free(pcm))})
        cleanup.append({"operation": "close", "rc": int(api.close(pcm))})
    result["cleanup"] = cleanup
    result["prepared"] = result["set_params_rc"] == 0
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    if Path('/proc/1/comm').read_text().strip()!='native-guardian':
        raise SystemExit('requires the reviewed native guardian session')
    try:
        result = run(AlsaBindings())
    except OSError as exc:
        result = {"device": DEVICE, "error": str(exc), "prepared": False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("prepared") and all(x['rc']==0 for x in result.get('cleanup',[])) else 1


if __name__ == "__main__":
    raise SystemExit(main())
