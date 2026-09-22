/* Preinitialize real hardware, then bounded existing-kernel H4/IBS handoff. */
#define S22_BT_BRIDGE_EMBED 1
#include "bt-h4-ibs-bridge.c"
static int bridge_after_reset(int fd) { return s22_bridge_run(fd,20000); }
#define S22_BT_AFTER_RUNTIME_COMMANDS bridge_after_reset
#include "bt-qca6490-runtime-reset.c"
