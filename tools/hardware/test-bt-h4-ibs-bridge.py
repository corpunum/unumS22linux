#!/usr/bin/env python3
import os
import pty
import select
import signal
import subprocess
import termios
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
BINARY = "/tmp/bt-h4-ibs-bridge-test"


def raw(fd):
    t = termios.tcgetattr(fd)
    t[0] = t[1] = t[3] = 0
    t[2] = termios.CLOCAL | termios.CREAD
    termios.tcsetattr(fd, termios.TCSANOW, t)


def read_exact(fd, n, timeout=2.0):
    out = bytearray(); end = time.monotonic() + timeout
    while len(out) < n and time.monotonic() < end:
        r, _, _ = select.select([fd], [], [], max(0, end - time.monotonic()))
        if r:
            out.extend(os.read(fd, n - len(out)))
    return bytes(out)


class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
                        os.path.join(ROOT, "tools/hardware/bt-h4-ibs-bridge.c"),
                        "-o", BINARY, "-lutil"], check=True)

    def test_wake_queue_and_h4_forwarding(self):
        physical_master, physical_slave = pty.openpty()
        raw(physical_master); raw(physical_slave)
        proc = subprocess.Popen([BINARY, "-u", os.ttyname(physical_slave)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)
        virtual_name = proc.stdout.readline().strip()
        self.assertTrue(virtual_name.startswith("/dev/pts/"))
        virtual = os.open(virtual_name, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        raw(virtual)
        command = bytes.fromhex("01 03 0c 00")
        command2 = bytes.fromhex("01 01 10 00")
        os.write(virtual, command + command2)
        self.assertEqual(read_exact(physical_master, 1), b"\xfd")
        os.write(physical_master, b"\xfc")
        self.assertEqual(read_exact(physical_master, len(command)), command)
        self.assertEqual(read_exact(physical_master, len(command2)), command2)
        event = bytes.fromhex("04 0e 04 01 03 0c 00")
        os.write(physical_master, event)
        self.assertEqual(read_exact(virtual, len(event)), event)
        os.write(physical_master, b"\xfd")
        self.assertEqual(read_exact(physical_master, 1), b"\xfc")
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=2)
        proc.stdout.close(); proc.stderr.close()
        os.close(virtual); os.close(physical_master); os.close(physical_slave)

    def test_wake_timeout_is_fail_closed(self):
        physical_master, physical_slave = pty.openpty()
        raw(physical_master); raw(physical_slave)
        proc = subprocess.Popen([BINARY, "--uart", os.ttyname(physical_slave)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)
        virtual_name = proc.stdout.readline().strip()
        virtual = os.open(virtual_name, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        raw(virtual)
        os.write(virtual, bytes.fromhex("01 03 0c 00"))
        self.assertEqual(read_exact(physical_master, 3, timeout=1.0), b"\xfd" * 3)
        proc.wait(timeout=2)
        self.assertNotEqual(proc.returncode, 0)
        proc.stdout.close(); proc.stderr.close()
        os.close(virtual); os.close(physical_master); os.close(physical_slave)

    def test_iso_reserved_bits_fail_closed(self):
        physical_master, physical_slave = pty.openpty()
        raw(physical_master); raw(physical_slave)
        proc = subprocess.Popen([BINARY, "--uart", os.ttyname(physical_slave)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)
        virtual_name = proc.stdout.readline().strip()
        virtual = os.open(virtual_name, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        raw(virtual)
        os.write(virtual, bytes.fromhex("05 00 00 00 c0"))
        proc.wait(timeout=2)
        self.assertNotEqual(proc.returncode, 0)
        proc.stdout.close(); proc.stderr.close()
        os.close(virtual); os.close(physical_master); os.close(physical_slave)


if __name__ == "__main__":
    unittest.main()
