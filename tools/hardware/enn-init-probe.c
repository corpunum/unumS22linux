/*
 * Bounded ENN initialization-only probe for the exact staged wrapper.
 *
 * This binary is for the supervised Android/Bionic sandbox only.  It loads
 * the exact public wrapper, resolves the no-argument C++ ABI entry point, and
 * calls it once.  It deliberately does not call model, buffer, device, or
 * deinitialization APIs.  _exit() avoids vendor atexit/fini cleanup after the
 * status has been flushed; the supervisor owns the process deadline.
 */
#include <dlfcn.h>
#include <stdio.h>
#include <unistd.h>

static const char kWrapperPath[] =
    "/vendor/lib64/libenn_public_api_cpp_lib.so";
static const char kInitializeSymbol[] = "_ZN3enn3api13EnnInitializeEv";

typedef int (*enn_initialize_fn)(void);

static int fail(int code, const char *stage, const char *detail) {
    fprintf(stderr, "stage=%s error=%s\n", stage, detail);
    fflush(stderr);
    return code;
}

int main(void) {
    void *handle;
    void *symbol;
    const char *error;
    enn_initialize_fn initialize;
    int status;

    fprintf(stdout, "stage=before_dlopen path=%s\n", kWrapperPath);
    fflush(stdout);
    handle = dlopen(kWrapperPath, RTLD_NOW | RTLD_LOCAL);
    if (handle == NULL) {
        error = dlerror();
        return fail(2, "dlopen", error != NULL ? error : "unknown");
    }
    fprintf(stdout, "stage=after_dlopen\n");
    fflush(stdout);

    fprintf(stdout, "stage=before_dlsym symbol=%s\n", kInitializeSymbol);
    fflush(stdout);
    dlerror();
    symbol = dlsym(handle, kInitializeSymbol);
    error = dlerror();
    if (symbol == NULL || error != NULL) {
        return fail(3, "dlsym", error != NULL ? error : "null");
    }
    fprintf(stdout, "stage=dlsym_found symbol=%s\n", kInitializeSymbol);
    fflush(stdout);

    /* POSIX specifies dlsym's result as a symbol address; the wrapper is a
     * function symbol, and this conversion is the ABI boundary under test. */
    initialize = (enn_initialize_fn)symbol;
    fprintf(stdout, "stage=before_enn_initialize\n");
    fflush(stdout);
    status = initialize();
    fprintf(stdout, "result=enn_initialize status=%d deinitialize=none\n", status);
    fflush(stdout);

    /* Do not run vendor atexit/fini handlers after the isolated call. */
    _exit(status == 0 ? 0 : 4);
}
