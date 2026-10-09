/* Preinitialize the controller, then hand off through the existing H4 ldisc. */
#define S22_BT_BRIDGE_EMBED 1
#include "bt-h4-ibs-bridge.c"
static int bridge_after_reset(int fd);
#define S22_BT_AFTER_RUNTIME_COMMANDS bridge_after_reset
#include "bt-qca6490-runtime-reset.c"

/*
 * This probe uses the exact reviewed QCA6490 runtime NVM header above. The
 * QCA Tag 17 transport field carries IBS enable in payload byte 0 bit 7;
 * select the bridge mode from that embedded, already-transmitted profile.
 * Generic bridge callers remain IBS by default and cannot infer plain H4.
 */
static int qca6490_runtime_transport_mode(
		const uint8_t *data, size_t length,
		enum s22_bt_transport_mode *mode)
{
	size_t at = 4;
	unsigned tag17_count = 0;
	int ibs_enabled = 0;

	if (!data || !mode || length < 4 || data[0] != 2)
		return -EINVAL;
	if (((size_t)data[1] | ((size_t)data[2] << 8) |
	     ((size_t)data[3] << 16)) != length - 4)
		return -EINVAL;

	while (at < length) {
		uint16_t tag, payload_length;
		size_t payload;

		if (length - at < 12)
			return -EINVAL;
		tag = (uint16_t)data[at] | ((uint16_t)data[at + 1] << 8);
		payload_length = (uint16_t)data[at + 2] |
			((uint16_t)data[at + 3] << 8);
		payload = at + 12;
		if ((size_t)payload_length > length - payload)
			return -EINVAL;
		if (tag == 17) {
			if (tag17_count++ || payload_length != 6)
				return -EPROTO;
			ibs_enabled = !!(data[payload] & 0x80);
		}
		at = payload + payload_length;
	}
	if (at != length || tag17_count != 1)
		return -EPROTO;
	*mode = ibs_enabled ? S22_BT_TRANSPORT_H4_IBS :
		S22_BT_TRANSPORT_H4_NO_IBS;
	return 0;
}

static int bridge_after_reset(int fd)
{
	enum s22_bt_transport_mode mode;
	int ret = qca6490_runtime_transport_mode(s22_nvm_payload,
			s22_nvm_payload_len, &mode);

	if (ret) {
		fprintf(stderr, "bridge_profile_invalid=runtime-nvm-tag17 errno=%d\n",
			-ret);
		return ret;
	}
	puts("bridge_transport_profile=qca6490-runtime-nvm-tag17");
	return s22_bridge_run(fd, 20000, mode);
}
