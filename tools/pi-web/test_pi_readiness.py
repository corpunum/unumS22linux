"""Synthetic host tests for exact Pi session and service readiness."""
from __future__ import annotations

from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
import hashlib
from unittest import mock
import importlib.util
import sys

_SOURCE = Path(__file__).with_name("pi_readiness.py")
_SPEC = importlib.util.spec_from_file_location("pi_readiness", _SOURCE)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"could not load Pi readiness module at {_SOURCE}")
readiness = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = readiness
_SPEC.loader.exec_module(readiness)


def make_process(proc_root: Path, root: Path, pid: int, *, uid: int,
                 argv: tuple[str, ...], executable: Path | None = None,
                 state: str = "S") -> None:
    root.mkdir(parents=True, exist_ok=True)
    proc = proc_root / str(pid)
    proc.mkdir(parents=True)
    (proc / "root").symlink_to(root, target_is_directory=True)
    executable = root / readiness.PI_EXECUTABLE if executable is None else executable
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_bytes(b"synthetic executable identity")
    (proc / "exe").symlink_to(executable)
    (proc / "stat").write_text(f"{pid} (pi) {state} 1\n")
    (proc / "status").write_text(
        f"Name:\tpi\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
    (proc / "cmdline").write_bytes(b"\0".join(arg.encode() for arg in argv) + b"\0")


def make_socket(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(path))


class PiSessionReadinessTest(unittest.TestCase):
    def test_healthy_idle_services_allow_absent_on_demand_session(self):
        with tempfile.TemporaryDirectory(prefix="pr-", dir="/tmp") as temporary:
            socket_path = Path(temporary) / "arch" / readiness.TMUX_SOCKET
            result = readiness.inspect_pi_session(
                socket_path,
                query=mock.Mock(side_effect=AssertionError("absent socket must not be queried")),
            )
            report = readiness.readiness_report(
                web_ready=True, model_ready=True, desktop_ready=True,
                desktop_pi_status="ready",
                pi_session_status=result["status"],
            )

        self.assertEqual(result, {"status": "absent", "ready": False})
        self.assertTrue(report["required_services_ready"])
        self.assertTrue(report["required_services"]["desktop"])
        self.assertEqual(report["pi_session_status"], "absent")
        self.assertFalse(report["pi_session_ready"])

    def test_unrelated_tmux_server_does_not_satisfy_dedicated_pi_socket(self):
        with tempfile.TemporaryDirectory(prefix="pr-", dir="/tmp") as temporary:
            root = Path(temporary)
            unrelated_socket = root / "unrelated-tmux.sock"
            make_socket(unrelated_socket)
            dedicated_socket = root / "arch" / readiness.TMUX_SOCKET
            query = mock.Mock(side_effect=AssertionError("wrong socket must not be queried"))
            result = readiness.inspect_pi_session(dedicated_socket, query=query)

        self.assertEqual(result["status"], "absent")
        query.assert_not_called()

    def test_exact_dedicated_session_requires_configured_binary_and_uid_1000(self):
        with tempfile.TemporaryDirectory(prefix="pr-", dir="/tmp") as temporary:
            root = Path(temporary)
            socket_path = root / "arch" / readiness.TMUX_SOCKET
            make_socket(socket_path)
            proc_root = root / "proc"
            pi_root = root / "arch"
            make_process(proc_root, pi_root, 234, uid=1000,
                         argv=("/opt/s22-pi/0.86.1/pi/pi", "--offline"))
            result = readiness.inspect_pi_session(
                socket_path, proc_root=proc_root,
                query=lambda: subprocess.CompletedProcess(
                    ["tmux"], 0, stdout="pi\t234\n", stderr=""),
            )

        self.assertEqual(result, {"status": "ready", "ready": True})

    def test_wrong_uid_or_executable_fails_closed(self):
        for uid, configured in ((0, True), (1000, False)):
            with self.subTest(uid=uid, configured=configured), tempfile.TemporaryDirectory(
                    prefix="pr-", dir="/tmp") as temporary:
                root = Path(temporary)
                socket_path = root / "arch" / readiness.TMUX_SOCKET
                make_socket(socket_path)
                proc_root = root / "proc"
                pi_root = root / "arch"
                executable = (pi_root / readiness.PI_EXECUTABLE if configured else
                              pi_root / "opt/other/pi")
                make_process(proc_root, pi_root, 235, uid=uid,
                             argv=("/opt/s22-pi/0.86.1/pi/pi",),
                             executable=executable)
                result = readiness.inspect_pi_session(
                    socket_path, proc_root=proc_root,
                    query=lambda: subprocess.CompletedProcess(
                        ["tmux"], 0, stdout="pi\t235\n", stderr=""),
                )
                self.assertEqual(result["status"], "failed")

    def test_missing_or_failed_required_service_blocks_service_readiness(self):
        for service in ("web", "model", "desktop"):
            with self.subTest(service=service):
                values = {"web": True, "model": True, "desktop": True}
                values[service] = False
                report = readiness.readiness_report(
                    web_ready=values["web"],
                    model_ready=values["model"],
                    desktop_ready=values["desktop"],
                    desktop_pi_status="ready",
                    pi_session_status="absent",
                )
                self.assertFalse(report["required_services_ready"])
                self.assertFalse(report["required_services"][service])
                self.assertEqual(report["pi_session_status"], "absent")

        missing_desktop_pi = readiness.readiness_report(
            web_ready=True, model_ready=True, desktop_ready=True,
            desktop_pi_status="absent", pi_session_status="absent",
        )
        self.assertFalse(missing_desktop_pi["required_services_ready"])
        self.assertFalse(missing_desktop_pi["required_services"]["desktop_pi"])
        self.assertEqual(missing_desktop_pi["desktop_pi_status"], "absent")

        unknown = readiness.readiness_report(
            web_ready=True, model_ready=None, desktop_ready=True,
            desktop_pi_status="ready",
            pi_session_status="absent",
        )
        self.assertFalse(unknown["required_services"]["model"])
        self.assertFalse(unknown["required_services_ready"])

        failed_session = readiness.readiness_report(
            web_ready=True, model_ready=True, desktop_ready=True,
            desktop_pi_status="ready",
            pi_session_status="failed",
        )
        self.assertTrue(failed_session["required_services_ready"])
        self.assertTrue(failed_session["required_services"]["desktop"])
        self.assertFalse(failed_session["pi_session_ready"])

    def test_runtime_pids_must_match_exact_desktop_and_model_argv(self):
        with tempfile.TemporaryDirectory(prefix="pi-readiness-") as temporary:
            root = Path(temporary)
            proc_root = root / "proc"
            desktop_argv = (
                "/usr/bin/dbus-run-session", "--", "/usr/bin/Hyprland",
                "--i-am-really-stupid", "--config", "/root/hyprland-omarchy-ui.lua",
            )
            model_argv = (
                "/mnt/model-bench/server/bin/llama-server", "-m", readiness.MODEL_ID,
            )
            make_process(proc_root, root / "desktop-root", 100, uid=0, argv=desktop_argv)
            make_process(proc_root, root / "model-root", 101, uid=0, argv=model_argv)
            runtime = {
                "uuid": readiness.PERSISTENT_UUID,
                "model_profile": readiness.MODEL_PROFILE,
                "desktop_pid": 100,
                "model_pid": 101,
            }
            self.assertEqual(readiness.runtime_process_readiness(runtime, proc_root),
                             {"desktop": True, "model_process": True})
            runtime["model_profile"] = "qwen2b"
            self.assertEqual(readiness.runtime_process_readiness(runtime, proc_root),
                             {"desktop": True, "model_process": False})
            runtime["model_profile"] = readiness.MODEL_PROFILE
            runtime["desktop_pid"] = 102
            self.assertEqual(readiness.runtime_process_readiness(runtime, proc_root),
                             {"desktop": False, "model_process": True})

    def test_desktop_pi_requires_exact_binary_inode_and_uid(self):
        with tempfile.TemporaryDirectory(prefix="pi-desktop-readiness-") as temporary:
            root = Path(temporary)
            proc_root = root / "proc"
            binary = root / "mounted" / "opt/s22-pi/0.86.1/pi/pi"
            make_process(proc_root, root / "chroot", 300, uid=1000,
                         argv=("/opt/s22-pi/0.86.1/pi/pi", "--offline"),
                         executable=binary)
            self.assertEqual(readiness.exact_process_status(
                proc_root, binary, uid=1000), "ready")
            self.assertEqual(readiness.exact_process_status(
                proc_root, binary, uid=0), "absent")
            (proc_root / "300" / "stat").write_text("300 (pi) Z 1\n")
            self.assertEqual(readiness.exact_process_status(
                proc_root, binary, uid=1000), "absent")

    def test_browser_service_receipt_requires_exact_pid_start_executable_uid_and_privileges(self):
        with tempfile.TemporaryDirectory(prefix="pi-browser-process-") as temporary:
            root = Path(temporary)
            proc_root = root / "proc"
            binary = root / "ttyd"
            binary.write_bytes(b"exact ttyd bytes")
            proc = proc_root / "400"
            proc.mkdir(parents=True)
            (proc / "exe").symlink_to(binary)
            stat_fields = ["S"] + ["0"] * 18 + ["777"]
            (proc / "stat").write_text("400 (ttyd) " + " ".join(stat_fields) + "\n")
            status = ("Uid:\t1000\t1000\t1000\t1000\n"
                      "CapEff:\t0000000000000000\nCapPrm:\t0000000000000000\n"
                      "CapBnd:\t0000000000000000\nNoNewPrivs:\t1\n")
            (proc / "status").write_text(status)
            record = {"pid": 400, "start_ticks": "777"}
            digest = hashlib.sha256(binary.read_bytes()).hexdigest()
            self.assertEqual(readiness.recorded_process_status(
                record, proc_root, executable_sha256=digest, uid=1000), "ready")
            self.assertEqual(readiness.recorded_process_status(
                dict(record, start_ticks="778"), proc_root,
                executable_sha256=digest, uid=1000), "absent")
            self.assertEqual(readiness.recorded_process_status(
                record, proc_root, executable_sha256="0" * 64, uid=1000), "failed")
            (proc / "status").write_text(status.replace("CapEff:\t0000000000000000",
                                                       "CapEff:\t0000000000000001"))
            self.assertEqual(readiness.recorded_process_status(
                record, proc_root, executable_sha256=digest, uid=1000), "failed")


if __name__ == "__main__":
    unittest.main()
