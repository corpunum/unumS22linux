/* Stub libvndksupport.so: no linker namespaces outside Android, so "sphal" loads are plain dlopen. */
#include <dlfcn.h>
void *android_load_sphal_library(const char *name, int flag) { return dlopen(name, flag); }
int android_unload_sphal_library(void *handle) { return dlclose(handle); }
int android_is_in_vendor_process(void) { return 1; }
