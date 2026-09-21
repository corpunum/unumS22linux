/* Exact stock RAM patch transfer in skip-intermediate-event mode3.
 * No NVM, reset, HCI registration or permanent flash. Bounded reply capture.
 */
static int capture_full_patch(int fd);
static int full_patch_self_test(void);
#define S22_BT_AFTER_BAUD capture_full_patch
#define S22_BT_EXTRA_SELF_TEST full_patch_self_test
#include "bt-qca6490-baud-probe.c"
#include "../../builds/bt-patch-20260922/qca-patch-private.h"

#ifdef S22_BT_POSTPATCH_BOARD
static const uint8_t patch_ack[] = {4,0x0e,5,1,0,0xfc,0,0x1e};
static const uint8_t board_cmd[] = {1,0,0xfc,1,0x23};
static int postpatch_board(int fd)
{
  uint8_t frame[BT_MAX_EVENT_BYTES];size_t n=0;int rc;
  rc=read_event(fd,frame,&n,monotonic_millis()+VERSION_TIMEOUT_MS);
  if (rc) return rc;
  printf("patch_ack raw=");print_hex(frame,n);putchar('\n');
  if (n!=sizeof(patch_ack) || memcmp(frame,patch_ack,n)) return -EPROTO;
  rc=write_bounded(fd,board_cmd,sizeof(board_cmd),VERSION_TIMEOUT_MS);
  if (rc) return rc;
  rc=read_event(fd,frame,&n,monotonic_millis()+VERSION_TIMEOUT_MS);
  if (rc) return rc;
  printf("postpatch_board raw=");print_hex(frame,n);putchar('\n');
  if (n!=11 || memcmp(frame,"\x04\x0e\x08\x01\x00\xfc\x00\x23\x02",9)) return -EPROTO;
  puts("postpatch_board_response=PASS nvm_transmitted=no hci_attached=no");return 0;
}
#endif

static size_t packet_at(size_t offset,uint8_t packet[249])
{
  size_t left=sizeof(s22_patch_payload)-offset,n=left>243?243:left;
  packet[0]=1;packet[1]=0;packet[2]=0xfc;packet[3]=(uint8_t)(n+2);
  packet[4]=0x1e;packet[5]=(uint8_t)n;
  memcpy(packet+6,s22_patch_payload+offset,n);return n+6;
}

static int full_patch_self_test(void)
{
  uint8_t packet[249];size_t offset=0,count=0,n=0;
  if (sizeof(s22_patch_payload)!=195848 || s22_patch_payload[14]!=3 ||
      memcmp(s22_patch_payload,"\x01\x04\xfd\x02",4)) return 1;
  while (offset<sizeof(s22_patch_payload)) {
    n=packet_at(offset,packet);
    if (n>249 || n<7 || packet[3]!=n-4 || packet[5]!=n-6 ||
        memcmp(packet+6,s22_patch_payload+offset,n-6)) return 1;
    offset+=n-6;count++;
  }
  if (count!=806 || n!=239 || offset!=195848) return 1;
#ifdef S22_BT_POSTPATCH_BOARD
  { int s[2];uint8_t command[5],reply[]={4,0x0e,8,1,0,0xfc,0,0x23,2,0,0};
    if (socketpair(AF_UNIX,SOCK_STREAM,0,s)) return 1;
    if (write(s[1],patch_ack,sizeof(patch_ack))!=sizeof(patch_ack) ||
        write(s[1],reply,sizeof(reply))!=sizeof(reply) || postpatch_board(s[0]) ||
        read(s[1],command,sizeof(command))!=sizeof(command) || memcmp(command,board_cmd,sizeof(command))) return 1;
    reply[6]=1;
    if (write(s[1],patch_ack,sizeof(patch_ack))!=sizeof(patch_ack) ||
        write(s[1],reply,sizeof(reply))!=sizeof(reply) || postpatch_board(s[0])!=-EPROTO) return 1;
    close(s[0]);close(s[1]);
  }
#endif
  puts("full patch806-segment framing self-test: PASS (no hardware)");return 0;
}

static int capture_full_patch(int fd)
{
  uint8_t packet[249];size_t offset=0,count=0;
  int64_t end=monotonic_millis()+5000;
  while (offset<sizeof(s22_patch_payload)) {
    int64_t remaining=end-monotonic_millis();int rc;
    if (remaining<=0 || stop_requested) return -ETIMEDOUT;
    size_t n=packet_at(offset,packet);
    rc=write_bounded(fd,packet,n,(int)remaining);
    if (rc) return rc;
    offset+=n-6;count++;
  }
  if (tcdrain(fd)) return -errno;
  printf("patch_transmitted_bytes=%zu segments=%zu download_mode=3 nvm_transmitted=no\n",offset,count);
  fflush(stdout);
#ifdef S22_BT_POSTPATCH_BOARD
  return postpatch_board(fd);
#else
  int rc=capture_opaque_events(fd,VERSION_TIMEOUT_MS);
  puts("patch_final_reply=UNINTERPRETED power_off_next");return rc;
#endif
}
