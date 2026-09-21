#!/usr/bin/env python3
"""Read-only post-boot ABOX/ALSA readiness collector.

Intended to run inside the native Alpine root after an audio-early recovery
candidate boot. It never opens ALSA/PCM nodes and never writes sysfs, mixer,
driver, power, bind, or reprobe controls. Missing paths are reported rather
than treated as success.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import os
import re

ABOX = Path("/sys/devices/platform/18c50000.abox")
FIRMWARE_ROOT = Path("/proc/1/root/vendor/firmware")
FIRMWARE = {
    "calliope_sram.bin": (165120, "786ae058d4c439c01edf856abb52184a92e8d25e90b1b748e380186bed84796d"),
    "calliope_dram.bin": (2204800, "d2460b85f1e8ceabe6a23e103688932c03ca5aca6da0de5ed95ff78dfd8feccd"),
    "abox_tplg.bin": (1066664, "3690d21b6a7e91242ee423ea655604842969607c318769fe73fbc6db826eb9da"),
    "abox_tplg.conf": (109315, "ddc3874ec797a085c8497a240d47b995fedbcc5c876483c94e2f236ecb79f782"),
}
PLATFORM = (
    "/sys/bus/platform/devices/18c50000.abox",
    "/sys/bus/platform/devices/18c55000.abox-core",
    "/sys/bus/platform/devices/0.abox-tplg",
    "/sys/bus/platform/devices/sound",
)
READ_FILES = (
    "/proc/asound/cards", "/proc/asound/pcm", "/proc/asound/devices",
    "/sys/kernel/debug/asoc/cards", "/sys/kernel/debug/asoc/components",
    "/sys/kernel/debug/devices_deferred",
    "/sys/module/firmware_class/parameters/path",
    "/proc/1/comm",
)

def read(path: str | Path, limit: int = 131072) -> str | None:
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(limit)
    except OSError:
        return None
    return data.rstrip(b"\0\n").decode("utf-8", "replace")

def binding(path: str) -> dict[str, str | None]:
    driver = Path(path) / "driver"
    try:
        driver_name = os.path.basename(os.readlink(driver))
    except OSError:
        driver_name = None
    return {"path": path, "driver": driver_name,
            "uevent": read(Path(path) / "uevent", 16384),
            "runtime_status": read(Path(path) / "power/runtime_status", 1024)}

def firmware() -> dict[str, object]:
    result: dict[str, object] = {"root": str(FIRMWARE_ROOT), "files": {}}
    files: dict[str, object] = {}
    for name, (expected_size, expected_hash) in FIRMWARE.items():
        path = FIRMWARE_ROOT / name
        try:
            data = path.read_bytes()
        except OSError as exc:
            files[name] = {"present": False, "error": str(exc)}
            continue
        actual = hashlib.sha256(data).hexdigest()
        files[name] = {"present": True, "bytes": len(data), "sha256": actual,
                       "expected": {"bytes": expected_size, "sha256": expected_hash},
                       "exact": len(data) == expected_size and actual == expected_hash}
    result["files"] = files
    return result

def log_gate() -> dict[str, object]:
    # Keep this bounded and keyword-focused; do not export a raw kernel log.
    raw = read("/proc/last_kmsg", 262144)
    if raw is None:
        return {"source": "/proc/last_kmsg", "available": False,
                "keywords": ["rainbow", "abox", "firmware", "snd", "asoc", "defer"]}
    pattern = re.compile(r"rainbow|abox|firmware|snd|asoc|defer|tplg", re.I)
    lines = [line for line in raw.splitlines() if pattern.search(line)][-200:]
    return {"source": "/proc/last_kmsg", "available": True,
            "matching_line_count": len(lines), "matching_lines": lines}

def main() -> int:
    sound_entries = []
    try:
        sound_entries = sorted(os.listdir("/sys/class/sound"))
    except OSError:
        pass
    snd_entries = []
    try:
        snd_entries = sorted(os.listdir("/dev/snd"))
    except OSError:
        pass
    result = {
        "mode": "read-only-postboot-audio-readiness",
        "phone_access": False,
        "writes_performed": [],
        "pcm_nodes_opened": [],
        "firmware": firmware(),
        "firmware_search_path": read("/sys/module/firmware_class/parameters/path", 1024),
        "abox": {name: read(ABOX / name, 4096) for name in
                 ("calliope_version", "reset_count", "service", "power/control", "power/runtime_status")},
        "platform_bindings": [binding(path) for path in PLATFORM],
        "alsa": {path: read(path) for path in ("/proc/asound/cards", "/proc/asound/pcm", "/proc/asound/devices")},
        "sound_class_entries": sound_entries,
        "sound_device_entries": snd_entries,
        "debug": {path: read(path) for path in
                  ("/sys/kernel/debug/asoc/cards", "/sys/kernel/debug/asoc/components",
                   "/sys/kernel/debug/devices_deferred")},
        "pid1": read("/proc/1/comm", 1024),
        "log_gate": log_gate(),
        "next_gates": {
            "machine_card": "Require a Rainbow/machine card in /proc/asound/cards.",
            "pcm": "Require non-empty playback/capture entries in /proc/asound/pcm; do not open them in this collector.",
            "controls": "Require corresponding control entries under /sys/class/sound and source-owned mixer route evidence.",
            "speaker_mic": "Physical speaker/microphone operation remains unproven even after kernel PCM registration.",
        },
        "source_expectations": {
            "card_name": "Rainbow-Prince",
            "controls": ["DMIC1", "DMIC2", "DMIC3", "SPEAKER", "RECEIVER", "Sound Wakelock"],
            "dapm_endpoints": ["RECEIVER", "SPEAKER", "DMIC1", "DMIC2", "DMIC3",
                               "BLUETOOTH MIC", "BLUETOOTH SPK", "USB MIC", "USB SPK",
                               "FWD MIC", "FWD SPK"],
            "source": "lineage/android_kernel_samsung_s5e9925/sound/soc/samsung/rainbow_prince.c:1386-1421,1428-1444",
            "firmware_root": "/proc/1/root/vendor/firmware",
            "abox_root": "/sys/devices/platform/18c50000.abox",
        },
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
