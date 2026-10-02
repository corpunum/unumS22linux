#!/usr/bin/env python3
"""Host regressions for passive, bounded audio-control readiness collection."""
from __future__ import annotations

import contextlib
import errno
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/hardware/audio-control-readiness.py"
SPEC = importlib.util.spec_from_file_location("audio_control_readiness", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load audio-control-readiness.py")
readiness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(readiness)


class AudioControlReadinessTests(unittest.TestCase):
    def run_main(self, argv: list[str]) -> dict[str, object]:
        output = io.StringIO()
        with mock.patch.object(sys, "argv", [str(SCRIPT), *argv]), \
                contextlib.redirect_stdout(output):
            self.assertEqual(readiness.main(), 0)
        return json.loads(output.getvalue())

    def assert_exception_cleanup(
        self,
        command: list[str],
        selector_factory: object,
        expected_exception: BaseException,
        *,
        read_fault: bool = False,
        cleanup_fault: bool = False,
    ) -> BaseException:
        """Inject a post-spawn failure and verify cleanup without losing it."""
        children: list[object] = []
        real_popen = readiness.subprocess.Popen
        real_read = readiness.os.read
        armed = False

        def capture_popen(*args: object, **kwargs: object) -> object:
            nonlocal armed
            process = real_popen(*args, **kwargs)
            children.append(process)
            armed = True
            return process

        def faulting_read(fd: int, size: int) -> bytes:
            if armed and read_fault:
                raise expected_exception
            return real_read(fd, size)

        caught: BaseException | None = None
        try:
            with mock.patch.object(readiness.subprocess, "Popen", side_effect=capture_popen), \
                    mock.patch.object(readiness.selectors, "DefaultSelector", side_effect=selector_factory), \
                    mock.patch.object(readiness.os, "read", side_effect=faulting_read), \
                    mock.patch.object(
                        readiness.os, "killpg",
                        side_effect=PermissionError("injected group signal refusal"),
                    ) if cleanup_fault else contextlib.nullcontext():
                with self.assertRaises(type(expected_exception)) as raised:
                    readiness.listing(command, timeout=2, cleanup_timeout=0.05)
                caught = raised.exception
                self.assertIs(caught, expected_exception)
                self.assertEqual(len(children), 1)
                process = children[0]
                was_unreaped_at_return = process.returncode is None
                was_alive_at_return = process.poll() is None
                if was_alive_at_return:
                    # Keep a failing baseline run from leaving a sleeper behind.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except OSError:
                        process.kill()
                    process.wait(timeout=1)
                self.assertFalse(was_alive_at_return, "listing leaked its direct child")
                self.assertFalse(was_unreaped_at_return, "listing did not reap its direct child")
                if cleanup_fault:
                    notes = getattr(caught, "__notes__", [])
                    self.assertTrue(any("injected group signal refusal" in note for note in notes))
        finally:
            for process in children:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except OSError:
                        process.kill()
                    process.wait(timeout=1)
        if caught is None:
            self.fail("fault injection did not reach the listing helper")
        return caught

    def test_keyboard_interrupt_from_select_terminates_and_reaps_child(self) -> None:
        real_selector_factory = readiness.selectors.DefaultSelector
        interrupt = KeyboardInterrupt("injected selector interrupt")

        class InterruptingSelector:
            def __init__(self) -> None:
                self.selector = real_selector_factory()

            def register(self, *args: object, **kwargs: object) -> object:
                return self.selector.register(*args, **kwargs)

            def get_map(self) -> object:
                return self.selector.get_map()

            def select(self, timeout: float | None = None) -> object:
                raise interrupt

            def unregister(self, *args: object, **kwargs: object) -> object:
                return self.selector.unregister(*args, **kwargs)

            def close(self) -> None:
                self.selector.close()

        self.assert_exception_cleanup(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            InterruptingSelector,
            interrupt,
        )

    def test_unexpected_selector_registration_error_terminates_and_reaps_child(self) -> None:
        real_selector_factory = readiness.selectors.DefaultSelector
        error = RuntimeError("injected selector registration error")

        class FailingRegisterSelector:
            def __init__(self) -> None:
                self.selector = real_selector_factory()

            def register(self, *args: object, **kwargs: object) -> object:
                raise error

            def get_map(self) -> object:
                return self.selector.get_map()

            def select(self, timeout: float | None = None) -> object:
                return self.selector.select(timeout)

            def unregister(self, *args: object, **kwargs: object) -> object:
                return self.selector.unregister(*args, **kwargs)

            def close(self) -> None:
                self.selector.close()

        self.assert_exception_cleanup(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            FailingRegisterSelector,
            error,
        )

    def test_unexpected_pipe_drain_error_terminates_and_reaps_child(self) -> None:
        selector_factory = readiness.selectors.DefaultSelector
        error = RuntimeError("injected pipe drain error")
        self.assert_exception_cleanup(
            [sys.executable, "-c", "import os,time; os.write(1,b'x'); time.sleep(30)"],
            selector_factory,
            error,
            read_fault=True,
        )

    def test_cleanup_failure_is_reported_without_replacing_original_exception(self) -> None:
        real_selector_factory = readiness.selectors.DefaultSelector
        error = RuntimeError("injected selector failure")

        class FailingSelectSelector:
            def __init__(self) -> None:
                self.selector = real_selector_factory()

            def register(self, *args: object, **kwargs: object) -> object:
                return self.selector.register(*args, **kwargs)

            def get_map(self) -> object:
                return self.selector.get_map()

            def select(self, timeout: float | None = None) -> object:
                raise error

            def unregister(self, *args: object, **kwargs: object) -> object:
                return self.selector.unregister(*args, **kwargs)

            def close(self) -> None:
                self.selector.close()

        self.assert_exception_cleanup(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            FailingSelectSelector,
            error,
            cleanup_fault=True,
        )

    def test_default_does_not_discover_or_invoke_control_tools(self) -> None:
        empty_read = {
            "available": False,
            "value": None,
            "bytes_read": 0,
            "truncated": False,
            "error": {"type": "FileNotFoundError", "message": "fixture"},
        }
        with mock.patch.object(readiness, "read", return_value=empty_read), \
                mock.patch.object(readiness.shutil, "which", side_effect=AssertionError("tool lookup")), \
                mock.patch.object(readiness, "listing", side_effect=AssertionError("control invocation")):
            result = self.run_main([])

        self.assertEqual(result["execution_context"]["scope"], "local-process")
        self.assertEqual(result["execution_context"]["proc_sys_source"], "local execution environment")
        self.assertEqual(result["mode"], "passive-local-alsa-metadata")
        self.assertFalse(result["execution_context"]["remote_phone_access"])
        self.assertFalse(result["control_listing"]["requested"])
        self.assertEqual(result["control_listing"]["control_device_access"], "not-requested")
        self.assertEqual(result["pcm_nodes_opened"], [])
        self.assertEqual(result["mixer_writes"], [])
        self.assertNotIn("phone_access", result)

    def test_explicit_control_listing_uses_only_fixed_metadata_commands(self) -> None:
        empty_read = {
            "available": False,
            "value": None,
            "bytes_read": 0,
            "truncated": False,
            "error": {"type": "FileNotFoundError", "message": "fixture"},
        }
        seen: list[list[str]] = []

        def fake_listing(command: list[str]) -> dict[str, object]:
            seen.append(command)
            return {"available": True, "returncode": 0, "stdout": "", "stderr": ""}

        with mock.patch.object(readiness, "read", return_value=empty_read), \
                mock.patch.object(readiness.shutil, "which", side_effect=lambda name: f"/fake/{name}"), \
                mock.patch.object(readiness, "listing", side_effect=fake_listing):
            result = self.run_main(["--list-controls"])

        self.assertEqual(seen, [
            ["/fake/amixer", "-c", "0", "controls"],
            ["/fake/tinymix"],
        ])
        self.assertTrue(result["control_listing"]["requested"])
        self.assertEqual(result["mode"], "local-alsa-metadata-with-explicit-control-listing")
        self.assertIn("metadata-listing-requested",
                      result["control_listing"]["control_device_access"])
        self.assertEqual(result["pcm_nodes_opened"], [])
        self.assertEqual(result["mixer_writes"], [])

    def test_limited_read_reports_truncation_exact_bytes_and_io_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large"
            path.write_bytes(b"abcdefghij")
            limited = readiness.read(str(path), 4)
            self.assertTrue(limited["available"])
            self.assertEqual(limited["value"], "abcd")
            self.assertEqual(limited["bytes_read"], 5)
            self.assertTrue(limited["truncated"])

            path.write_bytes(b"abcd")
            exact = readiness.read(str(path), 4)
            self.assertEqual(exact["value"], "abcd")
            self.assertEqual(exact["bytes_read"], 4)
            self.assertFalse(exact["truncated"])

            missing = readiness.read(str(Path(directory) / "absent"), 4)
            self.assertFalse(missing["available"])
            self.assertEqual(missing["error"]["type"], "FileNotFoundError")

    def test_limited_read_rejects_unbounded_requested_limits_and_nonregular_files(self) -> None:
        with self.assertRaises(ValueError):
            readiness.read("unused", readiness.MAX_FILE_READ_BYTES + 1)
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / "fifo"
            os.mkfifo(fifo)
            result = readiness.read(str(fifo), 10)
            self.assertFalse(result["available"])
            self.assertEqual(result["error"]["type"], "NotRegularFile")

    def test_source_xml_is_bounded_hashed_only_when_complete_and_errors_are_truthful(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mixer.xml"
            content = b'<mixer><path name="media-handset"><ctl name="Speaker" value="1"/></path></mixer>'
            path.write_bytes(content)
            complete = readiness.source_routes(path)
            self.assertTrue(complete["available"])
            self.assertEqual(complete["bytes_read"], len(content))
            self.assertFalse(complete["truncated"])
            self.assertEqual(complete["source_sha256"], hashlib.sha256(content).hexdigest())
            self.assertEqual(complete["paths"]["media-handset"]["controls"],
                             [{"name": "Speaker", "value": "1"}])

            path.write_bytes(b"<mixer>" + b" " * readiness.MAX_MIXER_XML_BYTES + b"</mixer>")
            oversized = readiness.source_routes(path)
            self.assertFalse(oversized["available"])
            self.assertTrue(oversized["truncated"])
            self.assertEqual(oversized["bytes_read"], readiness.MAX_MIXER_XML_BYTES + 1)
            self.assertIsNone(oversized["source_sha256"])

            path.write_bytes(b"<mixer><path></mixer>")
            malformed = readiness.source_routes(path)
            self.assertFalse(malformed["available"])
            self.assertEqual(malformed["error"]["type"], "XMLParseError")
            self.assertFalse(malformed["truncated"])

            link = Path(directory) / "mixer-link.xml"
            link.symlink_to(path)
            linked = readiness.source_routes(link)
            self.assertFalse(linked["available"])
            self.assertEqual(linked["error"]["type"], "SymlinkRefused")

    def test_listing_retention_is_capped_and_does_not_spool_to_disk(self) -> None:
        code = "import os; os.write(1, b'x' * 1048576); os.write(2, b'y' * 1048576)"
        tracked_fds: list[int] = []
        real_temporary_file = tempfile.TemporaryFile

        def tracked_temporary_file(*args: object, **kwargs: object) -> object:
            file_object = real_temporary_file(*args, **kwargs)
            tracked_fds.append(os.dup(file_object.fileno()))
            return file_object

        try:
            with mock.patch.object(tempfile, "TemporaryFile", side_effect=tracked_temporary_file):
                result = readiness.listing([sys.executable, "-c", code])
            spooled_bytes = sum(os.fstat(fd).st_size for fd in tracked_fds)
        finally:
            for fd in tracked_fds:
                os.close(fd)
        self.assertTrue(result["available"])
        self.assertFalse(result["complete"])
        self.assertTrue(result["stdout_truncated"])
        self.assertTrue(result["stderr_truncated"])
        self.assertLessEqual(len(result["stdout"].encode("utf-8")), readiness.MAX_LISTING_STDOUT_BYTES)
        self.assertLessEqual(len(result["stderr"].encode("utf-8")), readiness.MAX_LISTING_STDERR_BYTES)
        self.assertTrue(result["process_reaped"])
        self.assertEqual(spooled_bytes, 0)

    def test_listing_rejects_nonfinite_budgets_before_starting_a_process(self) -> None:
        command = [sys.executable, "-c", "pass"]
        for timeout in (float("nan"), float("inf"), float("-inf"), 10 ** 1000):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                readiness.listing(command, timeout=timeout)
        for cleanup_timeout in (float("nan"), float("inf"), float("-inf"), 10 ** 1000):
            with self.subTest(cleanup_timeout=cleanup_timeout), self.assertRaises(ValueError):
                readiness.listing(command, cleanup_timeout=cleanup_timeout)

        completed = readiness.listing(command, timeout=1)
        self.assertTrue(completed["available"])
        self.assertTrue(completed["complete"])

    def test_listing_timeout_kills_and_reaps_the_command_group(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            child_pid_path = Path(directory) / "child.pid"
            child_code = (
                "import subprocess,sys,time; "
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
                "open(sys.argv[1],'w').write(str(child.pid)); time.sleep(30)"
            )
            with mock.patch.object(readiness, "DEFAULT_LISTING_TIMEOUT_SECONDS", 0.25, create=True):
                result = readiness.listing(
                    [sys.executable, "-c", child_code, str(child_pid_path)],
                )
            self.assertTrue(result["timed_out"])
            self.assertTrue(result["process_reaped"])
            self.assertLess(result["elapsed_seconds"], 2.0)
            self.assertTrue(child_pid_path.is_file())
            child_pid = int(child_pid_path.read_text())

            # The fixture's descendant inherits the pipes. It must be gone (or
            # already a dead zombie awaiting init reaping) before returning.
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                proc_stat = Path(f"/proc/{child_pid}/stat")
                try:
                    fields = proc_stat.read_text().split()
                except (FileNotFoundError, ProcessLookupError):
                    break
                if len(fields) > 2 and fields[2] == "Z":
                    break
                time.sleep(0.02)
            else:
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    os.kill(child_pid, signal.SIGKILL)
                    self.fail("listing left a descendant process running")

    def test_descendant_disappearing_during_proc_read_is_already_reaped(self) -> None:
        original_read = Path.read_text
        injected = []

        def disappearing_read(path, *args, **kwargs):
            if str(path).startswith("/proc/") and path.name == "stat":
                injected.append(path)
                raise ProcessLookupError(errno.ESRCH, "injected process disappearance")
            return original_read(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", disappearing_read):
            self.test_listing_timeout_kills_and_reaps_the_command_group()
        self.assertEqual(len(injected), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
