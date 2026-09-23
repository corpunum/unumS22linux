#!/usr/bin/env python3
"""Host-only PTY and unit regressions for the QCA H4/IBS bridge."""
import os
import pty
import select
import signal
import subprocess
import termios
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
COMMAND = bytes.fromhex("01 03 0c 00")
EVENT = bytes.fromhex("04 0e 04 01 03 0c 00")


def raw(fd):
    t = termios.tcgetattr(fd)
    t[0] = t[1] = t[3] = 0
    t[2] = termios.CLOCAL | termios.CREAD
    termios.tcsetattr(fd, termios.TCSANOW, t)


def read_exact(fd, n, timeout=2.0):
    out = bytearray()
    end = time.monotonic() + timeout
    while len(out) < n and time.monotonic() < end:
        ready, _, _ = select.select([fd], [], [], max(0, end - time.monotonic()))
        if ready:
            out.extend(os.read(fd, n - len(out)))
    return bytes(out)


def assert_quiet(test, fd, timeout=0.05):
    ready, _, _ = select.select([fd], [], [], timeout)
    test.assertFalse(ready, "unexpected bytes before a complete H4 frame")


def start_bridge(test):
    physical_master, physical_slave = pty.openpty()
    raw(physical_master)
    raw(physical_slave)
    proc = subprocess.Popen([test.BINARY, "--uart", os.ttyname(physical_slave)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)
    try:
        virtual_name = proc.stdout.readline().strip()
        test.assertTrue(virtual_name.startswith("/dev/pts/"), virtual_name)
        virtual = os.open(virtual_name, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        raw(virtual)
    except BaseException:
        if proc.poll() is None:
            proc.kill()
        proc.communicate()
        os.close(physical_master)
        os.close(physical_slave)
        raise
    return proc, virtual, physical_master, physical_slave


def stop_bridge(proc, virtual, physical_master, physical_slave, sig=signal.SIGTERM):
    if proc.poll() is None:
        proc.send_signal(sig)
    try:
        stdout, stderr = proc.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        _, stderr = proc.communicate()
        raise AssertionError("bridge did not stop within two seconds")
    finally:
        os.close(virtual)
        os.close(physical_master)
        os.close(physical_slave)
    return proc.returncode, stdout, stderr


UNIT_SOURCE = r'''#define S22_BT_BRIDGE_EMBED 1
#include "tools/hardware/bt-h4-ibs-bridge.c"
#include <pthread.h>
#include <sys/socket.h>

static void die(const char *why) { fprintf(stderr, "unit failure: %s\n", why); exit(1); }

static void parser_test(void) {
    struct parser p = {0};
    const uint8_t event[] = {4, 0x0e, 4, 1, 3, 0x0c, 0};
    uint8_t *frame = NULL; size_t n = 0;
    for (size_t i = 0; i < sizeof(event); ++i) {
        int r = parser_byte(&p, event[i], &frame, &n);
        if (r != (i + 1 == sizeof(event))) die("fragmented event completion boundary");
    }
    if (n != sizeof(event) || memcmp(frame, event, n)) die("fragmented event payload");

    const uint8_t oversized_acl[] = {2, 1, 0, 0xff, 0xff};
    int r = 0;
    for (size_t i = 0; i < sizeof(oversized_acl); ++i)
        r = parser_byte(&p, oversized_acl[i], &frame, &n);
    if (r != -1 || p.n || p.need) die("oversized ACL length was not reset/rejected");

    const uint8_t malformed_iso[] = {5, 0, 0, 0, 0xc0};
    for (size_t i = 0; i < sizeof(malformed_iso); ++i)
        r = parser_byte(&p, malformed_iso[i], &frame, &n);
    if (r != -1 || p.n || p.need) die("reserved ISO bits were not reset/rejected");
}

static void queue_test(void) {
    int sv[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv)) die("socketpair for queue test");
    struct bridge x = {.uart = sv[0], .pty_master = -1, .pty_slave = -1};
    const uint8_t command[] = {1, 3, 0x0c, 0};
    for (size_t i = 0; i < MAX_PENDING; ++i)
        if (queue_pty_frame(&x, command, sizeof(command))) die("queue rejected before bound");
    uint8_t wake = 0;
    if (read(sv[1], &wake, 1) != 1 || wake != IBS_WAKE) die("initial IBS wake");
    if (x.pending_count != MAX_PENDING) die("queue did not reach exact bound");
    if (queue_pty_frame(&x, command, sizeof(command)) == 0)
        die("queue accepted frame beyond bound");
    if (x.pending_count != MAX_PENDING) die("overflow corrupted queue accounting");
    close(sv[0]); close(sv[1]);
}

static void ibs_state_test(void) {
    int sv[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv)) die("socketpair for IBS test");
    struct bridge x = {.uart = sv[0], .pty_master = -1, .pty_slave = -1};
    if (handle_uart_byte(&x, IBS_WAKE) || !x.rx_awake) die("wake state transition");
    uint8_t ack = 0;
    if (read(sv[1], &ack, 1) != 1 || ack != IBS_ACK) die("wake ACK response");
    if (handle_uart_byte(&x, IBS_SLEEP) || x.rx_awake) die("sleep state transition");

    const uint8_t command[] = {1, 3, 0x0c, 0};
    memcpy(x.pending[0], command, sizeof(command));
    x.pending_n[0] = sizeof(command); x.pending_count = 1;
    x.waiting_ack = 1; x.retries = 1;
    if (handle_uart_byte(&x, IBS_ACK) || x.waiting_ack || !x.tx_awake ||
        x.ibs_ack_rx != 1 || x.pending_count)
        die("ACK did not transition awake and flush pending H4");
    uint8_t received[sizeof(command)];
    if (read(sv[1], received, sizeof(received)) != (ssize_t)sizeof(received) ||
        memcmp(received, command, sizeof(command))) die("ACK flush payload");
    close(sv[0]); close(sv[1]);
}

struct drain_args { int fd; const uint8_t *expected; size_t length; int bad; };
static void *drain_socket(void *arg) {
    struct drain_args *a = arg;
    uint8_t b[1024]; size_t offset = 0;
    while (offset < a->length) {
        ssize_t n = read(a->fd, b, sizeof(b));
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) { a->bad = 1; return NULL; }
        if (memcmp(b, a->expected + offset, (size_t)n)) a->bad = 1;
        offset += (size_t)n;
    }
    return NULL;
}

static void nonblocking_short_write_test(void) {
    int sv[2], sndbuf = 1024;
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv)) die("socketpair for short write test");
    if (setsockopt(sv[0], SOL_SOCKET, SO_SNDBUF, &sndbuf, sizeof(sndbuf))) die("set send buffer");
    int flags = fcntl(sv[0], F_GETFL, 0);
    if (flags < 0 || fcntl(sv[0], F_SETFL, flags | O_NONBLOCK)) die("set nonblocking writer");
    const size_t total = 65536;
    uint8_t *payload = malloc(total);
    if (!payload) die("allocate write payload");
    for (size_t i = 0; i < total; ++i) payload[i] = (uint8_t)(i * 31u + 7u);
    ssize_t first = write(sv[0], payload, total);
    if (first <= 0 || (size_t)first >= total) die("socket did not force a short write");
    errno = 0;
    if (write(sv[0], payload + first, total - (size_t)first) >= 0 ||
        (errno != EAGAIN && errno != EWOULDBLOCK))
        die("nonblocking retry precondition did not reach EAGAIN");
    struct drain_args args = {.fd = sv[1], .expected = payload, .length = total};
    pthread_t reader;
    if (pthread_create(&reader, NULL, drain_socket, &args)) die("start socket drainer");
    running = 1;
    if (write_full(sv[0], payload + first, total - (size_t)first)) die("write_full after EAGAIN");
    if (pthread_join(reader, NULL) || args.bad) die("short-write payload mismatch");
    free(payload); close(sv[0]); close(sv[1]);
}

int main(int argc, char **argv) {
    if (argc != 2) die("expected unit case name");
    if (!strcmp(argv[1], "parser")) parser_test();
    else if (!strcmp(argv[1], "queue")) queue_test();
    else if (!strcmp(argv[1], "ibs")) ibs_state_test();
    else if (!strcmp(argv[1], "short-write")) nonblocking_short_write_test();
    else die("unknown unit case");
    return 0;
}
'''


class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._build_tmp = tempfile.TemporaryDirectory(prefix="s22-bt-bridge-tests-")
        cls.BINARY = os.path.join(cls._build_tmp.name, "bt-h4-ibs-bridge")
        cls.UNIT_BINARY = os.path.join(cls._build_tmp.name, "bt-h4-ibs-bridge-unit")
        source = os.path.join(ROOT, "tools/hardware/bt-h4-ibs-bridge.c")
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
                        source, "-o", cls.BINARY, "-lutil"], check=True)
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
                        "-pthread", "-I", ROOT, "-x", "c", "-", "-o", cls.UNIT_BINARY,
                        "-lutil", "-pthread"], input=UNIT_SOURCE, text=True, check=True)

    @classmethod
    def tearDownClass(cls):
        cls._build_tmp.cleanup()

    def test_fragmented_h4_command_and_event(self):
        proc, virtual, physical_master, physical_slave = start_bridge(self)
        try:
            for byte in COMMAND:
                os.write(virtual, bytes([byte]))
                if byte != COMMAND[-1]:
                    assert_quiet(self, physical_master)
            self.assertEqual(read_exact(physical_master, 1), b"\xfd")
            os.write(physical_master, b"\xfc")
            self.assertEqual(read_exact(physical_master, len(COMMAND)), COMMAND)
            for byte in EVENT:
                os.write(physical_master, bytes([byte]))
                if byte != EVENT[-1]:
                    assert_quiet(self, virtual)
            self.assertEqual(read_exact(virtual, len(EVENT)), EVENT)
        finally:
            stop_bridge(proc, virtual, physical_master, physical_slave)

    def test_queue_bound_fails_closed(self):
        proc, virtual, physical_master, physical_slave = start_bridge(self)
        try:
            # First command waits for IBS ACK; no UART credit is returned, so
            # the ninth queued command must be rejected at MAX_PENDING=8.
            burst = COMMAND * 9
            self.assertEqual(os.write(virtual, burst), len(burst))
            self.assertEqual(read_exact(physical_master, 1), b"\xfd")
            self.assertIsNotNone(proc.wait(timeout=2))
            assert_quiet(self, physical_master, timeout=0.02)
        finally:
            return_code, stdout, _ = stop_bridge(
                proc, virtual, physical_master, physical_slave
            )
        self.assertNotEqual(return_code, 0)
        self.assertEqual(
            stdout.count("bridge_hci_command="), 8,
            "expected eight accepted commands before the ninth overflowed the queue",
        )
        self.assertIn("bridge_queue_overflow=1 queued=8", stdout)
        self.assertIn("pty_cleanup_ioctl_result=0", stdout)

    def test_invalid_packet_type_fails_closed(self):
        proc, virtual, physical_master, physical_slave = start_bridge(self)
        try:
            os.write(virtual, b"\x06")
            self.assertNotEqual(proc.wait(timeout=2), 0)
            assert_quiet(self, physical_master, timeout=0.02)
        finally:
            stop_bridge(proc, virtual, physical_master, physical_slave)

    def test_oversized_acl_length_fails_closed(self):
        proc, virtual, physical_master, physical_slave = start_bridge(self)
        try:
            os.write(virtual, bytes.fromhex("02 01 00 ff ff"))
            self.assertNotEqual(proc.wait(timeout=2), 0)
            assert_quiet(self, physical_master, timeout=0.02)
        finally:
            stop_bridge(proc, virtual, physical_master, physical_slave)

    def test_wake_sleep_transition_and_following_event(self):
        proc, virtual, physical_master, physical_slave = start_bridge(self)
        try:
            os.write(physical_master, b"\xfd")
            self.assertEqual(read_exact(physical_master, 1), b"\xfc")
            os.write(physical_master, b"\xfe")
            os.write(physical_master, EVENT)
            self.assertEqual(read_exact(virtual, len(EVENT)), EVENT)
        finally:
            stop_bridge(proc, virtual, physical_master, physical_slave)

    def test_wake_retry_timeout_fails_closed_and_detaches(self):
        proc, virtual, physical_master, physical_slave = start_bridge(self)
        try:
            os.write(virtual, COMMAND)
            self.assertEqual(read_exact(physical_master, 3, timeout=1.0), b"\xfd" * 3)
            self.assertNotEqual(proc.wait(timeout=2), 0)
            rest, _ = proc.communicate(timeout=2)
            self.assertIn("pty_cleanup_ioctl_result=0", rest)
        finally:
            # communicate() above already reaped the child; helper is idempotent.
            if proc.poll() is None:
                stop_bridge(proc, virtual, physical_master, physical_slave)
            else:
                os.close(virtual); os.close(physical_master); os.close(physical_slave)

    def test_ack_after_wake_retry_flushes_command(self):
        proc, virtual, physical_master, physical_slave = start_bridge(self)
        try:
            os.write(virtual, COMMAND)
            self.assertEqual(read_exact(physical_master, 1), b"\xfd")
            self.assertEqual(read_exact(physical_master, 1, timeout=0.5), b"\xfd")
            os.write(physical_master, b"\xfc")
            self.assertEqual(read_exact(physical_master, len(COMMAND)), COMMAND)
            assert_quiet(self, physical_master, timeout=0.2)
        finally:
            stop_bridge(proc, virtual, physical_master, physical_slave)

    def test_three_independent_sigterm_runs_report_cleanup(self):
        """Each of three fresh bridge processes receives one SIGTERM."""
        for _ in range(3):
            proc, virtual, physical_master, physical_slave = start_bridge(self)
            proc.send_signal(signal.SIGTERM)
            rest, _ = proc.communicate(timeout=2)
            self.assertNotEqual(proc.returncode, 0)  # orderly signal cancellation
            self.assertIn("pty_cleanup_ioctl_result=0", rest)
            os.close(virtual); os.close(physical_master); os.close(physical_slave)

    def test_parser_fragmentation_and_malformed_lengths(self):
        subprocess.run([self.UNIT_BINARY, "parser"], check=True)

    def test_queue_bound_unit(self):
        subprocess.run([self.UNIT_BINARY, "queue"], check=True, stdout=subprocess.DEVNULL)

    def test_ibs_wake_ack_sleep_unit(self):
        subprocess.run([self.UNIT_BINARY, "ibs"], check=True)

    def test_nonblocking_short_write_recovery(self):
        subprocess.run([self.UNIT_BINARY, "short-write"], check=True)


if __name__ == "__main__":
    unittest.main()
