/* Read-only HCI identity query after the accepted patch/board sequence.
 * No EFS access, NVM, radio enable, reset or HCI attachment. */
#define S22_BT_POSTPATCH_BOARD
#define S22_BT_READ_ADDRESS
#include "bt-qca6490-patch-capture.c"
