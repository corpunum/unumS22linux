/* Host-only PatchVerReq identity candidate. No baud, firmware, NVM or HCI. */
#define main bt_version_probe_main
#include "bt-version-transport-probe.c"
#undef main

#define VERSION_TIMEOUT_MS 2000
#define MAX_OPAQUE_EVENTS 16U
static const uint8_t patch_version_cmd[] = {0x01, 0x00, 0xfc, 0x01, 0x19};

static int read_event(int fd, uint8_t *frame, size_t *length, int64_t deadline)
{
	uint8_t h[3]; int rc = read_bounded(fd, h, sizeof(h), deadline);
	if (rc <= 0) return rc == 0 ? -ETIMEDOUT : rc;
	if (h[0] != BT_H4_EVENT || (size_t)h[2] + 3U > BT_MAX_EVENT_BYTES) return -EPROTO;
	*length = (size_t)h[2] + 3U; memcpy(frame, h, 3);
	rc = read_bounded(fd, frame + 3, h[2], deadline);
	return rc <= 0 ? (rc == 0 ? -ETIMEDOUT : rc) : 0;
}

static int command_complete_version(int fd, uint8_t *frame, size_t *length)
{
	int64_t deadline = monotonic_millis() + VERSION_TIMEOUT_MS; unsigned skipped = 0;
	for (;;) {
		struct bt_h4_event event; int rc = read_event(fd, frame, length, deadline);
		if (rc) return rc;
		rc = bt_parse_h4_event(frame, *length, &event);
		if (rc != 1 || event.event_code != 0x0e) { if (++skipped > MAX_OPAQUE_EVENTS) return -EPROTO; continue; }
		if (event.parameter_length < 5 || frame[4] != 0 || frame[5] != 0xfc) return -EPROTO;
		if (frame[7] != 0x06) { if (++skipped > MAX_OPAQUE_EVENTS) return -EAGAIN; continue; }
		return frame[6] == 0 ? 0 : -EIO;
	}
}

static int validate_version_identity(const uint8_t *frame, size_t length)
{
	struct bt_h4_event event; char ascii[256]; size_t n;
	const char *prefix = "Release 19.0201 PF=QCA6490 BUILD=BTFW.HSP.2.1-00124-ROM-1";
	if (length != 95 || bt_parse_h4_event(frame, length, &event) != 1 || event.event_code != 0x0e ||
	    event.parameter_length < 5 || frame[4] || frame[5] != 0xfc || frame[6] || frame[7] != 6) return -EPROTO;
	n = event.parameter_length - 5U; if (n >= sizeof(ascii)) n = sizeof(ascii) - 1U;
	memcpy(ascii, frame + 8, n); ascii[n] = 0;
	if (ascii[0] == 'V') memmove(ascii, ascii + 1, n);
	return !strncmp(ascii, prefix, strlen(prefix)) && strstr(ascii, "SoCID=0x400C0210") ? 0 : -EPROTO;
}

static int run_probe(const char *uart_path, const char *power_path,
			     unsigned power_major, unsigned power_minor, bool live)
{
	struct termios saved; struct sigaction old_int, old_term;
	uint8_t frame[BT_MAX_EVENT_BYTES]; size_t length = 0;
	int uart = -1, power = -1, rc = 1; bool saved_valid = false, configured = false;
	bool powered = false, power_opened = false, signals = false;
	if (install_signal_handlers(&old_int, &old_term)) return 1;
	signals = true;
	if (require_char_device(uart_path, EXPECTED_UART_PATH, EXPECTED_UART_MAJOR, EXPECTED_UART_MINOR) ||
	    require_char_device(power_path, EXPECTED_BTPOWER_PATH, power_major, power_minor) ||
	    (live ? validate_live_wlan_baseline() : validate_wlan_baseline())) goto cleanup;
	power = open(power_path, O_RDWR | O_CLOEXEC); if (power < 0) goto cleanup; power_opened = true;
	{ struct stat st; if (fstat(power, &st) || !S_ISCHR(st.st_mode) || major(st.st_rdev) != power_major || minor(st.st_rdev) != power_minor) goto cleanup; }
	uart = open(uart_path, O_RDWR | O_NOCTTY | O_CLOEXEC | O_NONBLOCK); if (uart < 0) goto cleanup;
	{ struct stat st; if (fstat(uart, &st) || !S_ISCHR(st.st_mode) || major(st.st_rdev) != EXPECTED_UART_MAJOR || minor(st.st_rdev) != EXPECTED_UART_MINOR || ioctl(uart, TIOCEXCL)) goto cleanup; }
	if (configure_uart(uart, &saved, &saved_valid)) goto cleanup;
	configured = true;
	if (power_control(power, 1)) goto cleanup;
	powered = true;
	if (live && check_vote(1)) goto cleanup;
	if (write_bounded(uart, qti_get_app_version, sizeof(qti_get_app_version), VERSION_TIMEOUT_MS) ||
	    command_complete_version(uart, frame, &length) || validate_version_identity(frame, length)) { rc = -EPROTO; goto cleanup; }
	if (write_bounded(uart, patch_version_cmd, sizeof(patch_version_cmd), VERSION_TIMEOUT_MS)) { rc = -EIO; goto cleanup; }
	puts("sent=patch_version raw=01 00 fc 01 19");
	/* Vendor reply and Command Complete need not echo sub-op 0x19. Preserve
	 * every bounded event for source-matched offline decoding. */
	rc = capture_opaque_events(uart, VERSION_TIMEOUT_MS);
cleanup:
	if (saved_valid) { int restore = configured ? stock_uart_cleanup(uart, &saved) : restore_uart_termios(uart, &saved); if (restore && rc == 0) rc = -EIO; }
	if (uart >= 0) { (void)ioctl(uart, TIOCNXCL); close(uart); }
	if (powered && power_opened) { int off = power_control(power, 0); if (off && rc == 0) rc = off; if (live && check_vote(0)) rc = -EIO; }
	if (power >= 0) close(power);
	if (signals) { (void)sigaction(SIGINT, &old_int, NULL); (void)sigaction(SIGTERM, &old_term, NULL); }
	if (stop_requested && rc == 0) rc = -ECANCELED;
	return rc;
}

static int patch_self_test(void)
{
	uint8_t version[95] = {0x04, 0x0e, 0x5c, 0x01, 0x00, 0xfc, 0x00, 0x06};
	uint8_t event[] = {0x04, 0xff, 0x02, 0x00, 0x02};
	const char *text = "Release 19.0201 PF=QCA6490 BUILD=BTFW.HSP.2.1-00124-ROM-1 SoCID=0x400C0210";
	int p[2];
	memcpy(version + 8, text, strlen(text));
	if (validate_version_identity(version, sizeof(version)) || pipe(p)) return 1;
	if (write(p[1], event, sizeof(event)) != (ssize_t)sizeof(event) ||
	    capture_opaque_events(p[0], 20)) return 1;
	close(p[0]); close(p[1]);
	version[5] = 0xfb;
	if (validate_version_identity(version, sizeof(version)) == 0) return 1;
	version[5] = 0xfc; version[6] = 1;
	if (validate_version_identity(version, sizeof(version)) == 0 ||
	    validate_version_identity(version, sizeof(version)-1) == 0) return 1;
	puts("bt-qca6490 patch-version probe self-test: PASS"); return 0;
}

int main(int argc, char **argv)
{
	const char *uart = NULL, *power = NULL, *rdev = NULL; unsigned maj, min;
	bool execute = false, allow = false, live = false;
	if (argc == 2 && !strcmp(argv[1], "--self-test")) return patch_self_test();
	if (argc == 2 && !strcmp(argv[1], "--check-live-wlan"))
		return validate_live_wlan_baseline() ? 1 : 0;
	for (int i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "--execute")) execute = true;
		else if (!strcmp(argv[i], "--allow-shared-wlan-rail")) allow = true;
		else if (!strcmp(argv[i], "--live-wlan-vote")) live = true;
		else if (!strcmp(argv[i], "--uart") && i + 1 < argc) uart = argv[++i];
		else if (!strcmp(argv[i], "--btpower") && i + 1 < argc) power = argv[++i];
		else if (!strcmp(argv[i], "--btpower-rdev") && i + 1 < argc) rdev = argv[++i];
		else return 2;
	}
	if (!execute || !allow || !uart || !power || !rdev || parse_rdev(rdev, &maj, &min)) return 2;
	return run_probe(uart, power, maj, min, live) ? 1 : 0;
}
