/* Stub libandroid_net.so for gpsd outside Android: no network selection, plain getaddrinfo. */
#include <errno.h>
#include <netdb.h>
#include <stdint.h>
typedef uint64_t net_handle_t;
int android_setsocknetwork(net_handle_t network, int fd) { (void)network; (void)fd; return 0; }
int android_setprocnetwork(net_handle_t network) { (void)network; return 0; }
int android_getaddrinfofornetwork(net_handle_t network, const char *node, const char *service,
                                  const struct addrinfo *hints, struct addrinfo **res) {
    (void)network;
    return getaddrinfo(node, service, hints, res);
}
