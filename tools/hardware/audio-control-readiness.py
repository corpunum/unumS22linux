#!/usr/bin/env python3
"""Passively collect bounded local ALSA metadata for the native target.

The default reads only named local procfs/sysfs files. It does not discover or
invoke mixer utilities. ``--list-controls`` is an explicit opt-in to the two
fixed metadata-only commands below; those may open ALSA control interfaces,
but never pass mixer-write or PCM arguments. Optional mixer XML is read only
from the explicitly supplied local source path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import shutil
import signal
import stat
import subprocess
import time
import xml.etree.ElementTree as ET


DEFAULT_FILE_READ_BYTES = 65536
MAX_FILE_READ_BYTES = 1024 * 1024
MAX_MIXER_XML_BYTES = 256 * 1024

MAX_LISTING_STDOUT_BYTES = 256 * 1024
MAX_LISTING_STDERR_BYTES = 4 * 1024
DEFAULT_LISTING_TIMEOUT_SECONDS = 5.0
MAX_LISTING_TIMEOUT_SECONDS = 30.0
DEFAULT_LISTING_CLEANUP_SECONDS = 0.5
MAX_LISTING_CLEANUP_SECONDS = 2.0
LISTING_READ_CHUNK_BYTES = 8192


def _error_record(exc: BaseException, *, error_type: str | None = None) -> dict[str, str]:
    return {
        "type": error_type or type(exc).__name__,
        "message": str(exc),
    }


def _validate_limit(limit: int, maximum: int, label: str) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0 or limit > maximum:
        raise ValueError(f"{label} must be an integer from 0 through {maximum}")


def _finite_number(value: object) -> bool:
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def _read_limited_bytes(path: str | Path, limit: int) -> tuple[bytes | None, dict[str, object]]:
    """Read at most limit+1 bytes from one regular file to detect truncation."""
    _validate_limit(limit, MAX_FILE_READ_BYTES, "read limit")
    path_text = str(path)
    record: dict[str, object] = {
        "available": False,
        "bytes_read": 0,
        "limit_bytes": limit,
        "truncated": False,
        "error": None,
    }
    fd: int | None = None
    try:
        # Keep the old explicit symlink refusal for the optional source input.
        # O_NOFOLLOW also closes the check/open race on the final component.
        try:
            if Path(path).is_symlink():
                record["error"] = {"type": "SymlinkRefused", "message": "symbolic link input refused"}
                return None, record
        except OSError as exc:
            record["error"] = _error_record(exc)
            return None, record

        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path_text, flags)
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            record["error"] = {
                "type": "NotRegularFile",
                "message": "only regular files are read",
            }
            return None, record
        with os.fdopen(fd, "rb", closefd=True) as stream:
            fd = None
            data = stream.read(limit + 1)
        truncated = len(data) > limit
        record.update({
            "available": True,
            "bytes_read": len(data),
            "truncated": truncated,
        })
        return data[:limit], record
    except OSError as exc:
        record["error"] = _error_record(exc)
        return None, record
    finally:
        if fd is not None:
            os.close(fd)


def read(path: str, limit: int = DEFAULT_FILE_READ_BYTES) -> dict[str, object]:
    """Return a bounded text record with explicit truncation and I/O status."""
    data, record = _read_limited_bytes(path, limit)
    record["value"] = data.decode("utf-8", "replace") if data is not None else None
    return record


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        # The process-group operation can be unavailable under a restrictive
        # host policy. Still ensure the direct child receives a bounded kill.
        try:
            process.kill()
        except OSError:
            pass


def listing(
    command: list[str],
    *,
    timeout: float | None = None,
    stdout_limit: int = MAX_LISTING_STDOUT_BYTES,
    stderr_limit: int = MAX_LISTING_STDERR_BYTES,
    cleanup_timeout: float = DEFAULT_LISTING_CLEANUP_SECONDS,
) -> dict[str, object]:
    """Run one fixed metadata command with bounded memory, time, and lifecycle.

    Output is drained from pipes so a full pipe cannot deadlock the command.
    Only the configured prefixes are retained in memory; excess bytes are
    discarded. No temporary output files are created.
    """
    if timeout is None:
        timeout = DEFAULT_LISTING_TIMEOUT_SECONDS
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not _finite_number(timeout) \
            or timeout <= 0 \
            or timeout > MAX_LISTING_TIMEOUT_SECONDS:
        raise ValueError(f"timeout must be greater than 0 and at most {MAX_LISTING_TIMEOUT_SECONDS} seconds")
    if not isinstance(cleanup_timeout, (int, float)) or isinstance(cleanup_timeout, bool) \
            or not _finite_number(cleanup_timeout) \
            or cleanup_timeout < 0 or cleanup_timeout > MAX_LISTING_CLEANUP_SECONDS:
        raise ValueError(f"cleanup timeout must be from 0 through {MAX_LISTING_CLEANUP_SECONDS} seconds")
    _validate_limit(stdout_limit, MAX_LISTING_STDOUT_BYTES, "stdout limit")
    _validate_limit(stderr_limit, MAX_LISTING_STDERR_BYTES, "stderr limit")
    if not command or any(not isinstance(part, str) for part in command):
        raise ValueError("command must be a non-empty list of strings")

    selector = selectors.DefaultSelector()
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            start_new_session=True,
        )
    except OSError as exc:
        selector.close()
        return {
            "available": False,
            "complete": False,
            "attempted": True,
            "returncode": None,
            "timed_out": False,
            "process_reaped": True,
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "stdout_limit_bytes": stdout_limit,
            "stderr_limit_bytes": stderr_limit,
            "stdout_bytes_read": 0,
            "stderr_bytes_read": 0,
            "stdout_truncated": False,
            "stderr_truncated": False,
            "stdout": "",
            "stderr": "",
            "error": _error_record(exc),
        }

    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    counts = {"stdout": 0, "stderr": 0}
    limits = {"stdout": stdout_limit, "stderr": stderr_limit}
    truncated = {"stdout": False, "stderr": False}
    streams = {"stdout": process.stdout, "stderr": process.stderr}
    capture_error: dict[str, str] | None = None
    timed_out = False

    def unregister_and_close(key: selectors.SelectorKey) -> None:
        try:
            selector.unregister(key.fileobj)
        except (KeyError, ValueError):
            pass
        try:
            key.fileobj.close()
        except OSError:
            pass

    def drain(events: list[tuple[selectors.SelectorKey, int]]) -> None:
        nonlocal capture_error
        for key, _ in events:
            label = key.data
            try:
                data = os.read(key.fd, LISTING_READ_CHUNK_BYTES)
            except BlockingIOError:
                continue
            except OSError as exc:
                capture_error = _error_record(exc)
                truncated[label] = True
                unregister_and_close(key)
                continue
            if not data:
                unregister_and_close(key)
                continue
            counts[label] += len(data)
            room = limits[label] - len(buffers[label])
            if room > 0:
                buffers[label].extend(data[:room])
            if len(data) > room:
                truncated[label] = True

    try:
        for label, stream in streams.items():
            if stream is None:
                continue
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)

        deadline = started + float(timeout)
        while selector.get_map() or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            events = selector.select(min(remaining, 0.1))
            if events:
                drain(events)
            if capture_error is not None:
                break

        if timed_out or capture_error is not None:
            _kill_process_group(process)
            cleanup_deadline = time.monotonic() + float(cleanup_timeout)
            while selector.get_map() and time.monotonic() < cleanup_deadline:
                remaining = cleanup_deadline - time.monotonic()
                events = selector.select(min(remaining, 0.05))
                if events:
                    drain(events)
            for key in list(selector.get_map().values()):
                truncated[key.data] = True
                unregister_and_close(key)
    except OSError as exc:
        capture_error = capture_error or _error_record(exc)
        _kill_process_group(process)
        for key in list(selector.get_map().values()):
            truncated[key.data] = True
            unregister_and_close(key)
    finally:
        selector.close()
        for stream in streams.values():
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass

    try:
        # Once the leader exited, this returns immediately. On timeout or a
        # capture failure, the finite grace period avoids an unbounded wait.
        process.wait(timeout=max(float(cleanup_timeout), 0.05))
    except subprocess.TimeoutExpired:
        _kill_process_group(process)
        try:
            process.wait(timeout=0.05)
        except subprocess.TimeoutExpired:
            pass
    process_reaped = process.returncode is not None
    returncode = process.returncode
    elapsed = time.monotonic() - started

    error: dict[str, str] | None = capture_error
    if timed_out:
        error = {"type": "TimeoutExpired", "message": f"command exceeded {float(timeout):g} seconds"}
    elif error is None and not process_reaped:
        error = {"type": "ChildNotReaped", "message": "direct child remained unreaped after bounded cleanup"}
    elif error is None and returncode != 0:
        error = {"type": "ProcessExit", "message": f"command exited with status {returncode}"}

    return {
        "available": returncode == 0 and process_reaped and not timed_out and capture_error is None,
        "complete": (
            returncode == 0 and process_reaped and not timed_out and capture_error is None
            and not truncated["stdout"] and not truncated["stderr"]
        ),
        "attempted": True,
        "returncode": returncode,
        "timed_out": timed_out,
        "process_reaped": process_reaped,
        "elapsed_seconds": round(elapsed, 6),
        "stdout_limit_bytes": stdout_limit,
        "stderr_limit_bytes": stderr_limit,
        "stdout_bytes_read": counts["stdout"],
        "stderr_bytes_read": counts["stderr"],
        "stdout_truncated": truncated["stdout"],
        "stderr_truncated": truncated["stderr"],
        "stdout": bytes(buffers["stdout"]).decode("utf-8", "replace"),
        "stderr": bytes(buffers["stderr"]).decode("utf-8", "replace"),
        "error": error,
    }


def source_routes(path: Path | None) -> dict[str, object]:
    if path is None:
        return {
            "available": False,
            "reason": "host source XML not supplied; local proc/sys collection is repo-independent",
            "max_bytes": MAX_MIXER_XML_BYTES,
            "bytes_read": 0,
            "truncated": False,
            "source_sha256": None,
            "error": None,
        }

    raw, record = _read_limited_bytes(path, MAX_MIXER_XML_BYTES)
    common: dict[str, object] = {
        "source": str(path),
        "max_bytes": MAX_MIXER_XML_BYTES,
        "bytes_read": record["bytes_read"],
        "truncated": record["truncated"],
        "source_sha256": None,
        "error": record["error"],
    }
    if raw is None:
        return {"available": False, **common}
    if record["truncated"]:
        return {
            "available": False,
            **common,
            "error": {
                "type": "ReadLimitExceeded",
                "message": f"source XML exceeds {MAX_MIXER_XML_BYTES} bytes; parse and hash skipped",
            },
        }

    source_hash = hashlib.sha256(raw).hexdigest()
    common["source_sha256"] = source_hash
    # ElementTree does not fetch external entities, but internal declarations
    # can expand input. This source format needs neither DTDs nor entities.
    normalized = raw.upper().replace(b"\x00", b"")
    if b"<!DOCTYPE" in normalized or b"<!ENTITY" in normalized:
        return {
            "available": False,
            **common,
            "error": {
                "type": "ForbiddenXMLDeclaration",
                "message": "DOCTYPE and ENTITY declarations are not accepted in mixer source XML",
            },
        }
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        return {
            "available": False,
            **common,
            "error": _error_record(exc, error_type="XMLParseError"),
        }

    wanted = {
        "media-handset", "media-speaker", "media-speaker-top",
        "media-speaker-bottom", "media-mic", "media-2nd-mic",
        "media-3rd-mic", "media-dualmic", "dev-multi-mic",
    }
    paths: dict[str, object] = {}
    for item in root.findall("path"):
        name = item.get("name")
        if name not in wanted:
            continue
        controls = [
            {"name": control.get("name"), "value": control.get("value")}
            for control in item.iter("ctl")
        ]
        children = [child.get("name") for child in item.findall("path")]
        paths[name] = {"controls": controls, "paths": children}
    return {"available": True, **common, "paths": paths}


def _tool_result(name: str, args: argparse.Namespace) -> dict[str, object]:
    if not args.list_controls:
        return {
            "requested": False,
            "available": None,
            "reason": "not requested; default collection does not open mixer controls",
        }
    executable = shutil.which(name)
    if executable is None:
        return {
            "requested": True,
            "available": False,
            "attempted": False,
            "error": {"type": "ExecutableNotFound", "message": f"{name} not found on PATH"},
        }
    if name == "amixer":
        command = [executable, "-c", "0", "controls"]
    else:
        command = [executable]
    return {"requested": True, **listing(command)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mixer-xml",
        type=Path,
        help="optional local source XML; read is capped at 256 KiB and never falls back to a remote source",
    )
    parser.add_argument(
        "--list-controls",
        action="store_true",
        help="explicitly run only amixer controls and no-argument tinymix metadata listings",
    )
    args = parser.parse_args()

    controls: dict[str, object] = {}
    for card in range(4):
        card_root = f"/proc/asound/card{card}"
        controls[str(card)] = {
            "id": read(f"{card_root}/id", 1024),
            "oss_mixer": read(f"{card_root}/oss_mixer", 8192),
            "codec": read(f"{card_root}/codec#0", DEFAULT_FILE_READ_BYTES),
        }

    tools = {
        "amixer": _tool_result("amixer", args),
        "tinymix": _tool_result("tinymix", args),
    }
    result = {
        "mode": (
            "passive-local-alsa-metadata" if not args.list_controls
            else "local-alsa-metadata-with-explicit-control-listing"
        ),
        "execution_context": {
            "scope": "local-process",
            "proc_sys_source": "local execution environment",
            "target_identity": "not-verified",
            "remote_phone_access": False,
            "semantics": (
                "procfs and sysfs values describe the machine running this process; "
                "if run on the native target they are local target observations, "
                "and if run on a host they are host observations"
            ),
        },
        "pcm_nodes_opened": [],
        "mixer_writes": [],
        "alsa": {
            path: read(path)
            for path in ("/proc/asound/cards", "/proc/asound/pcm", "/proc/asound/devices")
        },
        "control_metadata": controls,
        "sysfs_sound": read("/sys/class/sound/controlC0/id", 1024),
        "control_listing": {
            "requested": args.list_controls,
            "control_device_access": (
                "not-requested" if not args.list_controls
                else "metadata-listing-requested; successful control-node open is not independently verified"
            ),
            "commands_allowlisted": True,
        },
        "tools": tools,
        "source_routes": source_routes(args.mixer_xml),
        "safety": (
            "Default reads are local procfs/sysfs only. --list-controls opts into fixed metadata-only "
            "control listings; no cset/set or PCM playback/capture arguments are passed."
        ),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
