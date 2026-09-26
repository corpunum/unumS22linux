#!/usr/bin/env python3
"""Host-only PTY and unit regressions for the QCA H4/IBS bridge."""
import os
import errno
import pty
import select
import signal
import subprocess
import sys
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

LIFECYCLE_SOURCE = r'''#define S22_BT_BRIDGE_EMBED 1
#define ioctl s22_bt_test_ioctl
#define socket s22_bt_test_socket
#include "tools/hardware/bt-h4-ibs-bridge.c"
#include <fcntl.h>
#include <pthread.h>
#include <stdarg.h>
#include <sys/socket.h>

static unsigned attach_calls, proto_calls, device_calls, detach_calls;
static unsigned raw_socket_calls, info_ioctl_calls;
static int inject_overflow, fail_initial_attach, fail_setproto;
static int fail_device_lookup, fail_detach, injection_failed;

int s22_bt_test_ioctl(int fd, unsigned long request, ...)
{
    va_list args;
    int result = 0;
    va_start(args, request);
    if (request == TIOCSETD) {
        int *line = va_arg(args, int *);
        if (*line == N_HCI) {
            static const uint8_t command[] = {1, 3, 0x0c, 0};
            attach_calls++;
            if (fail_initial_attach) {
                errno = ENODEV;
                result = -1;
            } else {
                for (unsigned i = 0; inject_overflow && i < 9; ++i)
                    if (write(fd, command, sizeof(command)) != (ssize_t)sizeof(command))
                        injection_failed = 1;
            }
        } else if (*line == 0) {
            detach_calls++;
            if (fail_detach) {
                errno = EIO;
                result = -1;
            }
        }
    } else if (request == HCIUARTSETPROTO) {
        (void)va_arg(args, unsigned long);
        proto_calls++;
        if (fail_setproto) {
            errno = EPROTO;
            result = -1;
        }
    } else if (request == HCIUARTGETDEVICE) {
        (void)va_arg(args, unsigned long);
        device_calls++;
        if (fail_device_lookup) {
            errno = ENODEV;
            result = -1;
        } else {
            result = 7;
        }
    } else if (request == _IOR('H', 211, int)) {
        void *info = va_arg(args, void *);
        (void)info;
        info_ioctl_calls++;
    }
    va_end(args);
    return result;
}

int s22_bt_test_socket(int domain, int type, int protocol)
{
    (void)type;
    (void)protocol;
    raw_socket_calls++;
    if (domain != AF_BLUETOOTH) {
        errno = EAFNOSUPPORT;
        return -1;
    }
    return open("/dev/null", O_RDONLY | O_CLOEXEC);
}

static void *send_term(void *unused)
{
    struct timespec delay = {.tv_sec = 0, .tv_nsec = 30000000};
    (void)unused;
    nanosleep(&delay, NULL);
    kill(getpid(), SIGTERM);
    return NULL;
}

int main(int argc, char **argv)
{
    int pair[2], rc, expected_abort;
    pthread_t stopper;
    int have_stopper = 0;
    if (argc != 2) return 2;
    if (!strcmp(argv[1], "overflow")) inject_overflow = 1;
    if (!strcmp(argv[1], "initial-attach-fail")) fail_initial_attach = 1;
    if (!strcmp(argv[1], "setproto-fail") ||
        !strcmp(argv[1], "setproto-fail-detach-fail")) fail_setproto = 1;
    if (!strcmp(argv[1], "attach-fail")) fail_device_lookup = 1;
    if (!strcmp(argv[1], "detach-fail") ||
        !strcmp(argv[1], "setproto-fail-detach-fail")) fail_detach = 1;
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, pair)) return 2;
    if (!strcmp(argv[1], "malformed") && write(pair[1], "\x06", 1) != 1) return 2;
    if (!strcmp(argv[1], "term")) {
        if (pthread_create(&stopper, NULL, send_term, NULL)) return 2;
        have_stopper = 1;
    }
    if (strcmp(argv[1], "clean") && strcmp(argv[1], "malformed") &&
        strcmp(argv[1], "overflow") && strcmp(argv[1], "term") &&
        strcmp(argv[1], "attach-fail") && strcmp(argv[1], "initial-attach-fail") &&
        strcmp(argv[1], "setproto-fail") && strcmp(argv[1], "detach-fail") &&
        strcmp(argv[1], "setproto-fail-detach-fail")) return 2;
    rc = s22_bridge_run(pair[0],
                        (!strcmp(argv[1], "clean") || !strcmp(argv[1], "detach-fail"))
                        ? 20 : 1000);
    int errno_after = errno;
    if (have_stopper && pthread_join(stopper, NULL)) return 2;
    close(pair[0]);
    close(pair[1]);
    expected_abort = strcmp(argv[1], "clean") != 0;
    unsigned expected_detach = fail_initial_attach ? 0 : 1;
    unsigned expected_proto = fail_initial_attach ? 0 : 1;
    unsigned expected_device = fail_initial_attach || fail_setproto ? 0 : 1;
    printf("lifecycle socket_calls=%u info_ioctl_calls=%u attach_calls=%u "
           "proto_calls=%u device_calls=%u detach_calls=%u rc=%d errno_after=%d\n",
           raw_socket_calls, info_ioctl_calls, attach_calls, proto_calls,
           device_calls, detach_calls, rc, errno_after);
    if (injection_failed || raw_socket_calls || info_ioctl_calls ||
        attach_calls != 1 || detach_calls != expected_detach ||
        proto_calls != expected_proto || device_calls != expected_device ||
        (expected_abort ? rc >= 0 : rc != 0)) return 1;
    return 0;
}
'''


class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._build_tmp = tempfile.TemporaryDirectory(prefix="s22-bt-bridge-tests-")
        cls.BINARY = os.path.join(cls._build_tmp.name, "bt-h4-ibs-bridge")
        cls.UNIT_BINARY = os.path.join(cls._build_tmp.name, "bt-h4-ibs-bridge-unit")
        cls.LIFECYCLE_BINARY = os.path.join(cls._build_tmp.name, "bt-h4-ibs-bridge-lifecycle")
        source = os.path.join(ROOT, "tools/hardware/bt-h4-ibs-bridge.c")
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
                        source, "-o", cls.BINARY, "-lutil"], check=True)
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
                        "-pthread", "-I", ROOT, "-x", "c", "-", "-o", cls.UNIT_BINARY,
                        "-lutil", "-pthread"], input=UNIT_SOURCE, text=True, check=True)
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
                        "-pthread", "-I", ROOT, "-x", "c", "-", "-o", cls.LIFECYCLE_BINARY,
                        "-lutil", "-pthread"], input=LIFECYCLE_SOURCE, text=True, check=True)

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
        self.assertIn("pty_restore_ioctl_result=0", stdout)

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
            self.assertIn("pty_restore_ioctl_result=0", rest)
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
            self.assertIn("pty_restore_ioctl_result=0", rest)
            os.close(virtual); os.close(physical_master); os.close(physical_slave)

    def test_parser_fragmentation_and_malformed_lengths(self):
        subprocess.run([self.UNIT_BINARY, "parser"], check=True)

    def test_queue_bound_unit(self):
        subprocess.run([self.UNIT_BINARY, "queue"], check=True, stdout=subprocess.DEVNULL)

    def test_ibs_wake_ack_sleep_unit(self):
        subprocess.run([self.UNIT_BINARY, "ibs"], check=True)

    def test_nonblocking_short_write_recovery(self):
        subprocess.run([self.UNIT_BINARY, "short-write"], check=True)

    def run_lifecycle_case(self, case):
        return subprocess.run([self.LIFECYCLE_BINARY, case], check=False,
                              capture_output=True, text=True, timeout=3)

    def assert_lifecycle_detached_without_raw_socket(self, result, *, registered, ran_bridge):
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("lifecycle socket_calls=0 info_ioctl_calls=0", result.stdout)
        self.assertIn("attach_calls=1", result.stdout)
        self.assertIn("device_calls=1", result.stdout)
        self.assertIn("detach_calls=1", result.stdout)
        self.assertEqual(result.stdout.count("pty_cleanup_ioctl_result="), 1)
        self.assertNotIn("bridge_hci_info", result.stdout)
        self.assertEqual("bridge_result=" in result.stdout, ran_bridge)
        if registered:
            self.assertIn("bridge_registered_hci=7", result.stdout)
        else:
            self.assertNotIn("bridge_registered_hci=7", result.stdout)

    def test_embedded_clean_registration_uses_uart_device_index_only(self):
        result = self.run_lifecycle_case("clean")
        self.assert_lifecycle_detached_without_raw_socket(
            result, registered=True, ran_bridge=True
        )
        self.assertIn("rc=0", result.stdout)

    def test_embedded_attach_failure_detaches_once_without_socket(self):
        result = self.run_lifecycle_case("attach-fail")
        self.assert_lifecycle_detached_without_raw_socket(
            result, registered=False, ran_bridge=False
        )
        self.assertIn("proto_calls=1 device_calls=1", result.stdout)

    def test_embedded_initial_line_discipline_failure_does_not_detach(self):
        result = self.run_lifecycle_case("initial-attach-fail")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("attach_calls=1 proto_calls=0 device_calls=0 detach_calls=0 rc=-1", result.stdout)
        self.assertIn("pty_cleanup_attempted=0", result.stdout)
        self.assertNotIn("pty_cleanup_ioctl_result=", result.stdout)
        self.assertIn(f"errno_after={errno.ENODEV}", result.stdout)
        self.assertNotIn("bridge_result=", result.stdout)

    def test_embedded_setproto_failure_detaches_once_and_preserves_errno(self):
        result = self.run_lifecycle_case("setproto-fail")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("attach_calls=1 proto_calls=1 device_calls=0 detach_calls=1 rc=-1", result.stdout)
        self.assertIn("pty_cleanup_ioctl_result=0", result.stdout)
        self.assertIn(f"errno_after={errno.EPROTO}", result.stdout)
        self.assertNotIn("bridge_result=", result.stdout)

    def test_embedded_detach_failure_is_reported_and_changes_clean_status(self):
        result = self.run_lifecycle_case("detach-fail")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("attach_calls=1 proto_calls=1 device_calls=1 detach_calls=1 rc=-1", result.stdout)
        self.assertIn("pty_cleanup_ioctl_result=-1", result.stdout)
        self.assertIn(f"pty_detach_failed errno={errno.EIO}", result.stderr)
        self.assertIn(f"errno_after={errno.EIO}", result.stdout)
        self.assertIn("bridge_result=0", result.stdout)

    def test_embedded_detach_failure_does_not_replace_primary_errno(self):
        result = self.run_lifecycle_case("setproto-fail-detach-fail")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("attach_calls=1 proto_calls=1 device_calls=0 detach_calls=1 rc=-1", result.stdout)
        self.assertIn("pty_cleanup_ioctl_result=-1", result.stdout)
        self.assertIn(f"pty_detach_failed errno={errno.EIO}", result.stderr)
        self.assertIn(f"errno_after={errno.EPROTO}", result.stdout)
        self.assertNotIn("bridge_result=", result.stdout)

    def test_embedded_uart_error_and_queue_overflow_unwind_without_socket(self):
        for case in ("malformed", "overflow"):
            with self.subTest(case=case):
                result = self.run_lifecycle_case(case)
                self.assert_lifecycle_detached_without_raw_socket(
                    result, registered=True, ran_bridge=True
                )
                if case == "overflow":
                    self.assertIn("bridge_queue_overflow=1 queued=8", result.stdout)

    def test_embedded_signal_unwind_detaches_once_without_socket(self):
        result = self.run_lifecycle_case("term")
        self.assert_lifecycle_detached_without_raw_socket(
            result, registered=True, ran_bridge=True
        )

    def test_runner_blocks_attachment_until_exact_readback_review_and_authorization(self):
        runner = os.path.join(ROOT, "tools/hardware/run-bt-hci-bridge-once.py")
        command = [sys.executable]
        if not __debug__:
            command.append("-O")
        command.extend([runner, "bt-hci-registration-20260926", "--execute"])
        result = subprocess.run(command, check=False,
                                capture_output=True, text=True, timeout=3)
        if __debug__:
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("prior raw-HCI socket authorization did not cover", result.stderr)
            self.assertIn("automatic controller initialization/power-on", result.stderr)
            self.assertIn("separate owner authorization", result.stderr)
            self.assertIn("does not bound kernel detach", result.stderr)
        else:
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("optimized Python is refused", result.stderr)


if __name__ == "__main__":
    unittest.main()
