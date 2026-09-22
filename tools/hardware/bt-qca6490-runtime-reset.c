/* Normal runtime configuration with a private Linux-generated address.
 * Reset/readback only: no discovery, advertising, connection or HCI attach.
 * The base probe owns power/UART and always powers off on return.
 */
static int runtime_reset(int fd);
static int runtime_self_test(void);
#define S22_BT_PRECOMPUTED_BAUD 1
#define S22_BT_AFTER_PATCH_BOARD runtime_reset
#define S22_BT_RUNTIME_SELF_TEST runtime_self_test
#ifndef S22_BT_NVM_HEADER
#define S22_BT_NVM_HEADER "../../builds/bt-runtime-nvm-20260922/s22_nvm_payload.h"
#endif
#include "bt-qca6490-nvm-capture.c"

static const uint8_t reset_cmd[]={1,3,0x0c,0};
static const uint8_t bdaddr_cmd[]={1,9,0x10,0};
static const uint8_t local_version_cmd[]={1,1,0x10,0};

static int expected_address(uint8_t result[6])
{
  size_t at=4;unsigned found=0;
  while(at+12<=sizeof(s22_nvm_payload)) {
    unsigned tag=s22_nvm_payload[at] | ((unsigned)s22_nvm_payload[at+1]<<8);
    size_t len=s22_nvm_payload[at+2] | ((size_t)s22_nvm_payload[at+3]<<8);
    if (at+12+len>sizeof(s22_nvm_payload)) return -EINVAL;
    if (tag==2) {
      if (len!=6 || found++) return -EINVAL;
      memcpy(result,s22_nvm_payload+at+12,6);
    }
    at+=12+len;
  }
  if (at!=sizeof(s22_nvm_payload) || found!=1 || result[4]!=0x22 || result[5]!=0x22)
    return -EINVAL;
  return 0;
}

static int runtime_commands(int fd)
{
  uint8_t frame[BT_MAX_EVENT_BYTES],address[6];size_t n=0;int rc;
  if ((rc=expected_address(address))) return rc;
  if ((rc=write_bounded(fd,reset_cmd,sizeof(reset_cmd),VERSION_TIMEOUT_MS))) return rc;
  if ((rc=read_event(fd,frame,&n,monotonic_millis()+VERSION_TIMEOUT_MS))) return rc;
  printf("runtime_reset_reply=");print_hex(frame,n);putchar('\n');fflush(stdout);
  if (n!=7 || memcmp(frame,"\x04\x0e\x04\x01\x03\x0c\x00",7)) return -EPROTO;
  if ((rc=write_bounded(fd,bdaddr_cmd,sizeof(bdaddr_cmd),VERSION_TIMEOUT_MS))) return rc;
  if ((rc=read_event(fd,frame,&n,monotonic_millis()+VERSION_TIMEOUT_MS))) return rc;
  /* Do not print the address; the restricted UART trace is private evidence. */
  if (n!=13 || memcmp(frame,"\x04\x0e\x0a\x01\x09\x10\x00",7) ||
      memcmp(frame+7,address,6)) return -EPROTO;
  puts("runtime_address_readback_matches=yes source=linux-generated not_factory=yes");
  if ((rc=write_bounded(fd,local_version_cmd,sizeof(local_version_cmd),VERSION_TIMEOUT_MS))) return rc;
  if ((rc=read_event(fd,frame,&n,monotonic_millis()+VERSION_TIMEOUT_MS))) return rc;
  printf("runtime_local_version_reply=");print_hex(frame,n);putchar('\n');fflush(stdout);
  if (n!=15 || memcmp(frame,"\x04\x0e\x0c\x01\x01\x10\x00",7)) return -EPROTO;
  puts("runtime_controller_reset_readback=PASS hci_attached=no discovery_sent=no power_off_next=yes");
  return 0;
}

static int runtime_reset(int fd)
{
  uint8_t address[6];int rc=expected_address(address);
  if (rc) return rc;
  rc=nvm_transfer(fd);
  if (!rc) rc=runtime_commands(fd);
#ifdef S22_BT_AFTER_RUNTIME_COMMANDS
  if (!rc) rc=S22_BT_AFTER_RUNTIME_COMMANDS(fd);
#endif
  return rc;
}

static int runtime_self_test(void)
{
  const uint8_t reset_reply[]={4,14,4,1,3,12,0};
  uint8_t address_reply[]={4,14,10,1,9,16,0,0,0,0,0,0,0};
  const uint8_t version_reply[]={4,14,12,1,1,16,0,12,0,1,12,29,0,0,1};
  uint8_t commands[12],expected[12];int s[2];
  if (expected_address(address_reply+7)) return 1;
  memcpy(expected,reset_cmd,4);memcpy(expected+4,bdaddr_cmd,4);memcpy(expected+8,local_version_cmd,4);
  for (unsigned mode=0;mode<4;mode++) {
    uint8_t reset[7];memcpy(reset,reset_reply,7);
    if (socketpair(AF_UNIX,SOCK_STREAM,0,s)) return 1;
    if (mode==1) reset[6]=1;
    if (mode==2) address_reply[7]^=1;
    if (mode==3) reset[4]=4;
    if (write(s[1],reset,7)!=7 || write(s[1],address_reply,13)!=13 ||
        write(s[1],version_reply,15)!=15) return 1;
    int rc=runtime_commands(s[0]);
    if ((!mode && rc) || (mode && rc!=-EPROTO)) return 1;
    if (!mode && (read(s[1],commands,12)!=12 || memcmp(commands,expected,12))) return 1;
    if (mode==2) address_reply[7]^=1;
    close(s[0]);close(s[1]);
  }
  puts("runtime reset/address/version and negative-reply self-tests: PASS (no hardware)");
  return 0;
}
