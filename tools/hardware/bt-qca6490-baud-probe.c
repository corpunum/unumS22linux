/* Bounded 115200 -> 3M transport test, never firmware/NVM/HCI activation. */
#pragma push_macro("main")
#undef main
#define main patch_identity_main
#define run_probe patch_identity_run_probe
#include "bt-qca6490-patch-version-probe.c"
#undef run_probe
#undef main
#pragma pop_macro("main")
#include <sys/socket.h>

static const uint8_t expected_patch[] = {
  0x04,0x0e,0x12,0x01,0x00,0xfc,0x00,0x19,0x0c,0x13,0x00,
  0x00,0x00,0xe6,0x38,0x01,0x02,0x10,0x02,0x0c,0x40
};
static const uint8_t baud_3m[] = {0x01,0x48,0xfc,0x01,0x0e};

static int patch_identity(int fd)
{
  uint8_t frame[BT_MAX_EVENT_BYTES]; size_t length;
  int64_t deadline = monotonic_millis() + VERSION_TIMEOUT_MS;
  int rc = write_bounded(fd, patch_version_cmd, sizeof(patch_version_cmd), VERSION_TIMEOUT_MS);
  if (rc) return rc;
  rc = read_event(fd, frame, &length, deadline);
  if (rc) return rc;
  printf("patch_identity raw="); print_hex(frame, length); putchar('\n');
  return length == sizeof(expected_patch) && !memcmp(frame, expected_patch, length) ? 0 : -EPROTO;
}

static int flow_control(int fd, bool enabled)
{
  struct termios tty, observed;
  if (tcgetattr(fd, &tty)) return -errno;
  if (enabled) tty.c_cflag |= CRTSCTS; else tty.c_cflag &= ~CRTSCTS;
  if (tcsetattr(fd, TCSANOW, &tty) || tcgetattr(fd, &observed)) return -errno;
  return !!(observed.c_cflag & CRTSCTS) == enabled ? 0 : -EIO;
}

static int set_3m(int fd)
{
  struct termios tty, observed;
  if (tcgetattr(fd, &tty) || cfsetispeed(&tty, B3000000) || cfsetospeed(&tty, B3000000) ||
      tcsetattr(fd, TCSANOW, &tty) || tcgetattr(fd, &observed)) return -errno;
  return cfgetispeed(&observed) == B3000000 && cfgetospeed(&observed) == B3000000 ? 0 : -EIO;
}

static int baud_probe(const char *uart_path, const char *power_path,
                      unsigned power_major, unsigned power_minor, bool live)
{
  struct termios saved; struct sigaction old_int, old_term;
#ifdef S22_BT_PRECOMPUTED_BAUD
  struct termios next_3m, observed_3m;
#endif
  uint8_t frame[BT_MAX_EVENT_BYTES]; size_t length = 0;
  int uart = -1, power = -1, rc = -EIO;
  bool saved_valid = false, configured = false, powered = false;
  if (install_signal_handlers(&old_int, &old_term)) return 1;
  if (!live || require_char_device(uart_path, EXPECTED_UART_PATH, EXPECTED_UART_MAJOR, EXPECTED_UART_MINOR) ||
      require_char_device(power_path, EXPECTED_BTPOWER_PATH, power_major, power_minor) ||
      validate_live_wlan_baseline()) goto cleanup;
  power = open(power_path, O_RDWR | O_CLOEXEC | O_NOFOLLOW); if (power < 0) goto cleanup;
  { struct stat st; if (fstat(power, &st) || !S_ISCHR(st.st_mode) || major(st.st_rdev) != power_major || minor(st.st_rdev) != power_minor) goto cleanup; }
  uart = open(uart_path, O_RDWR | O_NOCTTY | O_CLOEXEC | O_NONBLOCK | O_NOFOLLOW); if (uart < 0) goto cleanup;
  { struct stat st; if (fstat(uart, &st) || !S_ISCHR(st.st_mode) || major(st.st_rdev) != EXPECTED_UART_MAJOR || minor(st.st_rdev) != EXPECTED_UART_MINOR || ioctl(uart, TIOCEXCL)) goto cleanup; }
  if (configure_uart(uart, &saved, &saved_valid)) goto cleanup;
  configured = true;
  if (power_control(power, 1)) goto cleanup;
  powered = true;
  if (check_vote(1)) goto cleanup;
  if (write_bounded(uart, qti_get_app_version, sizeof(qti_get_app_version), VERSION_TIMEOUT_MS) ||
      command_complete_version(uart, frame, &length) || validate_version_identity(frame, length) ||
      patch_identity(uart)) { rc = -EPROTO; goto cleanup; }
  /* Private HAL jump table 2d9e9: op3 -> 5fbc8 clears CRTSCTS.
   * op4, NOT op3, invokes private ioctl 54ec. */
  if ((rc = flow_control(uart, false))) goto cleanup;
#ifdef S22_BT_PRECOMPUTED_BAUD
  /* Finish configuration reads before the controller switches its clock.
   * Preserve exactly the same baud/flow protocol; omit diagnostic stdout
   * and avoidable TCGETS calls in the write-to-host-speed interval. */
  if (tcgetattr(uart, &next_3m) || cfsetispeed(&next_3m, B3000000) ||
      cfsetospeed(&next_3m, B3000000)) { rc = -errno; goto cleanup; }
#endif
  if ((rc = write_bounded(uart, baud_3m, sizeof(baud_3m), VERSION_TIMEOUT_MS))) goto cleanup;
#ifndef S22_BT_PRECOMPUTED_BAUD
  puts("sent=baud3m raw=01 48 fc 01 0e"); fflush(stdout);
#endif
  if (tcdrain(uart)) { rc = -errno; goto cleanup; }
  if (stop_requested) { rc = -ECANCELED; goto cleanup; }
#ifdef S22_BT_PRECOMPUTED_BAUD
  if (tcsetattr(uart, TCSANOW, &next_3m)) { rc = -errno; goto cleanup; }
#else
  if ((rc = set_3m(uart))) goto cleanup;
#endif
  { struct timespec delay = {0, 20000000};
    if (nanosleep(&delay, NULL) || stop_requested) { rc = -ECANCELED; goto cleanup; } }
#ifdef S22_BT_PRECOMPUTED_BAUD
  if (tcgetattr(uart, &observed_3m)) { rc = -errno; goto cleanup; }
  if (cfgetispeed(&observed_3m) != B3000000 || cfgetospeed(&observed_3m) != B3000000) {
    rc = -EIO; goto cleanup;
  }
#endif
  if ((rc = flow_control(uart, true))) goto cleanup;
#ifdef S22_BT_PRECOMPUTED_BAUD
  puts("sent=baud3m raw=01 48 fc 01 0e precomputed_termios=yes"); fflush(stdout);
#endif
  if ((rc = capture_opaque_events(uart, VERSION_TIMEOUT_MS))) goto cleanup;
  if ((rc = patch_identity(uart))) goto cleanup;
  rc = 0;
  puts("baud_transport_3m_identity=PASS");
#ifdef S22_BT_AFTER_BAUD
  rc = S22_BT_AFTER_BAUD(uart);
#endif
cleanup:
  if (saved_valid) { int r = configured ? stock_uart_cleanup(uart, &saved) : restore_uart_termios(uart, &saved); if (r && !rc) rc = r; }
  if (uart >= 0) { (void)ioctl(uart, TIOCNXCL); close(uart); }
  if (powered) { int r = power_control(power, 0); if (r && !rc) rc = r; if (check_vote(0)) rc = -EIO; }
  if (power >= 0) close(power);
  (void)sigaction(SIGINT, &old_int, NULL); (void)sigaction(SIGTERM, &old_term, NULL);
  if (stop_requested && !rc) rc = -ECANCELED;
  fprintf(stderr, "baud_probe_result=%d\n", rc);
  return rc;
}

static int baud_self_test(void)
{
  int p[2], s[2], master, slave; uint8_t sent[5]; struct termios saved;
  if (patch_self_test() || pipe(p)) return 1;
  if (write_bounded(p[1], baud_3m, sizeof(baud_3m), 20) ||
      read(p[0], sent, sizeof(sent)) != sizeof(sent) || memcmp(sent, baud_3m, sizeof(sent))) return 1;
  close(p[0]); close(p[1]);
  if (socketpair(AF_UNIX, SOCK_STREAM, 0, s)) return 1;
  if (write(s[1], expected_patch, sizeof(expected_patch)) != sizeof(expected_patch) ||
      patch_identity(s[0]) || read(s[1], sent, sizeof(sent)) != sizeof(sent) ||
      memcmp(sent, patch_version_cmd, sizeof(sent))) return 1;
  { uint8_t bad[sizeof(expected_patch)]; memcpy(bad, expected_patch, sizeof(bad)); bad[6] = 1;
    if (write(s[1], bad, sizeof(bad)) != sizeof(bad) || patch_identity(s[0]) != -EPROTO) return 1; }
  close(s[0]); close(s[1]);
  master = posix_openpt(O_RDWR | O_NOCTTY);
  if (master < 0 || grantpt(master) || unlockpt(master)) return 1;
  slave = open(ptsname(master), O_RDWR | O_NOCTTY);
  if (slave < 0 || tcgetattr(slave, &saved) || flow_control(slave, false) ||
      set_3m(slave) || flow_control(slave, true) || tcsetattr(slave, TCSANOW, &saved)) return 1;
  close(slave); close(master);
#ifdef S22_BT_EXTRA_SELF_TEST
  if (S22_BT_EXTRA_SELF_TEST()) return 1;
#endif
  puts("baud command framing self-test: PASS (no hardware)"); return 0;
}

int main(int argc, char **argv)
{
  const char *uart = NULL, *power = NULL, *rdev = NULL; unsigned maj, min;
  bool execute = false, allow = false, live = false;
  if (argc == 2 && !strcmp(argv[1], "--self-test")) return baud_self_test();
  if (argc == 2 && !strcmp(argv[1], "--check-live-wlan")) return validate_live_wlan_baseline() ? 1 : 0;
  for (int i = 1; i < argc; i++) {
    if (!strcmp(argv[i], "--execute")) execute = true;
    else if (!strcmp(argv[i], "--allow-shared-wlan-rail")) allow = true;
    else if (!strcmp(argv[i], "--live-wlan-vote")) live = true;
    else if (!strcmp(argv[i], "--uart") && i + 1 < argc) uart = argv[++i];
    else if (!strcmp(argv[i], "--btpower") && i + 1 < argc) power = argv[++i];
    else if (!strcmp(argv[i], "--btpower-rdev") && i + 1 < argc) rdev = argv[++i];
    else return 2;
  }
  if (!execute || !allow || !live || !uart || !power || !rdev || parse_rdev(rdev, &maj, &min) || maj != 503 || min != 0) return 2;
  return baud_probe(uart, power, maj, min, live) ? 1 : 0;
}
