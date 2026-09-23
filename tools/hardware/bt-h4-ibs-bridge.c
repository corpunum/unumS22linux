// Host-only QCA H4+IBS UART bridge. It never powers or initializes a controller.
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <poll.h>
#include <pty.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/select.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>
#include <linux/tty.h>

#define IBS_SLEEP 0xfe
#define IBS_WAKE 0xfd
#define IBS_ACK 0xfc
#define MAX_FRAME 4096
#define WAKE_RETRY_MS 100
#define MAX_WAKE_RETRIES 3
#define MAX_PENDING 8
#define N_HCI 15
#define HCIUARTSETPROTO _IOW('U', 200, int)
#define HCIUARTGETDEVICE _IOR('U', 202, int)

static volatile sig_atomic_t running = 1;
static void stop_signal(int sig) { (void)sig; running = 0; }
void s22_bridge_request_stop(void) { running = 0; }
static uint64_t now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000u + (uint64_t)t.tv_nsec / 1000000u;
}

struct parser { uint8_t b[MAX_FRAME]; size_t n, need; int type; };
struct bridge {
    int uart, pty_master, pty_slave;
    char slave_name[128];
    struct parser uart_rx, pty_rx;
    uint8_t pending[MAX_PENDING][MAX_FRAME]; size_t pending_n[MAX_PENDING];
    size_t pending_head, pending_count;
    int tx_awake, rx_awake, waiting_ack, retries;
    int hci_index;
    unsigned commands, events, ibs_ack_rx, ibs_wake_rx;
    uint64_t wake_at;
};

static void parser_reset(struct parser *p) { p->n = p->need = 0; p->type = 0; }
static int frame_need(uint8_t type, const uint8_t *b, size_t n, size_t *need) {
    size_t x;
    if (type == 1) { if (n < 4) return 0; x = 4u + b[3]; }
    else if (type == 2) { if (n < 5) return 0; x = 5u + b[3] + ((size_t)b[4] << 8); }
    else if (type == 3) { if (n < 4) return 0; x = 4u + b[3]; }
    else if (type == 4) { if (n < 3) return 0; x = 3u + b[2]; }
    else if (type == 5) { if (n < 5) return 0; if (b[4] & 0xc0) return -1; x = 5u + b[3] + ((size_t)b[4] << 8); }
    else return -1;
    if (x > MAX_FRAME) return -1;
    *need = x; return 1;
}
static int parser_byte(struct parser *p, uint8_t c, uint8_t **frame, size_t *len) {
    if (!p->n) { if (c < 1 || c > 5) return -1; p->type = c; }
    if (p->n == MAX_FRAME) return -1;
    p->b[p->n++] = c;
    if (!p->need) {
        int r = frame_need((uint8_t)p->type, p->b, p->n, &p->need);
        if (r < 0) { parser_reset(p); return -1; }
    }
    if (p->need && p->n == p->need) {
        *frame = p->b; *len = p->n; p->n = p->need = 0; p->type = 0; return 1;
    }
    return 0;
}
static int write_full(int fd, const uint8_t *b, size_t n) {
    const uint64_t deadline = now_ms() + 1000;
    while (n) {
        if (!running || now_ms() >= deadline) { errno = ECANCELED; return -1; }
        ssize_t w = write(fd, b, n);
        if (w > 0) { b += w; n -= (size_t)w; continue; }
        if (w < 0 && errno == EINTR) { if (!running || now_ms() >= deadline) return -1; continue; }
        if (w < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            struct pollfd p = {fd, POLLOUT, 0};
            uint64_t left = now_ms() >= deadline ? 0 : deadline - now_ms();
            if (!left || poll(&p, 1, (int)left) <= 0) return -1;
            continue;
        }
        return -1;
    }
    return 0;
}
static int send_wake(struct bridge *x) { const uint8_t c = IBS_WAKE; return write_full(x->uart, &c, 1); }
static int flush_pending(struct bridge *x) {
    while (x->pending_count) {
        size_t i = x->pending_head;
        if (write_full(x->uart, x->pending[i], x->pending_n[i]) < 0) return -1;
        x->pending_n[i] = 0; x->pending_head = (i + 1) % MAX_PENDING; x->pending_count--;
    }
    return 0;
}
static int queue_pty_frame(struct bridge *x, const uint8_t *b, size_t n) {
    if (x->pending_count == MAX_PENDING || n > MAX_FRAME) return -1;
    size_t i = (x->pending_head + x->pending_count) % MAX_PENDING;
    memcpy(x->pending[i], b, n); x->pending_n[i] = n; x->pending_count++;
    if (b[0] == 1) {
        x->commands++;
        printf("bridge_hci_command=%02x%02x length=%zu\n",b[2],b[1],n);
    }
    if (!x->tx_awake && !x->waiting_ack) {
        if (send_wake(x) < 0) return -1;
        x->waiting_ack = 1; x->retries = 1; x->wake_at = now_ms() + WAKE_RETRY_MS;
    } else if (x->tx_awake) {
        if (flush_pending(x) < 0) return -1;
    }
    return 0;
}
static int handle_uart_byte(struct bridge *x, uint8_t c) {
    if (!x->uart_rx.n && (c == IBS_WAKE || c == IBS_SLEEP || c == IBS_ACK)) {
        if (c == IBS_WAKE) { const uint8_t a = IBS_ACK; x->ibs_wake_rx++; if (write_full(x->uart, &a, 1) < 0) return -1; }
        if (c == IBS_SLEEP) x->rx_awake = 0;
        if (c == IBS_ACK && x->waiting_ack) {
            x->ibs_ack_rx++;
            x->waiting_ack = 0; x->tx_awake = 1;
            if (flush_pending(x) < 0) return -1;
        }
        if (c == IBS_WAKE) x->rx_awake = 1;
        return 0;
    }
    uint8_t *f; size_t n; int r = parser_byte(&x->uart_rx, c, &f, &n);
    if (r < 0) { parser_reset(&x->uart_rx); return -1; }
    if (r == 1) {
        if (f[0] == 4) x->events++;
        return write_full(x->pty_master, f, n);
    }
    return 0;
}
static int handle_pty_byte(struct bridge *x, uint8_t c) {
    uint8_t *f; size_t n; int r = parser_byte(&x->pty_rx, c, &f, &n);
    if (r < 0) { parser_reset(&x->pty_rx); return -1; }
    return r == 1 ? queue_pty_frame(x, f, n) : 0;
}
#ifndef S22_BT_BRIDGE_EMBED
static int set_uart(int fd, speed_t speed) {
    struct termios t;
    if (tcgetattr(fd, &t) < 0) return -1;
    cfmakeraw(&t); t.c_cflag |= CLOCAL | CREAD | CRTSCTS;
    if (cfsetispeed(&t, speed) || cfsetospeed(&t, speed)) return -1;
    return tcsetattr(fd, TCSANOW, &t);
}
static int speed_value(unsigned long n, speed_t *out) {
    if (n == 115200) *out = B115200; else if (n == 3000000) *out = B3000000;
    else if (n == 4000000) *out = B4000000; else return -1;
    return 0;
}
#endif
static int cleanup_pty(struct bridge *x) {
    int line = 0;
    return ioctl(x->pty_slave, TIOCSETD, &line);
}
static int attach_h4(struct bridge *x) {
    int line = N_HCI, index = -1;
    if (ioctl(x->pty_slave, TIOCSETD, &line) < 0) return -1;
    /* These ioctls use arg as a VALUE / return the index, not pointers. */
    if (ioctl(x->pty_slave, HCIUARTSETPROTO, 0UL) < 0) { line = 0; (void)ioctl(x->pty_slave, TIOCSETD, &line); return -1; }
    index = ioctl(x->pty_slave, HCIUARTGETDEVICE, 0UL);
    if (index < 0) return -1;
    x->hci_index=index;
    fprintf(stdout, "bridge_registered_hci=%d\n", index); fflush(stdout); return 0;
}
static int run_bridge(struct bridge *x, int duration_ms) {
    uint8_t buf[512];
    const uint64_t end = duration_ms > 0 ? now_ms() + (uint64_t)duration_ms : 0;
    while (running) {
        if (end && now_ms() >= end) break;
        int timeout = 100;
        if (x->waiting_ack) {
            uint64_t n = now_ms();
            timeout = n >= x->wake_at ? 0 : (int)(x->wake_at - n);
        }
        struct pollfd p[2] = {{x->uart, POLLIN, 0}, {x->pty_master, POLLIN, 0}};
        if (end) { uint64_t left = now_ms() >= end ? 0 : end - now_ms(); if (!left) break; if (timeout > (int)left) timeout = (int)left; }
        int r = poll(p, 2, timeout);
        if (r < 0 && errno == EINTR) continue;
        if (r < 0) return -1;
        if (p[0].revents & (POLLERR | POLLHUP | POLLNVAL)) return -1;
        if (p[1].revents & (POLLERR | POLLHUP | POLLNVAL)) return -1;
        if (p[0].revents & POLLIN) { ssize_t n = read(x->uart, buf, sizeof(buf)); if (n <= 0) return -1; for (ssize_t i=0;i<n;i++) if (handle_uart_byte(x,buf[i]) < 0) return -1; }
        if (p[1].revents & POLLIN) { ssize_t n = read(x->pty_master, buf, sizeof(buf)); if (n <= 0) return -1; for (ssize_t i=0;i<n;i++) if (handle_pty_byte(x,buf[i]) < 0) return -1; }
        if (x->waiting_ack && now_ms() >= x->wake_at) {
            if (x->retries >= MAX_WAKE_RETRIES) return -1;
            if (send_wake(x) < 0) return -1;
            x->retries++; x->wake_at = now_ms() + WAKE_RETRY_MS;
        }
    }
    return running ? 0 : -ECANCELED;
}

/* Pinned Linux hci_sock.h UAPI layout. No Bluetooth library dependency.
 * The address is deliberately never logged. This is a read-only ioctl.
 */
struct s22_hci_info {
    uint16_t dev_id; char name[8]; uint8_t address[6]; uint32_t flags;
    uint8_t type, features[8]; uint32_t pkt_type, link_policy, link_mode;
    uint16_t acl_mtu, acl_pkts, sco_mtu, sco_pkts; uint32_t stats[10];
};
static int report_hci(struct bridge *x) {
    struct s22_hci_info info={.dev_id=(uint16_t)x->hci_index};
    _Static_assert(sizeof(info)==92,"hci_dev_info UAPI size");
    int fd=socket(AF_BLUETOOTH,SOCK_RAW|SOCK_CLOEXEC,1);
    if(fd<0)return -1;
    int rc=ioctl(fd,_IOR('H',211,int),&info);close(fd);
    if(rc)return -1;
    printf("bridge_hci_info index=%u flags=%08x acl_mtu=%u cmd_tx=%u evt_rx=%u err_rx=%u err_tx=%u\n",
        info.dev_id,info.flags,info.acl_mtu,info.stats[2],info.stats[3],info.stats[0],info.stats[1]);
    return 0;
}
int s22_bridge_run(int uart, int duration_ms) {
    if(duration_ms<1 || duration_ms>60000)return -EINVAL;
    running = 1;
    struct sigaction action={0},old_int,old_term;
    action.sa_handler=stop_signal;sigemptyset(&action.sa_mask);
    if(sigaction(SIGINT,&action,&old_int))return -1;
    if(sigaction(SIGTERM,&action,&old_term)) { sigaction(SIGINT,&old_int,NULL);return -1; }
    struct bridge x; memset(&x, 0, sizeof(x)); x.uart = uart; x.pty_master = x.pty_slave = -1;
    int rc=-1;
    if (openpty(&x.pty_master, &x.pty_slave, x.slave_name, NULL, NULL) < 0) goto out;
    int fl = fcntl(x.pty_master, F_GETFL, 0); if (fl < 0 || fcntl(x.pty_master, F_SETFL, fl | O_NONBLOCK)<0) goto out;
    fl = fcntl(x.pty_slave, F_GETFL, 0); if (fl < 0 || fcntl(x.pty_slave, F_SETFL, fl | O_NONBLOCK)<0) goto out;
    struct termios t; if (tcgetattr(x.pty_slave, &t) < 0) goto out;
    cfmakeraw(&t); t.c_cflag |= CLOCAL | CREAD; if (tcsetattr(x.pty_slave, TCSANOW, &t) < 0) goto out;
    if (attach_h4(&x) < 0) { perror("H4 attach");goto out; }
    rc = run_bridge(&x, duration_ms);
    if (report_hci(&x) && !rc)rc=-1;
    printf("bridge_result=%d commands=%u events=%u ibs_wake_rx=%u ibs_ack_rx=%u queued=%zu\n",
           rc,x.commands,x.events,x.ibs_wake_rx,x.ibs_ack_rx,x.pending_count);fflush(stdout);
out:
    if(x.pty_slave>=0) {
        int detached=cleanup_pty(&x);
        printf("bridge_detach_result=%d\n",detached);fflush(stdout);
        if(detached && !rc)rc=-1;
        close(x.pty_slave);
    }
    if(x.pty_master>=0)close(x.pty_master);
    sigaction(SIGINT,&old_int,NULL);sigaction(SIGTERM,&old_term,NULL);
    return rc;
}
#ifndef S22_BT_BRIDGE_EMBED
int main(int argc, char **argv) {
    const char *uart_path = NULL; unsigned long baud = 3000000; int opt;
    static const struct option long_options[] = {
        {"uart", required_argument, NULL, 'u'},
        {"baud", required_argument, NULL, 'b'},
        {"help", no_argument, NULL, 'h'},
        {NULL, 0, NULL, 0}
    };
    while ((opt = getopt_long(argc, argv, "u:b:h", long_options, NULL)) != -1) {
        if (opt == 'u') uart_path = optarg;
        else if (opt == 'b') baud = strtoul(optarg, NULL, 10);
        else { fprintf(stderr, "usage: %s --uart PATH [--baud 3000000]\n", argv[0]); return opt == 'h' ? 0 : 2; }
    }
    if (!uart_path) { fprintf(stderr, "--uart is required\n"); return 2; }
    speed_t speed; if (speed_value(baud, &speed) < 0) { fprintf(stderr, "unsupported baud\n"); return 2; }
    struct bridge x; memset(&x, 0, sizeof(x)); x.uart = -1; x.pty_master = x.pty_slave = -1;
    x.uart = open(uart_path, O_RDWR | O_NOCTTY | O_NONBLOCK | O_CLOEXEC);
    if (x.uart < 0 || set_uart(x.uart, speed) < 0 || openpty(&x.pty_master, &x.pty_slave, x.slave_name, NULL, NULL) < 0) {
        perror("bridge setup");
        if (x.pty_slave >= 0) close(x.pty_slave);
        if (x.pty_master >= 0) close(x.pty_master);
        if (x.uart >= 0) close(x.uart);
        return 1;
    }
    int fl = fcntl(x.pty_master, F_GETFL, 0); if (fl >= 0) (void)fcntl(x.pty_master, F_SETFL, fl | O_NONBLOCK);
    fl = fcntl(x.pty_slave, F_GETFL, 0); if (fl >= 0) (void)fcntl(x.pty_slave, F_SETFL, fl | O_NONBLOCK);
    struct termios pt; if (tcgetattr(x.pty_slave, &pt) == 0) { cfmakeraw(&pt); pt.c_cflag |= CLOCAL | CREAD; (void)tcsetattr(x.pty_slave, TCSANOW, &pt); }
    signal(SIGINT, stop_signal); signal(SIGTERM, stop_signal);
    fprintf(stdout, "%s\n", x.slave_name); fflush(stdout);
    int rc = run_bridge(&x, 0);
    int detached = cleanup_pty(&x);
    printf("bridge_detach_result=%d\n", detached);
    fflush(stdout);
    if (detached < 0 && rc == 0) rc = -1;
    close(x.pty_master); close(x.pty_slave); close(x.uart);
    return rc == 0 ? 0 : 1;
}
#endif
