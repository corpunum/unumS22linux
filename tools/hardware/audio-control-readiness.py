#!/usr/bin/env python3
"""Read-only ALSA control/route metadata collector for the native target.

No PCM node is opened and no mixer control is written.  ``amixer controls``
and no-argument ``tinymix`` are metadata-only listings when available; all
other data comes from named proc/sysfs files.  The mixer XML is parsed from
the pinned source checkout, never from private phone storage.
"""
from __future__ import annotations

import json
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

def read(path: str, limit: int = 65536) -> str | None:
    try:
        with open(path, "rb") as stream:
            return stream.read(limit).decode("utf-8", "replace")
    except OSError:
        return None

def listing(command: list[str]) -> dict[str, object]:
    process = None
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            process = subprocess.Popen(command, stdout=stdout_file, stderr=stderr_file)
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired) as exc:
            if process is not None:
                process.kill()
                process.wait()
            return {"available": False, "error": str(exc)}
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read(262145)
        stderr = stderr_file.read(4097)
    return {"available": process.returncode == 0, "returncode": process.returncode,
            "stdout_truncated":len(stdout)>262144,"stderr_truncated":len(stderr)>4096,
            "stdout": stdout[:262144].decode("utf-8", "replace"),
            "stderr": stderr[:4096].decode("utf-8", "replace")}

def source_routes(path: Path | None) -> dict[str, object]:
    if path is None:
        return {"available": False, "reason": "host source XML not supplied; native collector is repo-independent"}
    root = ET.parse(path).getroot()
    wanted = {"media-handset", "media-speaker", "media-speaker-top",
              "media-speaker-bottom", "media-mic", "media-2nd-mic",
              "media-3rd-mic", "media-dualmic", "dev-multi-mic"}
    paths = {}
    for item in root.findall("path"):
        name = item.get("name")
        if name not in wanted:
            continue
        controls = [{"name": ctl.get("name"), "value": ctl.get("value")}
                    for ctl in item.iter("ctl")]
        children = [child.get("name") for child in item.findall("path")]
        paths[name] = {"controls": controls, "paths": children}
    source_name = str(path)
    return {"available": True, "source": source_name, "paths": paths,
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mixer-xml", type=Path,
                        help="host-only source XML; omit for repo-independent native collection")
    args = parser.parse_args()
    mixer_xml = args.mixer_xml
    if mixer_xml is not None and (mixer_xml.is_symlink() or not mixer_xml.is_file()):
        raise SystemExit(f"mixer XML is not a regular file: {mixer_xml}")
    controls = {}
    for card in range(4):
        card_root = f"/proc/asound/card{card}"
        controls[str(card)] = {
            "id": read(f"{card_root}/id", 1024),
            "oss_mixer": read(f"{card_root}/oss_mixer", 8192),
            "codec": read(f"{card_root}/codec#0", 65536),
        }
    result = {
        "mode": "read-only-alsa-control-metadata",
        "phone_access": False,
        "pcm_nodes_opened": [],
        "mixer_writes": [],
        "alsa": {path: read(path) for path in
                  ("/proc/asound/cards", "/proc/asound/pcm", "/proc/asound/devices")},
        "control_metadata": controls,
        "sysfs_sound": read("/sys/class/sound/controlC0/id", 1024),
        "tools": {
            "amixer": listing([shutil.which("amixer") or "amixer", "-c", "0", "controls"])
                      if shutil.which("amixer") else {"available": False},
            "tinymix": listing([shutil.which("tinymix") or "tinymix"])
                       if shutil.which("tinymix") else {"available": False},
        },
        "source_routes": source_routes(mixer_xml),
        "safety": "Metadata only; do not pass cset, set, or PCM playback/capture arguments.",
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
