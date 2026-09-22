/* Volatile, non-transmitting NVM transport diagnostic. Never HCI reset/attach.
 * The private generated image preserves the observed all-zero address; it
 * must not be used to enable scanning, advertising, pairing or RF operation.
 */
static int nvm_transfer(int fd);
static int nvm_self_test(void);
#define S22_BT_POSTPATCH_BOARD 1
#define S22_BT_AFTER_PATCH_BOARD nvm_transfer
#define S22_BT_NVM_SELF_TEST nvm_self_test
#include "bt-qca6490-patch-capture.c"
#include "../../builds/bt-nvm-20260922/qca-nvm-private.h"

static size_t nvm_packet_at(size_t offset,uint8_t packet[249])
{
  size_t left=sizeof(s22_nvm_payload)-offset,n=left>243?243:left;
  packet[0]=1;packet[1]=0;packet[2]=0xfc;packet[3]=(uint8_t)(n+2);
  packet[4]=0x1e;packet[5]=(uint8_t)n;
  memcpy(packet+6,s22_nvm_payload+offset,n);return n+6;
}

static int nvm_transfer(int fd)
{
  uint8_t packet[249],frame[BT_MAX_EVENT_BYTES]={0};size_t offset=0,count=0;
  int64_t deadline=monotonic_millis()+5000;
  while (offset<sizeof(s22_nvm_payload)) {
    size_t bytes=nvm_packet_at(offset,packet),n=0;
    int64_t remaining=deadline-monotonic_millis();int rc;
    if (remaining<=0 || stop_requested) return -ETIMEDOUT;
    rc=write_bounded(fd,packet,bytes,(int)remaining);
    if (rc) return rc;
    /* Type2 uses per-segment completion; byte14 is NOT a patch mode field. */
    rc=read_event(fd,frame,&n,deadline);
    printf("nvm_segment=%zu bytes=%zu reply=",count+1,bytes-6);
    print_hex(frame,n);putchar('\n');fflush(stdout);
    if (rc) return rc;
    if (n!=sizeof(patch_ack) || memcmp(frame,patch_ack,n)) return -EPROTO;
    offset+=bytes-6;count++;
  }
  printf("nvm_transmitted_bytes=%zu acknowledged_segments=%zu reset_sent=no hci_attached=no power_off_next=yes\n",offset,count);
  return 0;
}

static int nvm_self_test(void)
{
  uint8_t packet[249],received[249];size_t offset=0,count=0,n=0;int s[2];
  if (sizeof(s22_nvm_payload)!=7023 ||
      memcmp(s22_nvm_payload,"\x02\x6b\x1b\x00",4)) return 1;
  while (offset<sizeof(s22_nvm_payload)) {
    n=nvm_packet_at(offset,packet);
    if (n>249 || n<7 || packet[3]!=n-4 || packet[5]!=n-6 ||
        memcmp(packet+6,s22_nvm_payload+offset,n-6)) return 1;
    offset+=n-6;count++;
  }
  if (count!=29 || n!=225 || offset!=7023) return 1;
  if (socketpair(AF_UNIX,SOCK_STREAM,0,s)) return 1;
  for (size_t i=0;i<29;i++)
    if (write(s[1],patch_ack,sizeof(patch_ack))!=sizeof(patch_ack)) return 1;
  if (nvm_transfer(s[0])) return 1;
  for (offset=0;offset<sizeof(s22_nvm_payload);offset+=n-6) {
    n=nvm_packet_at(offset,packet);
    if (read(s[1],received,n)!=(ssize_t)n || memcmp(packet,received,n)) return 1;
  }
  { uint8_t bad[sizeof(patch_ack)];memcpy(bad,patch_ack,sizeof(bad));bad[6]=1;
    if (write(s[1],bad,sizeof(bad))!=sizeof(bad) || nvm_transfer(s[0])!=-EPROTO) return 1;
  }
  close(s[0]);close(s[1]);
  puts("NVM29-segment framing and rejection self-test: PASS (no hardware)");return 0;
}
