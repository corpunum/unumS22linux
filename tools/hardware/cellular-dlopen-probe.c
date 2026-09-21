/*
 * Minimal AArch64/Bionic Samsung RIL loader probe.
 *
 * This performs only dlopen(RTLD_NOW) and one dlsym lookup.  It never calls
 * RIL_Init, invokes a RIL callback, opens a device/socket, touches EFS/NV, or
 * calls dlclose.  dlopen constructors are vendor code and therefore this
 * binary must only be run by a separately supervised, device-free parent.
 */
#include <dlfcn.h>
#include <stdio.h>
#include <unistd.h>

int main(void) {
    const char *path = "/vendor/lib64/libsec-ril.so";
    const char *name = "RIL_Init";

    fprintf(stdout, "stage=before_dlopen path=%s\n", path);
    fflush(stdout);
    void *handle = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (handle == NULL) {
        fprintf(stderr, "stage=dlopen error=%s\n", dlerror());
        fflush(stderr);
        _exit(2);
    }
    fprintf(stdout, "stage=after_dlopen\n");
    fflush(stdout);

    fprintf(stdout, "stage=before_dlsym symbol=%s\n", name);
    fflush(stdout);
    dlerror();
    void *symbol = dlsym(handle, name);
    const char *error = dlerror();
    if (symbol == NULL || error != NULL) {
        fprintf(stderr, "stage=dlsym missing=%s error=%s\n",
                name, error != NULL ? error : "null");
        fflush(stderr);
        _exit(3);
    }
    fprintf(stdout, "stage=dlsym found=%s\n", name);
    fprintf(stdout, "result=symbol_resolved calls=none dlclose=none\n");
    fflush(stdout);
    _exit(0);
}
