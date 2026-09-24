"""Host-only, exact-identity readiness helpers for the Pi browser terminal.

The browser session is on demand.  Its absence is reported separately from
the persistent desktop, model, and web services.
"""
from __future__ import annotations

from pathlib import Path
import stat
from typing import Callable


PERSISTENT_UUID = "1dd55c26-bd57-489a-9d9b-4c60e6f430eb"
MODEL_PROFILE = "qwen4b"
MODEL_ID = "/mnt/model-bench/models/Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf"
PI_EXECUTABLE = Path("opt/s22-pi/0.86.1/pi/pi")
TMUX_SOCKET = Path("home/alarm/.pi/agent/web-sessions/web-musl.tmux")


def _proc_entry(proc_root: Path, pid: int) -> Path:
    if type(pid) is not int or pid <= 0:
        raise ValueError("invalid pid")
    return proc_root / str(pid)


def process_matches(
    proc_root: Path,
    pid: int,
    *,
    uid: int,
    argv_sequence: tuple[str, ...] = (),
    executable_relative: Path | None = None,
) -> bool:
    """Verify one known PID against proc state, UID, argv, and optional inode.

    ``executable_relative`` is resolved through that process's ``/proc/PID/root``
    and compared by device/inode to ``/proc/PID/exe``.  This works for the Pi
    binary inside the Arch chroot without accepting a process-name match.
    """
    try:
        entry = _proc_entry(proc_root, pid)
        stat_fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
        if not stat_fields or stat_fields[0] in {"Z", "X"}:
            return False

        status_fields = {}
        for line in (entry / "status").read_text().splitlines():
            name, separator, value = line.partition(":")
            if separator:
                status_fields[name] = value.strip()
        uid_values = status_fields.get("Uid", "").split()
        if len(uid_values) != 4 or any(int(value) != uid for value in uid_values):
            return False

        if argv_sequence:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
            decoded = [value.decode("utf-8", "surrogateescape") for value in argv if value]
            width = len(argv_sequence)
            if not any(tuple(decoded[index:index + width]) == argv_sequence
                       for index in range(len(decoded) - width + 1)):
                return False

        if executable_relative is not None:
            executable = entry / "exe"
            configured = entry / "root" / executable_relative
            actual_info = executable.stat()
            configured_info = configured.stat()
            if (actual_info.st_dev, actual_info.st_ino) != (
                    configured_info.st_dev, configured_info.st_ino):
                return False
        return True
    except (OSError, ValueError, IndexError):
        return False


def runtime_process_readiness(runtime: object, proc_root: Path = Path("/proc")) -> dict[str, bool]:
    """Validate the supervisor's pinned runtime record and its exact service PIDs."""
    if not isinstance(runtime, dict):
        return {"desktop": False, "model_process": False}
    if runtime.get("uuid") != PERSISTENT_UUID:
        return {"desktop": False, "model_process": False}

    desktop_pid = runtime.get("desktop_pid")
    model_pid = runtime.get("model_pid")
    desktop = process_matches(
        proc_root,
        desktop_pid,
        uid=0,
        argv_sequence=("/usr/bin/Hyprland", "--i-am-really-stupid", "--config",
                       "/root/hyprland-omarchy-ui.lua"),
    )
    model = (runtime.get("model_profile") == MODEL_PROFILE and process_matches(
        proc_root,
        model_pid,
        uid=0,
        argv_sequence=("/mnt/model-bench/server/bin/llama-server", "-m", MODEL_ID),
    ))
    return {"desktop": desktop, "model_process": model}


def _result_status(result: object) -> tuple[str, str]:
    returncode = getattr(result, "returncode", None)
    stdout = getattr(result, "stdout", None)
    stderr = getattr(result, "stderr", None)
    if returncode == 0 and isinstance(stdout, str):
        return "ok", stdout
    if isinstance(stderr, str) and "no server running" in stderr.lower():
        return "absent", ""
    return "failed", ""


def inspect_pi_session(
    socket_path: Path,
    *,
    proc_root: Path = Path("/proc"),
    query: Callable[[], object],
) -> dict[str, object]:
    """Inspect only the dedicated tmux socket; never create or attach a session."""
    try:
        socket_info = socket_path.lstat()
    except FileNotFoundError:
        return {"status": "absent", "ready": False}
    except OSError:
        return {"status": "failed", "ready": False}
    if not stat.S_ISSOCK(socket_info.st_mode) or socket_info.st_uid != 1000:
        return {"status": "failed", "ready": False}

    try:
        result = query()
    except Exception:
        return {"status": "failed", "ready": False}
    result_status, output = _result_status(result)
    if result_status == "absent":
        return {"status": "absent", "ready": False}
    if result_status != "ok":
        return {"status": "failed", "ready": False}

    pi_pids: list[int] = []
    try:
        for line in output.splitlines():
            fields = line.split("\t")
            if len(fields) != 2 or not fields[0]:
                return {"status": "failed", "ready": False}
            if fields[0] == "pi":
                pid = int(fields[1])
                if pid <= 0:
                    return {"status": "failed", "ready": False}
                pi_pids.append(pid)
    except ValueError:
        return {"status": "failed", "ready": False}

    if not pi_pids:
        return {"status": "absent", "ready": False}
    if len(pi_pids) != 1 or not process_matches(
            proc_root,
            pi_pids[0],
            uid=1000,
            executable_relative=PI_EXECUTABLE,
    ):
        return {"status": "failed", "ready": False}
    return {"status": "ready", "ready": True}


def readiness_report(
    *,
    web_ready: object,
    model_ready: object,
    desktop_ready: object,
    pi_session_status: object,
) -> dict[str, object]:
    """Keep required services independent from the optional Pi session."""
    services = {
        "web": web_ready is True,
        "model": model_ready is True,
        "desktop": desktop_ready is True,
    }
    if pi_session_status not in ("absent", "ready", "failed"):
        pi_session_status = "failed"
    return {
        "required_services": services,
        "required_services_ready": all(services.values()),
        "pi_session_status": pi_session_status,
        "pi_session_ready": pi_session_status == "ready",
    }
