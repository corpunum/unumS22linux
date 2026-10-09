/*
 * Persistent variant of bt-qca6490-hci-bridge-probe.c (that reviewed file and
 * bt-h4-ibs-bridge.c are unchanged; their hashes are pinned by the trial adapter).
 * Same power/patch/NVM/3M-baud/runtime-reset sequence; the H4 bridge then runs
 * until SIGINT/SIGTERM instead of 20 s, so hci0 stays registered. On stop the
 * existing cleanup detaches the line discipline, restores the UART and powers
 * the chip off. No pairing, scan or advertising is issued by this program.
 * Build (host): aarch64-linux-gnu-gcc -static -std=c11 -Wall -Wextra -Werror -O2
 *   -I /home/corpunum/s22-linux/tools/hardware -I tools/hardware bt/bt-qca6490-hci-hold.c
 */
#define S22_BT_BRIDGE_EMBED 1
#include "../bt-h4-ibs-bridge.c"
static int hold_after_reset(int fd);
#define S22_BT_AFTER_RUNTIME_COMMANDS hold_after_reset
#include "../bt-qca6490-runtime-reset.c"

/* Tag 17 byte 0 bit 7 = IBS enable (same rule as the reviewed probe). */
static int hold_transport_mode(enum s22_bt_transport_mode *mode)
{
	const uint8_t *data = s22_nvm_payload;
	size_t length = s22_nvm_payload_len, at = 4;
	unsigned tag17 = 0;
	int ibs = 0;

	if (length < 4 || data[0] != 2 ||
	    ((size_t)data[1] | ((size_t)data[2] << 8) | ((size_t)data[3] << 16)) != length - 4)
		return -EINVAL;
	while (at < length) {
		uint16_t tag, plen;
		if (length - at < 12)
			return -EINVAL;
		tag = (uint16_t)(data[at] | (data[at + 1] << 8));
		plen = (uint16_t)(data[at + 2] | (data[at + 3] << 8));
		if ((size_t)plen > length - at - 12)
			return -EINVAL;
		if (tag == 17) {
			if (tag17++ || plen != 6)
				return -EPROTO;
			ibs = !!(data[at + 12] & 0x80);
		}
		at += 12 + plen;
	}
	if (at != length || tag17 != 1)
		return -EPROTO;
	*mode = ibs ? S22_BT_TRANSPORT_H4_IBS : S22_BT_TRANSPORT_H4_NO_IBS;
	return 0;
}

/* s22_bridge_run() with the duration check replaced by "until a stop signal". */
static int s22_bridge_hold(int uart, enum s22_bt_transport_mode transport_mode)
{
	struct sigaction action = {0}, old_int, old_term;
	struct bridge x;
	struct termios t;
	int rc = -1, fl;

	running = 1;
	action.sa_handler = stop_signal;
	sigemptyset(&action.sa_mask);
	if (sigaction(SIGINT, &action, &old_int))
		return -1;
	if (sigaction(SIGTERM, &action, &old_term)) {
		sigaction(SIGINT, &old_int, NULL);
		return -1;
	}
	memset(&x, 0, sizeof(x));
	x.uart = uart;
	x.pty_master = x.pty_slave = -1;
	x.transport_mode = transport_mode;
	x.tx_awake = x.rx_awake = transport_mode == S22_BT_TRANSPORT_H4_NO_IBS;
	if (openpty(&x.pty_master, &x.pty_slave, x.slave_name, NULL, NULL) < 0)
		goto out;
	fl = fcntl(x.pty_master, F_GETFL, 0);
	if (fl < 0 || fcntl(x.pty_master, F_SETFL, fl | O_NONBLOCK) < 0)
		goto out;
	fl = fcntl(x.pty_slave, F_GETFL, 0);
	if (fl < 0 || fcntl(x.pty_slave, F_SETFL, fl | O_NONBLOCK) < 0)
		goto out;
	if (tcgetattr(x.pty_slave, &t) < 0)
		goto out;
	cfmakeraw(&t);
	t.c_cflag |= CLOCAL | CREAD;
	if (tcsetattr(x.pty_slave, TCSANOW, &t) < 0)
		goto out;
	if (attach_h4(&x) < 0) {
		perror("H4 attach");
		goto out;
	}
	printf("bridge_hold=until-signal transport=%s\n",
	       transport_mode == S22_BT_TRANSPORT_H4_IBS ? "h4-ibs" : "h4-no-ibs");
	fflush(stdout);
	rc = run_bridge(&x, 0);
	if (rc == -ECANCELED)
		rc = 0;	/* stopped on request: normal end of a hold */
	if (rc < 0 && x.failure_reason)
		fprintf(stderr, "bridge_failure=%s\n", x.failure_reason);
	report_queue_overflow(&x);
	printf("bridge_result=%d commands=%u events=%u queued=%zu\n",
	       rc, x.commands, x.events, x.pending_count);
	fflush(stdout);
out:
	if (x.pty_slave >= 0) {
		int attached = x.hci_attached;
		int r = detach_h4(&x);
		if (attached) {
			printf("pty_cleanup_ioctl_result=%d\n", r);
			if (r < 0 && !rc)
				rc = -1;
		}
		close(x.pty_slave);
	}
	if (x.pty_master >= 0)
		close(x.pty_master);
	sigaction(SIGINT, &old_int, NULL);
	sigaction(SIGTERM, &old_term, NULL);
	fflush(stdout);
	return rc;
}

static int hold_after_reset(int fd)
{
	enum s22_bt_transport_mode mode;
	int ret = hold_transport_mode(&mode);

	if (ret) {
		fprintf(stderr, "bridge_profile_invalid=runtime-nvm-tag17 errno=%d\n", -ret);
		return ret;
	}
	return s22_bridge_hold(fd, mode);
}
