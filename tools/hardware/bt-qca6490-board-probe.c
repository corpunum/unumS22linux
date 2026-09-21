/* Separate board-ID diagnostic; reuse every accepted probe guard verbatim. */
#define main bt_version_probe_main
#include "bt-version-transport-probe.c"
#undef main

#define BOARD_SUBOP 0x23
#define VERSION_TIMEOUT_MS 2000
#define BOARD_TIMEOUT_MS 2000
#define MAX_INTERLEAVED 16U
static const uint8_t qti_board_id[] = {0x01, 0x00, 0xfc, 0x01, BOARD_SUBOP};

static int read_one_event(int fd, uint8_t *frame, size_t *length, int64_t deadline)
{
	uint8_t h[3]; int rc = read_bounded(fd, h, sizeof(h), deadline);
	if (rc <= 0) return rc == 0 ? -ETIMEDOUT : rc;
	if (h[0] != BT_H4_EVENT) return -EPROTO;
	*length = (size_t)h[2] + 3U;
	if (*length > BT_MAX_EVENT_BYTES) return -EOVERFLOW;
	memcpy(frame, h, 3);
	rc = read_bounded(fd, frame + 3, h[2], deadline);
	return rc <= 0 ? (rc == 0 ? -ETIMEDOUT : rc) : 0;
}

static int command_complete_for(int fd, uint8_t subop, uint8_t *frame,
					size_t *length, int timeout_ms)
{
	unsigned skipped = 0; int64_t deadline = monotonic_millis() + timeout_ms;
	for (;;) {
		struct bt_h4_event event;
		int rc = read_one_event(fd, frame, length, deadline);
		if (rc) return rc;
		rc = bt_parse_h4_event(frame, *length, &event);
		if (rc != 1 || event.event_code != 0x0e) {
			if (++skipped > MAX_INTERLEAVED) return -EPROTO;
			continue;
		}
		if (event.parameter_length < 5 || frame[4] != 0x00 || frame[5] != 0xfc)
			return -EPROTO;
		if (frame[7] != subop) {
			if (++skipped > MAX_INTERLEAVED) return -EAGAIN;
			continue;
		}
		return frame[6] == 0 ? 0 : -EIO;
	}
}

static int validate_version_identity(const uint8_t *frame, size_t length)
{
	struct bt_h4_event event; char ascii[256]; size_t n;
	const char *prefix = "Release 19.0201 PF=QCA6490 BUILD=BTFW.HSP.2.1-00124-ROM-1";
	const char *soc = "SoCID=0x400C0210";
	if (length != 95 || bt_parse_h4_event(frame, length, &event) != 1 || event.event_code != 0x0e ||
	    event.parameter_length < 5 || frame[4] != 0x00 || frame[5] != 0xfc ||
	    frame[6] != 0 || frame[7] != 0x06) return -EPROTO;
	n = event.parameter_length - 5U;
	if (n >= sizeof(ascii)) n = sizeof(ascii) - 1U;
	memcpy(ascii, frame + 8, n); ascii[n] = '\0';
	if (ascii[0] == 'V') { memmove(ascii, ascii + 1, n); --n; }
	return !strncmp(ascii, prefix, strlen(prefix)) && strstr(ascii, soc) ? 0 : -EPROTO;
}

static int run_board_probe(const char *uart_path, const char *power_path,
				   unsigned power_major, unsigned power_minor, bool live)
{
	struct termios saved; struct sigaction old_int, old_term;
	uint8_t frame[BT_MAX_EVENT_BYTES]; size_t length = 0;
	int uart = -1, power = -1, rc = 1, validation; bool saved_valid = false;
	bool configured = false, powered = false, signals = false, power_opened = false;
	validation = install_signal_handlers(&old_int, &old_term); if (validation) return validation;
	signals = true; stage_log("validate");
	validation = require_char_device(uart_path, EXPECTED_UART_PATH, EXPECTED_UART_MAJOR, EXPECTED_UART_MINOR);
	if (validation) { rc = validation; goto cleanup; }
	validation = require_char_device(power_path, EXPECTED_BTPOWER_PATH, power_major, power_minor);
	if (validation) { rc = validation; goto cleanup; }
	validation = live ? validate_live_wlan_baseline() : validate_wlan_baseline();
	if (validation) { rc = validation; goto cleanup; }
	power = open(power_path, O_RDWR | O_CLOEXEC); if (power < 0) goto cleanup;
	power_opened = true;
	{ struct stat st; if (fstat(power, &st) || !S_ISCHR(st.st_mode) || major(st.st_rdev) != power_major || minor(st.st_rdev) != power_minor) { rc = -ENODEV; goto cleanup; } }
	uart = open(uart_path, O_RDWR | O_NOCTTY | O_CLOEXEC | O_NONBLOCK); if (uart < 0) goto cleanup;
	{ struct stat st; if (fstat(uart, &st) || !S_ISCHR(st.st_mode) || major(st.st_rdev) != EXPECTED_UART_MAJOR || minor(st.st_rdev) != EXPECTED_UART_MINOR || ioctl(uart, TIOCEXCL)) { rc = -ENODEV; goto cleanup; } }
	if (configure_uart(uart, &saved, &saved_valid)) goto cleanup;
	configured = true;
	if (power_control(power, 1)) goto cleanup; /* failed power-on unwinds its own vote */
	powered = true; /* own the successful vote before any verification can fail */
	if (live && check_vote(1)) goto cleanup;
	stage_log("power_on");
	if (write_bounded(uart, qti_get_app_version, sizeof(qti_get_app_version), VERSION_TIMEOUT_MS) ||
	    command_complete_for(uart, 0x06, frame, &length, VERSION_TIMEOUT_MS) ||
	    validate_version_identity(frame, length)) { rc = -EPROTO; goto cleanup; }
	if (write_bounded(uart, qti_board_id, sizeof(qti_board_id), BOARD_TIMEOUT_MS) ||
	    command_complete_for(uart, BOARD_SUBOP, frame, &length, BOARD_TIMEOUT_MS)) { rc = -EPROTO; goto cleanup; }
	printf("board_reply_len=%zu raw=", length); print_hex(frame, length); putchar('\n'); rc = 0;
cleanup:
	if (saved_valid) { int restore = configured ? stock_uart_cleanup(uart, &saved) : restore_uart_termios(uart, &saved); if (restore && rc == 0) rc = -EIO; }
	if (uart >= 0) { (void)ioctl(uart, TIOCNXCL); close(uart); }
	if (powered && power_opened) { int off = power_control(power, 0); if (off && rc == 0) rc = off; if (live && check_vote(0)) rc = -EIO; }
	if (power >= 0) close(power);
	if (signals) { (void)sigaction(SIGINT, &old_int, NULL); (void)sigaction(SIGTERM, &old_term, NULL); }
	if (stop_requested && rc == 0) rc = -ECANCELED;
	return rc;
}

static int board_self_test(void)
{
	uint8_t frame[BT_MAX_EVENT_BYTES], version_frame[BT_MAX_EVENT_BYTES]; size_t n; int p[2];
	static const uint8_t version[] = {0x04,0x0e,0x5c,0x01,0x00,0xfc,0x00,0x06,0x56,0x52,0x65,0x6c,0x65,0x61,0x73,0x65,0x20,0x31,0x39,0x2e,0x30,0x32,0x30,0x31,0x20,0x50,0x46,0x3d,0x51,0x43,0x41,0x36,0x34,0x39,0x30,0x20,0x42,0x55,0x49,0x4c,0x44,0x3d,0x42,0x54,0x46,0x57,0x2e,0x48,0x53,0x50,0x2e,0x32,0x2e,0x31,0x2d,0x30,0x30,0x31,0x32,0x34,0x2d,0x52,0x4f,0x4d,0x2d,0x31,0x20,0x4c,0x4d,0x50,0x3d,0x30,0x78,0x33,0x38,0x45,0x36,0x20,0x53,0x6f,0x43,0x49,0x44,0x3d,0x30,0x78,0x34,0x30,0x30,0x43,0x30,0x32,0x31,0x30,0x20};
	static const uint8_t async[] = {0x04,0x0f,0x01,0xaa};
	static const uint8_t board[] = {0x04,0x0e,0x06,0x01,0x00,0xfc,0x00,0x23,0x42};
	memcpy(version_frame, version, sizeof(version));
	if (pipe(p) || write(p[1], version_frame, sizeof(version)) != (ssize_t)sizeof(version) || write(p[1], async, sizeof(async)) != (ssize_t)sizeof(async) || write(p[1], board, sizeof(board)) != (ssize_t)sizeof(board)) return 1;
	if (command_complete_for(p[0], 0x06, frame, &n, VERSION_TIMEOUT_MS) || validate_version_identity(frame, n) || command_complete_for(p[0], BOARD_SUBOP, frame, &n, BOARD_TIMEOUT_MS) || n != sizeof(board)) return 1;
	close(p[0]); close(p[1]);
	/* Negative identity and framing cases must fail closed. */
	if (validate_version_identity(version, sizeof(version) - 1U) == 0) return 1;
	memcpy(frame, version, sizeof(version)); frame[5] = 0xfb;
	if (validate_version_identity(frame, sizeof(version)) == 0) return 1;
	memcpy(frame, version, sizeof(version)); frame[6] = 1;
	if (validate_version_identity(frame, sizeof(version)) == 0) return 1;
	memcpy(frame, board, sizeof(board));
	if (pipe(p)) return 1;
	frame[6] = 1;
	if (write(p[1], frame, sizeof(board)) != (ssize_t)sizeof(board) ||
	    command_complete_for(p[0], BOARD_SUBOP, frame, &n, BOARD_TIMEOUT_MS) == 0) return 1;
	close(p[0]); close(p[1]);
	puts("bt-qca6490-board-probe self-test: PASS"); return 0;
}

int main(int argc, char **argv)
{
	const char *uart_path = NULL, *power_path = NULL, *rdev = NULL;
	unsigned major_value, minor_value; bool live = false, execute = false, allow = false;
	int i;
	if (argc == 2 && !strcmp(argv[1], "--self-test")) return board_self_test();
	if (argc == 2 && !strcmp(argv[1], "--check-live-wlan"))
		return validate_live_wlan_baseline() ? 1 : 0;
	for (i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "--execute")) execute = true;
		else if (!strcmp(argv[i], "--allow-shared-wlan-rail")) allow = true;
		else if (!strcmp(argv[i], "--live-wlan-vote")) live = true;
		else if (!strcmp(argv[i], "--uart") && i + 1 < argc) uart_path = argv[++i];
		else if (!strcmp(argv[i], "--btpower") && i + 1 < argc) power_path = argv[++i];
		else if (!strcmp(argv[i], "--btpower-rdev") && i + 1 < argc) rdev = argv[++i];
		else return 2;
	}
	if (!execute || !allow || !uart_path || !power_path || !rdev || parse_rdev(rdev, &major_value, &minor_value)) return 2;
	return run_board_probe(uart_path, power_path, major_value, minor_value, live) ? 1 : 0;
}
