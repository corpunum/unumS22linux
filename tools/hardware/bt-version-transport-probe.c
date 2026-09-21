/*
 * Bounded raw H4 transport probe for the QCA6490 Bluetooth controller.
 *
 * This is deliberately not an hciattach replacement and never downloads
 * firmware.  It sends one Samsung-QTI GetAppVer transport frame and reports
 * the returned H4 event(s) as opaque bytes.  There is no version decoder or
 * acceptance criterion here: the QTI response fixture is not available in
 * the host evidence.
 *
 * The device path is protected by two explicit opt-ins.  Normal builds and
 * --self-test never open a phone device, change rfkill, or issue an ioctl.
 * A parent agent must review the shared WLAN regulator risk before using the
 * --execute path on a device.
 */

#define _GNU_SOURCE

#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>
#include <signal.h>

#define BT_H4_COMMAND 0x01
#define BT_H4_EVENT 0x04
#define BT_MAX_EVENT_PARAM 255U
#define BT_MAX_EVENT_BYTES (3U + BT_MAX_EVENT_PARAM)
#define BT_MAX_CAPTURE_BYTES 1024U
#define BT_MAX_EVENTS 8U
#define EXPECTED_UART_PATH "/dev/ttySAC1"
#define EXPECTED_UART_MAJOR 204U
#define EXPECTED_UART_MINOR 65U
#define EXPECTED_BTPOWER_PATH "/dev/btpower"
/* Exact read-only baseline contract already used by wifi-bringup-once.py. */
#define CNSS_STATS_PATH "/sys/kernel/debug/cnss/stats"
#define WLAN_MODULE_PATH "/sys/module/wlan"
#define WLAN_INTERFACE_PATH "/sys/class/net/wlan0"
#define CNSS_QUIESCENT_STATE "State: 0x400000(PCI PROBE DONE)"

/* Linux btpower.h: #define BT_CMD_PWR_CTRL 0xbfad. */
#define BT_CMD_PWR_CTRL 0xbfadU
/* QTI PatchDLManager::GetAppVerCmd, exact five-byte H4 command. */
static const uint8_t qti_get_app_version[] = {0x01, 0x00, 0xfc, 0x01, 0x06};
/* Linux btqca.c's different standard QCA app-version payload, for tests/docs. */
static const uint8_t qca_get_app_version[] = {0x01, 0x00, 0xfc, 0x01, 0x19};
static volatile sig_atomic_t stop_requested;

static void request_stop(int signal_number)
{
	(void)signal_number;
	stop_requested = 1;
}

static int install_signal_handlers(struct sigaction *old_int,
					   struct sigaction *old_term)
{
	struct sigaction action;

	memset(&action, 0, sizeof(action));
	action.sa_handler = request_stop;
	sigemptyset(&action.sa_mask);
	if (sigaction(SIGINT, &action, old_int) != 0)
		return -errno;
	if (sigaction(SIGTERM, &action, old_term) != 0) {
		int saved_errno = errno;
		(void)sigaction(SIGINT, old_int, NULL);
		errno = saved_errno;
		return -errno;
	}
	return 0;
}

static int parse_rdev(const char *text, unsigned *major_out, unsigned *minor_out)
{
	char *end;
	unsigned long major_value;
	unsigned long minor_value;

	errno = 0;
	major_value = strtoul(text, &end, 10);
	if (errno || end == text || *end != ':')
		return -EINVAL;
	text = end + 1;
	errno = 0;
	minor_value = strtoul(text, &end, 10);
	if (errno || end == text || *end != '\0' ||
	    major_value > UINT32_MAX || minor_value > UINT32_MAX)
		return -EINVAL;
	*major_out = (unsigned)major_value;
	*minor_out = (unsigned)minor_value;
	return 0;
}

static int require_char_device(const char *path, const char *expected_path,
				       unsigned expected_major,
				       unsigned expected_minor)
{
	struct stat info;

	if (strcmp(path, expected_path) != 0)
		return -EPERM;
	if (lstat(path, &info) != 0)
		return -errno;
	if (!S_ISCHR(info.st_mode))
		return -ENOTTY;
	if (major(info.st_rdev) != expected_major ||
	    minor(info.st_rdev) != expected_minor)
		return -ENODEV;
	return 0;
}

static int validate_wlan_baseline(void)
{
	char state[160];
	ssize_t length;
	int fd;

	if (access(WLAN_MODULE_PATH, F_OK) == 0 ||
	    access(WLAN_INTERFACE_PATH, F_OK) == 0)
		return -EBUSY;
	fd = open(CNSS_STATS_PATH, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
	if (fd < 0)
		return -errno;
	length = read(fd, state, sizeof(state) - 1);
	(void)close(fd);
	if (length < 0)
		return -errno;
	state[length] = '\0';
	while (length > 0 && (state[length - 1] == '\n' ||
				      state[length - 1] == '\r'))
		state[--length] = '\0';
	if (strcmp(state, CNSS_QUIESCENT_STATE) != 0)
		return -EBUSY;
	return 0;
}

struct bt_h4_event {
	uint8_t event_code;
	uint8_t parameter_length;
	uint8_t bytes[BT_MAX_EVENT_BYTES];
	size_t length;
};

/*
 * Parse one complete H4 event.  Return values are deliberately small and
 * transport-oriented: 1 complete, 0 incomplete, -1 wrong packet type,
 * -2 impossible/over-cap length.
 */
static int bt_parse_h4_event(const uint8_t *buf, size_t length,
				     struct bt_h4_event *event)
{
	uint8_t parameter_length;
	size_t total;

	if (length < 1)
		return 0;
	if (buf[0] != BT_H4_EVENT)
		return -1;
	if (length < 3)
		return 0;

	parameter_length = buf[2];
	total = 3U + (size_t)parameter_length;
	if (total > BT_MAX_EVENT_BYTES)
		return -2;
	if (length < total)
		return 0;
	if (event) {
		event->event_code = buf[1];
		event->parameter_length = parameter_length;
		event->length = total;
		memcpy(event->bytes, buf, total);
	}
	return 1;
}

static int64_t monotonic_millis(void)
{
	struct timespec now;

	if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
		return -1;
	return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static int wait_fd(int fd, short events, int64_t deadline)
{
	struct pollfd pfd = {.fd = fd, .events = events};
	int64_t remaining;
	int timeout;
	int rc;

	for (;;) {
		if (stop_requested)
			return -ECANCELED;
		remaining = deadline - monotonic_millis();
		if (remaining <= 0)
			return 0;
		timeout = remaining > INT32_MAX ? INT32_MAX : (int)remaining;
		rc = poll(&pfd, 1, timeout);
		if (rc > 0)
			return (pfd.revents & (POLLERR | POLLHUP | POLLNVAL)) ? -EIO : 1;
		if (rc == 0)
			return 0;
		if (errno != EINTR)
			return -errno;
	}
}

static int write_bounded(int fd, const uint8_t *buf, size_t length,
				 int timeout_ms)
{
	int64_t deadline = monotonic_millis() + timeout_ms;
	size_t written = 0;

	while (written < length) {
		ssize_t rc;
		int ready = wait_fd(fd, POLLOUT, deadline);
		if (ready <= 0)
			return ready == 0 ? -ETIMEDOUT : (ready < 0 ? ready : -EIO);
		rc = write(fd, buf + written, length - written);
		if (rc > 0) {
			written += (size_t)rc;
			continue;
		}
		if (rc < 0 && errno == EINTR)
			continue;
		if (rc < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
			continue;
		return rc < 0 ? -errno : -EIO;
	}
	return 0;
}

static int read_bounded(int fd, uint8_t *buf, size_t length, int64_t deadline)
{
	size_t received = 0;

	while (received < length) {
		ssize_t rc;
		int ready = wait_fd(fd, POLLIN, deadline);
		if (ready <= 0)
			return ready == 0 ? 0 : (ready < 0 ? ready : -EIO);
		rc = read(fd, buf + received, length - received);
		if (rc > 0) {
			received += (size_t)rc;
			continue;
		}
		if (rc < 0 && errno == EINTR)
			continue;
		if (rc < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
			continue;
		return rc < 0 ? -errno : -EIO;
	}
	return 1;
}

static void print_hex(const uint8_t *buf, size_t length)
{
	size_t i;

	for (i = 0; i < length; ++i)
		printf("%s%02x", i ? " " : "", buf[i]);
}

/*
 * Read at most BT_MAX_CAPTURE_BYTES and BT_MAX_EVENTS.  A non-H4 byte is
 * printed and discarded so an unrelated line/noise cannot make this loop
 * unbounded.  Event code and payload are intentionally labelled UNKNOWN.
 */
static int capture_opaque_events(int fd, int timeout_ms)
{
	uint8_t frame[BT_MAX_EVENT_BYTES];
	size_t captured = 0;
	unsigned events = 0;
	int64_t deadline = monotonic_millis() + timeout_ms;
	int result = 0;

	while (captured < BT_MAX_CAPTURE_BYTES && events < BT_MAX_EVENTS) {
		uint8_t type;
		uint8_t header[2];
		struct bt_h4_event event;
		int rc;

		rc = read_bounded(fd, &type, 1, deadline);
		if (rc <= 0)
			return events ? 0 : (rc == 0 ? -ETIMEDOUT : rc);
		captured++;
		if (type != BT_H4_EVENT) {
			printf("h4=UNKNOWN non_event_byte=%02x\n", type);
			continue;
		}
		if (captured + 2U > BT_MAX_CAPTURE_BYTES)
			return -EOVERFLOW;

		frame[0] = type;
		rc = read_bounded(fd, header, sizeof(header), deadline);
		if (rc <= 0)
			return events ? 0 : (rc == 0 ? -ETIMEDOUT : rc);
		captured += sizeof(header);
		frame[1] = header[0];
		frame[2] = header[1];
		if (captured + header[1] > BT_MAX_CAPTURE_BYTES)
			return -EOVERFLOW;
		rc = read_bounded(fd, frame + 3, header[1], deadline);
		if (rc <= 0)
			return events ? 0 : (rc == 0 ? -ETIMEDOUT : rc);
		captured += header[1];

		rc = bt_parse_h4_event(frame, 3U + header[1], &event);
		if (rc != 1)
			return -EPROTO;
		printf("h4=UNKNOWN event_code=%02x parameter_length=%u raw=",
		       event.event_code, event.parameter_length);
		print_hex(event.bytes, event.length);
		putchar('\n');
		events++;
	}
	if (!events)
		result = -EOVERFLOW;
	return result;
}

static int configure_uart(int fd, struct termios *saved, bool *saved_valid)
{
	struct termios tty;

	*saved_valid = false;
	if (tcgetattr(fd, saved) != 0)
		return -errno;
	*saved_valid = true;
	if (stop_requested)
		return -ECANCELED;
	tty = *saved;
	cfmakeraw(&tty);
	cfsetispeed(&tty, B115200);
	cfsetospeed(&tty, B115200);
	tty.c_cflag |= CLOCAL | CREAD;
#ifdef CRTSCTS
	tty.c_cflag |= CRTSCTS;
#endif
	tty.c_cc[VMIN] = 0;
	tty.c_cc[VTIME] = 0;
	if (tcsetattr(fd, TCSANOW, &tty) != 0) {
		(void)tcsetattr(fd, TCSANOW, saved);
		return -errno;
	}
	if (tcflush(fd, TCIOFLUSH) != 0) {
		int saved_errno = errno;
		(void)tcsetattr(fd, TCSANOW, saved);
		errno = saved_errno;
		return -errno;
	}
	return 0;
}

static void stock_uart_cleanup(int fd, const struct termios *saved)
{
	int modem_bits;

	/* Matches the QTI HAL's Disconnect sequence: assert RTS, then flush. */
	if (ioctl(fd, TIOCMGET, &modem_bits) == 0) {
		modem_bits |= TIOCM_RTS;
		(void)ioctl(fd, TIOCMSET, &modem_bits);
	}
	(void)tcflush(fd, TCIOFLUSH);
	/* QTI DeInitTransport issues this private cleanup ioctl and ignores rc. */
	(void)ioctl(fd, 0x54ee);
	(void)tcsetattr(fd, TCSANOW, saved);
}

static void restore_uart_termios(int fd, const struct termios *saved)
{
	(void)tcsetattr(fd, TCSANOW, saved);
}

static int power_control(int fd, unsigned long state)
{
	int rc = ioctl(fd, BT_CMD_PWR_CTRL, state);
	return rc == 0 ? 0 : -errno;
}

static int run_device_probe(const char *uart_path, const char *power_path,
				    unsigned power_major, unsigned power_minor,
				    int timeout_ms)
{
	struct termios saved;
	struct sigaction old_int;
	struct sigaction old_term;
	int uart = -1;
	int power = -1;
	int rc = 1;
	bool uart_saved = false;
	bool uart_configured = false;
	bool power_opened = false;
	bool power_may_be_on = false;
	bool signals_installed = false;
	int validation;

	validation = install_signal_handlers(&old_int, &old_term);
	if (validation != 0) {
		fprintf(stderr, "install signal handlers failed rc=%d (%s)\n",
			validation, strerror(-validation));
		return validation;
	}
	signals_installed = true;
	validation = require_char_device(uart_path, EXPECTED_UART_PATH,
					 EXPECTED_UART_MAJOR, EXPECTED_UART_MINOR);
	if (validation != 0) {
		fprintf(stderr, "UART identity rejected rc=%d (%s)\n", validation,
			strerror(-validation));
		rc = validation;
		goto cleanup;
	}
	validation = require_char_device(power_path, EXPECTED_BTPOWER_PATH,
					 power_major, power_minor);
	if (validation != 0) {
		fprintf(stderr, "btpower identity rejected rc=%d (%s)\n", validation,
			strerror(-validation));
		rc = validation;
		goto cleanup;
	}
	validation = validate_wlan_baseline();
	if (validation != 0) {
		fprintf(stderr, "WLAN baseline rejected rc=%d (%s)\n", validation,
			strerror(-validation));
		rc = validation;
		goto cleanup;
	}

	power = open(power_path, O_RDWR | O_CLOEXEC);
	if (power < 0) {
		fprintf(stderr, "open btpower %s: %s\n", power_path, strerror(errno));
		goto cleanup;
	}
	power_opened = true;
	uart = open(uart_path, O_RDWR | O_NOCTTY | O_CLOEXEC | O_NONBLOCK);
	if (uart < 0) {
		fprintf(stderr, "open UART %s: %s\n", uart_path, strerror(errno));
		goto cleanup;
	}
	if (configure_uart(uart, &saved, &uart_saved) != 0) {
		fprintf(stderr, "configure UART failed\n");
		goto cleanup;
	}
	uart_configured = true;

	/* The explicit power path is dedicated BT control, but its board rail is
	 * also named vreg_wlan on r0s.  The caller must opt into that risk. */
	if (power_control(power, 1) != 0) {
		fprintf(stderr, "btpower on ioctl failed\n");
		power_may_be_on = true;
		goto cleanup;
	}
	power_may_be_on = true;
	if (write_bounded(uart, qti_get_app_version,
			  sizeof(qti_get_app_version), timeout_ms) != 0) {
		fprintf(stderr, "bounded H4 command write failed\n");
		goto cleanup;
	}
	printf("sent=qti_get_app_version raw=");
	print_hex(qti_get_app_version, sizeof(qti_get_app_version));
	putchar('\n');
	rc = capture_opaque_events(uart, timeout_ms);
	if (rc != 0)
		fprintf(stderr, "opaque H4 capture rc=%d (%s)\n", rc,
			strerror(-rc));

cleanup:
	if (uart_saved) {
		if (uart_configured)
			stock_uart_cleanup(uart, &saved);
		else
			restore_uart_termios(uart, &saved);
	}
	if (uart >= 0)
		close(uart);
	if (power_may_be_on && power_opened) {
		int off_rc = power_control(power, 0);
		if (off_rc != 0) {
			fprintf(stderr, "btpower off ioctl failed rc=%d (%s)\n",
				off_rc, strerror(-off_rc));
			if (rc == 0)
				rc = off_rc;
		}
	}
	if (power >= 0)
		close(power);
	if (signals_installed) {
		(void)sigaction(SIGINT, &old_int, NULL);
		(void)sigaction(SIGTERM, &old_term, NULL);
	}
	if (stop_requested && rc == 0)
		rc = -ECANCELED;
	return rc;
}

static int self_test(void)
{
	static const uint8_t valid[] = {0x04, 0xff, 0x02, 0x19, 0x02};
	static const uint8_t non_event[] = {0x01, 0x00, 0xfc, 0x01, 0x06};
	struct bt_h4_event event;

	assert(sizeof(qti_get_app_version) == 5);
	assert(qti_get_app_version[0] == BT_H4_COMMAND);
	assert(qti_get_app_version[1] == 0x00 && qti_get_app_version[2] == 0xfc);
	assert(qti_get_app_version[4] == 0x06);
	assert(memcmp(qca_get_app_version, "\x01\x00\xfc\x01\x19", 5) == 0);
	assert(bt_parse_h4_event(valid, sizeof(valid), &event) == 1);
	assert(event.event_code == 0xff && event.parameter_length == 2);
	assert(event.length == sizeof(valid));
	assert(bt_parse_h4_event(valid, 2, NULL) == 0);
	assert(bt_parse_h4_event(non_event, sizeof(non_event), NULL) == -1);
	assert(bt_parse_h4_event((const uint8_t[]){0x04, 0xff, 0xff}, 3,
					NULL) == 0);
	puts("bt-version-transport self-test: PASS (host-only parser/constants)");
	return 0;
}

static void usage(const char *argv0)
{
	fprintf(stderr,
		"usage: %s --self-test\n"
		"       %s --execute --allow-shared-wlan-rail --uart PATH "
		"--btpower PATH --btpower-rdev MAJOR:MINOR [--timeout-ms N]\n"
		"device identity: UART must be /dev/ttySAC1 (204:65); "
		"btpower rdev must come from a read-only preflight\n"
		"WLAN baseline: no /sys/module/wlan or wlan0 and CNSS stats "
		"must equal 'State: 0x400000(PCI PROBE DONE)'\n"
		"default: refuse device access; no hciattach/rfkill/firmware load\n",
		argv0, argv0);
}

int main(int argc, char **argv)
{
	const char *uart_path = NULL;
	const char *power_path = NULL;
	const char *power_rdev = NULL;
	unsigned power_major = 0;
	unsigned power_minor = 0;
	int timeout_ms = 1000;
	bool execute = false;
	bool allow_shared_wlan_rail = false;
	int i;

	if (argc == 2 && strcmp(argv[1], "--self-test") == 0)
		return self_test();
	for (i = 1; i < argc; ++i) {
		if (strcmp(argv[i], "--execute") == 0)
			execute = true;
		else if (strcmp(argv[i], "--allow-shared-wlan-rail") == 0)
			allow_shared_wlan_rail = true;
		else if (strcmp(argv[i], "--uart") == 0 && i + 1 < argc)
			uart_path = argv[++i];
		else if (strcmp(argv[i], "--btpower") == 0 && i + 1 < argc)
			power_path = argv[++i];
		else if (strcmp(argv[i], "--btpower-rdev") == 0 && i + 1 < argc)
			power_rdev = argv[++i];
		else if (strcmp(argv[i], "--timeout-ms") == 0 && i + 1 < argc)
			timeout_ms = atoi(argv[++i]);
		else {
			usage(argv[0]);
			return 2;
		}
	}
	if (!execute || !allow_shared_wlan_rail || !uart_path || !power_path ||
	    !power_rdev || timeout_ms < 1 || timeout_ms > 10000 ||
	    parse_rdev(power_rdev, &power_major, &power_minor) != 0) {
		usage(argv[0]);
		return 2;
	}
	return run_device_probe(uart_path, power_path, power_major, power_minor,
					timeout_ms);
}
