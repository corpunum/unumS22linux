/* One 243-byte exact firmware prefix, then bounded opaque reply and teardown.
 * This is NOT a complete firmware download or HCI activation.
 * The private prefix header is generated only after full-asset hash checking.
 */
static int capture_segment(int fd);
static int segment_self_test(void);
#define S22_BT_AFTER_BAUD capture_segment
#define S22_BT_EXTRA_SELF_TEST segment_self_test
#include "bt-qca6490-baud-probe.c"
#include "../../builds/bt-segment-20260922/qca-patch-prefix-private.h"

static void make_segment(uint8_t packet[249])
{
  const uint8_t header[6] = {0x01,0x00,0xfc,0xf5,0x1e,0xf3};
  memcpy(packet,header,6);memcpy(packet+6,s22_patch_prefix,243);
}

static int capture_segment(int fd)
{
  uint8_t packet[249];make_segment(packet);
  int rc=write_bounded(fd,packet,sizeof(packet),VERSION_TIMEOUT_MS);
  if (rc) return rc;
  puts("patch_prefix_transmitted_bytes=243 full_firmware_transmitted=no nvm_transmitted=no");
  fflush(stdout);
  rc=capture_opaque_events(fd,VERSION_TIMEOUT_MS);
  puts("patch_ack_status=UNINTERPRETED no_more_packets");
  return rc;
}

static int segment_self_test(void)
{
  uint8_t packet[249];make_segment(packet);
  if (sizeof(s22_patch_prefix)!=243 || packet[0]!=1 || packet[1]!=0 || packet[2]!=0xfc ||
      packet[3]!=sizeof(packet)-4 || packet[4]!=0x1e || packet[5]!=sizeof(packet)-6 ||
      memcmp(packet+6,"\x01\x04\xfd\x02",4)) return 1;
  puts("segment framing self-test: PASS (no hardware)");return 0;
}
