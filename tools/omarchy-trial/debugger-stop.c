/* Diagnostic-only preload: stop Hyprland before main so gdb can attach. */
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

__attribute__((constructor)) static void stop_for_debugger(void) {
    const char *enabled = getenv("S22_DEBUGGER_STOP");
    if (enabled && !strcmp(enabled, "1")) {
        char path[4096];
        ssize_t length = readlink("/proc/self/exe", path, sizeof(path) - 1);
        if (length <= 0) return;
        path[length] = 0;
        const char *base = strrchr(path, '/');
        if (!base || strcmp(base + 1, "Hyprland")) return;
        unsetenv("S22_DEBUGGER_STOP");
        raise(SIGSTOP);
    }
}
