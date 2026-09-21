/*
 * Minimal AArch64/Bionic ENN loader probe.
 *
 * It intentionally performs only dlopen(RTLD_NOW) and dlsym lookups.  It
 * never calls an ENN function, opens a device, maps a model, allocates a
 * buffer, or issues an ioctl.  dlopen constructors belong to the vendor ELF
 * and are therefore observable side effects for the supervised parent; this
 * binary is not to be run on the host.
 */
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>

static const char *const required_symbols[] = {
    "_ZN3enn3api13EnnInitializeEv",
    "_ZN3enn3api15EnnDeinitializeEv",
    "_ZN3enn3api14EnnGetMetaInfoE19_enn_meta_type_id_emPc",
    "_ZN3enn3api12EnnOpenModelEPKcPm",
    "_ZN3enn3api22EnnOpenModelFromMemoryEPKcjPm",
    "_ZN3enn3api13EnnCloseModelEm",
};

int main(int argc, char **argv) {
    const char *path = argc > 1 ? argv[1] : "libenn_public_api_cpp_lib.so";
    size_t i;

    fprintf(stdout, "stage=before_dlopen path=%s\n", path);
    fflush(stdout);
    void *handle = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (handle == NULL) {
        fprintf(stderr, "stage=dlopen error=%s\n", dlerror());
        fflush(stderr);
        return 2;
    }
    fprintf(stdout, "stage=after_dlopen\n");
    fflush(stdout);

    for (i = 0; i < sizeof(required_symbols) / sizeof(required_symbols[0]); ++i) {
        fprintf(stdout, "stage=before_dlsym symbol=%s\n", required_symbols[i]);
        fflush(stdout);
        dlerror();
        void *symbol = dlsym(handle, required_symbols[i]);
        const char *error = dlerror();
        if (symbol == NULL || error != NULL) {
            fprintf(stderr, "stage=dlsym missing=%s error=%s\n",
                    required_symbols[i], error != NULL ? error : "null");
            fflush(stderr);
            return 3;
        }
        fprintf(stdout, "stage=dlsym found=%s\n", required_symbols[i]);
        fflush(stdout);
    }
    fprintf(stdout, "result=all_symbols_resolved calls=none dlclose=none\n");
    fflush(stdout);
    return 0;
}
