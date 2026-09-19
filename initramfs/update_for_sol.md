# Update for Sol — the argv[0] fix didn't solve it; new decisive evidence

Thank you — the `native_busybox` → `busybox` argv[0] bug was real and confirmed
(reproduced locally with `qemu-aarch64-static -0`, exact "applet not found" / exit 127
match). But after fixing it and doing several more rounds of isolation testing, the
device still crash-loops, and I now have evidence that rules out shell/busybox entirely.

## What I tried after your fix, in order

**V6**: busybox correctly named `/busybox`, shebang `#!/busybox sh`. Init does only:
mount devtmpfs/proc/sysfs, write one line to `/dev/kmsg`, then `while true; do sleep 10;
done`. No modules, no USB, no configfs. Observed via USB polling: survived noticeably
longer than the pre-fix builds (~110-140s vs the original consistent ~60s), then reset.
Could not confirm definitively since this build had no persistent logging.

**V7**: same as V6 + persistent logging to a file on the CACHE partition (mounted via
`mount -t ext4 /dev/block/by-name/cache /cache`, chosen because it's Android's own
standard always-safe-to-wipe recovery-log scratch space). Crashed. **No log file was
ever written** - discovered afterward that `/dev/block/by-name/cache` doesn't exist in
our minimal environment (those symlinks are created by Android's `ueventd`, which we
never launch; only raw `/dev/block/sdaNN` nodes exist via devtmpfs alone).

**V8**: V7 + explicitly `insmod` the `s3c2410_wdt.ko` watchdog module first (found
`s3c2410_wdt.tmr_atboot=1` in the real kernel cmdline captured from a successful AOSP
boot, meaning that watchdog driver auto-arms and self-refreshes once loaded - theorized
this might be why a from-cold, zero-module boot could still get killed by a
bootloader-armed hardware watchdog that nothing ever feeds). Still crashed - same
signature.

**V9**: V8 + fixed the cache mount to use the real raw device (`/dev/block/sda33`,
confirmed via `/dev/block/by-name/cache -> sda33` on a working boot) instead of the
nonexistent symlink path. **Still no log file appeared after the crash**, despite the
path now being correct and mountable in principle.

**V10 - the decisive one**: threw away busybox/shell entirely. Wrote a ~50-line static
C program (`aarch64-linux-gnu-gcc -static`) as the ENTIRE ramdisk content:
```c
int main(void) {
    mkdir("/dev",0755); mkdir("/proc",0755); mkdir("/sys",0755); mkdir("/cache",0755);
    mount("devtmpfs","/dev","devtmpfs",0,NULL);
    mount("proc","/proc","proc",0,NULL);
    mount("sysfs","/sys","sysfs",0,NULL);
    wr("/dev/kmsg","[cinit] PID1 alive, about to mount cache\n");
    int mounted = (mount("/dev/block/sda33","/cache","ext4",0,NULL)==0);
    if (mounted) wr("/cache/cinit_log.txt","===== CINIT BOOT =====\n...");
    else wr("/dev/kmsg","[cinit] cache mount FAILED\n");
    for (;;) { /* heartbeat to kmsg + cache log every 5s */ sleep(5); }
}
```
Confirmed the binary is a valid static aarch64 ELF (`file` output confirms), confirmed
it runs correctly under `qemu-aarch64-static` locally. Ramdisk was 335KB compressed,
containing ONLY `/init` (this binary) plus empty `/dev /proc /sys /cache` directories -
nothing else at all, no busybox, no libraries (statically linked), no sepolicy, nothing.

**Result: identical crash-loop.** Same visual signature (static bootloader splash,
reset, repeat). And critically: **no evidence in `/dev/kmsg` either** - if this program
had executed even the very first `wr()` call before crashing, that message would have
gone to the kernel ring buffer, and if the RESET were a clean reboot (not power loss),
I'd expect *some* chance of it surviving into a subsequent dmesg read via pstore -
though as established, pstore has been empty after every single crash tonight, on every
build, including this one.

## What this rules out
- Busybox multicall/argv[0] dispatch: eliminated (V10 has no busybox at all)
- Shell/ash scripting semantics, `exec` failure modes, PATH issues: eliminated
- The specific watchdog module theory: inconclusive but didn't fix it (V9 had the
  module loaded, V10 doesn't, both fail identically)
- Our own image-packaging pipeline (mkbootimg + avbtool args): eliminated earlier via
  the control test (Lineage's UNCHANGED kernel+ramdisk+dtb+dtbo, repacked through our
  own tooling, booted fine)

## The pattern that remains
The ONLY thing that has ever booted successfully all night is a ramdisk with the REAL,
UNTOUCHED AOSP `init` binary at `/init`. EVERY variant that replaces `/init` with
literally anything else - a 70-line busybox ash script, a 20-line minimal busybox ash
script, or a ~50-line statically-linked C binary doing nothing but mount+write+loop -
has crashed identically, with zero observable evidence (no kmsg survival, no pstore, no
persistent log) regardless of what that replacement program actually does internally.

## New questions
1. Given this, do you think this looks like a Samsung-platform-specific integrity check
   tied specifically to `/init`'s identity/binary signature (Knox/RKP/TIMA-style),
   rather than anything about what code runs once exec'd? Is that a known category of
   protection on Samsung Exynos devices in Android 12+ recovery/boot chains
   specifically, as opposed to the main system boot path?
2. Is there a known mechanism (dm-verity metadata, AVB chain descriptor,
   Samsung-specific "recovery-from-boot.p" patch mechanism, or something else) that
   could validate/require the ramdisk's `/init` file specifically match an expected
   hash or signature, INDEPENDENT of the overall AVB footer (which we've set to
   `--algorithm NONE`, matching Lineage's own recovery signing approach exactly, and
   which by definition performs no cryptographic verification)?
3. Zero kmsg survival across 5 different builds, even ones that should have written to
   `/dev/kmsg` in their very first line of code, is itself strange. What would explain
   a crash so early/hard that not even the pstore RAM console backend
   (`CONFIG_PSTORE_CONSOLE` - need to check if this is actually enabled, will verify)
   captures a single line? Does this suggest the failure might be happening BEFORE the
   kernel even reaches userspace at all (i.e., in the kernel's own initramfs unpacking
   or `run_init_process()` call), rather than inside our program's first instructions?
4. Given all this, is it worth trying an approach where we DON'T replace `/init` at
   all, but instead let the real AOSP `init` run as normal (known to work), and instead
   try to intercept/redirect much later in the AOSP boot sequence (e.g., via an
   `init.rc` modification that execs our own program from a service definition, once
   the "known-good" boot environment is already fully up)? This would sacrifice the
   "zero Android userspace" purity temporarily but might let us make forward progress
   by building on the ONE thing that's proven to work tonight.
