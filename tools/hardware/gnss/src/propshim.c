/* propshim: LD_PRELOAD replacement for Bionic's property getters/setters, for running stock
 * vendor binaries (gpsd, the GNSS HAL) without Android's property service.
 * __system_property_get reads /vendor/etc/gnss/linux.prop ("key=value" lines); keys that are not
 * listed read as "" (the property is absent, as before). __system_property_set only updates the
 * in-process table and logs to stderr; nothing leaves the process.
 * Why: stock gpsd waits forever for service.bootanim.exit=1 before starting. */
#include <pthread.h>
#include <stdio.h>
#include <string.h>

#define PROP_VALUE_MAX 92
#define MAXP 64
static char keys[MAXP][96], vals[MAXP][PROP_VALUE_MAX];
static int n;
static pthread_once_t once = PTHREAD_ONCE_INIT;
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;

static void load(void) {
    FILE *f = fopen("/vendor/etc/gnss/linux.prop", "r");
    char line[256];
    if (!f) return;
    while (n < MAXP && fgets(line, sizeof(line), f)) {
        char *eq = strchr(line, '=');
        if (line[0] == '#' || !eq) continue;
        *eq = 0;
        char *v = eq + 1;
        v[strcspn(v, "\r\n")] = 0;
        snprintf(keys[n], sizeof(keys[n]), "%s", line);
        snprintf(vals[n], sizeof(vals[n]), "%s", v);
        n++;
    }
    fclose(f);
}

int __system_property_get(const char *name, char *value) {
    pthread_once(&once, load);
    pthread_mutex_lock(&mu);
    for (int i = 0; i < n; i++)
        if (!strcmp(keys[i], name)) {
            int l = snprintf(value, PROP_VALUE_MAX, "%s", vals[i]);
            pthread_mutex_unlock(&mu);
            return l;
        }
    pthread_mutex_unlock(&mu);
    value[0] = 0;
    return 0;
}

int __system_property_set(const char *name, const char *value) {
    pthread_once(&once, load);
    pthread_mutex_lock(&mu);
    int i;
    for (i = 0; i < n && strcmp(keys[i], name); i++) {}
    if (i < MAXP) {
        snprintf(keys[i], sizeof(keys[i]), "%s", name);
        snprintf(vals[i], sizeof(vals[i]), "%s", value ? value : "");
        if (i == n) n++;
    }
    pthread_mutex_unlock(&mu);
    fprintf(stderr, "propshim: set %s=%s\n", name, value ? value : "");
    return 0;
}
