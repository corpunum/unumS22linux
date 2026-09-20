# Native Init Experiments — 2026-09-18/19 session

## Summary
Attempted to replace Android's AOSP `/init` with a custom native-Linux init in the
recovery ramdisk, using the proven-working LineageOS kernel/DTB/DTBO. Every variant
that replaces `/init` crash-loops identically (static bootloader splash → reset,
repeating, no observable kernel/userspace output, empty pstore every time). The one
thing that has ever booted successfully all session is the real, untouched AOSP `/init`.

## Isolation results, most to least recent

| Build | What changed vs. known-good | Result |
|---|---|---|
| V11 | Untouched `/init`, added one harmless extra file to ramdisk | **BOOTS FINE** |
| Control test | Untouched kernel+ramdisk+dtb+dtbo, repacked via our own tooling | **BOOTS FINE** |
| V10 | `/init` = tiny static C binary (mount+kmsg+cache-log+loop), everything else removed | crash-loop |
| V9 | V8 + fixed cache mount to use raw `/dev/block/sda33` instead of nonexistent by-name symlink | crash-loop, still zero log |
| V8 | V7 + `insmod s3c2410_wdt.ko` first (watchdog-feed theory) | crash-loop |
| V7 | V6 + attempted persistent cache logging (used broken by-name path, so no log ever written) | crash-loop |
| V6 | `/init` = minimal busybox ash script (mount + kmsg + infinite loop), busybox correctly named | crash-loop (survived longer than pre-fix builds per USB timing, unconfirmed) |
| V4 | Exact working ramdisk (sepolicy, lib64, everything intact), ONLY `/init` swapped to busybox script (busybox misnamed `native_busybox` at this point) | crash-loop |
| V2/V3 | Full ramdisk replacement, 324 then 12 modules, busybox misnamed | crash-loop |

## Confirmed bugs found and fixed along the way (real, but not the root cause)
1. **Missing AVB footer**: our first custom images had no AVB footer at all, causing a
   distinct earlier failure (`SVB Fail! recovery: No footer detected`) visible on-screen
   before the kernel was even reached. Fixed by adding `avbtool add_hash_footer
   --algorithm NONE --rollback_index 0`, matching LineageOS's own BoardConfig exactly.
2. **Busybox argv[0]/multicall bug**: naming the binary `native_busybox` broke busybox's
   applet dispatch (`argv[0]` must be `busybox` or a known applet name), causing
   `applet not found`, exit 127, PID 1 death, kernel panic. Reproduced and confirmed
   locally via `qemu-aarch64-static -0`. Real bug, fixed by naming it `/busybox`.
3. **Broken persistent logging**: `/dev/block/by-name/cache` doesn't exist without
   `ueventd` running (those symlinks are created by it, not by devtmpfs alone). Fixed
   by using the raw device (`/dev/block/sda33`) directly. Even after fixing this, no
   log was ever produced by any custom-init build — see open issues below.
4. **vbmeta DOES chain-reference the recovery partition** (`Chain Partition descriptor`,
   rollback index 1, keyed to the stock signing key) — confirmed via `avbtool
   info_image` on the stock vbmeta. However this applies equally to LineageOS's own
   working recovery (also `algorithm NONE`, unsigned) and the bootloader is unlocked
   (AVB failures warn, don't block), so this does not explain the differential
   success/failure we're seeing. Real finding, not the root cause.

## What's now conclusively ruled out
- Module loading content/order/count (V2 with 324 modules and V3 with 12 both failed
  identically; V10 with zero modules also failed identically)
- Busybox/shell-specific bugs (V10's pure static C binary failed identically)
- Our image-packaging pipeline / mkbootimg+avbtool arguments (control test + V11 both
  succeed using the same pipeline)
- General ramdisk content modification / any-change-breaks-verification (V11 succeeds
  with a modified ramdisk, real `/init` untouched)

## What remains genuinely open
The failure is narrowed to "replacing what runs as PID 1" specifically, on this exact
kernel, but the mechanism is unconfirmed. Leading candidates from external review
(3 independent AI consultations, saved in `initramfs/nativev5_notes_for_sol.md` and
`initramfs/update_for_sol.md`, cross-model responses in `~/ttta/`):
- An early kernel/bootloader hand-off issue specific to how `run_init_process()`
  interacts with something the real AOSP init does immediately (SELinux policy load,
  a specific mount, a specific watchdog pet) that a replacement program doesn't
  replicate, one we haven't yet identified.
- Possibly Samsung DEFEX (Defeat Exploit LSM) - though this is disputed: DEFEX is not
  typically present in third-party GKI-style kernels like the LineageOS build we're
  using, which weakens this theory, but hasn't been definitively ruled out (would need
  to grep the actual kernel source/config for `defex` symbols to settle it).
- Our own logging methodology was unvalidated: no `fsync()`/`O_SYNC` on cache writes
  (page-cache writes are lost on a hard reset regardless of whether the program ran
  fine), no `/dev/console` node created in any custom ramdisk (so even a kernel-level
  panic message had nowhere to land that we were capturing), and pstore was never
  validated as working on this device via a deliberate known-good crash-and-recover
  test. This means "zero evidence survived" is NOT strong evidence of "crashed
  immediately" - it may simply reflect broken instrumentation.

## Recommended next steps (not yet attempted)
1. **Fix the instrumentation first**: create `/dev/console` (c 5 1) in any future custom
   ramdisk, use `O_SYNC`/`fsync()` for the cache log, and validate pstore/last_kmsg
   actually works on THIS kernel by deliberately crashing a known-good (real-init) boot
   (`echo c > /proc/sysrq-trigger`) and confirming the crash is readable afterward.
2. **`panic=` as a free oracle**: build the same failing custom-init image twice, once
   with `panic=5` and once with `panic=60` on the cmdline. If the reset timing changes
   between the two, the kernel IS running and genuinely panicking (userspace-triggered).
   If the timing doesn't change at all, the kernel likely never reaches that point,
   pointing to an earlier hand-off failure.
3. **Grep the kernel source for `defex`** to settle whether that specific Samsung LSM
   is compiled into this exact kernel build, one way or the other, instead of debating
   it without evidence.
4. **Real UART console** (if a 619kΩ ID-resistor jig or similar is ever available) would
   settle all of this immediately - `console=ttySAC0,115200 earlycon initcall_debug`.
5. **Pragmatic path forward in the meantime**: keep the real AOSP `/init`, inject our
   own code later via a custom `init.rc` service definition (started after SELinux/
   mounts/ueventd are already up), get a working log/exec path end-to-end using the
   known-good environment, then work backward - moving code earlier and earlier until
   the actual boundary that breaks is found, rather than continuing to guess blind at
   full `/init` replacement.

## Session state at stop
Phone is safely on LineageOS recovery (V11 build - which is functionally identical to
official Lineage recovery, so no distinction from stock behavior). No physical damage,
one fully-recovered incident (MISC secure-check failure, documented in
evidence/lineage_recovery_boot/MILESTONE.md and FLASH_LOG.md). All artifacts, builds,
and full firmware/toolchain preserved on disk under ~/s22-linux/ for continuation.

## Update — later same session, cross-model consultation round 2

Consulted local Qwen (via raw llama.cpp API) twice with generous token budgets (3000,
then 6000) - both times it got stuck in unbounded reasoning loops re-deriving Android
boot architecture from scratch and never produced a final answer (finish_reason:
"length" both times, zero actual content). Not useful in that mode.

Routed the same question through **OpenUnum's own `chat` CLI** instead (same underlying
Qwen model, but with OpenUnum's multi-turn agentic/self-critique orchestration around
it) - this converged to an actual, coherent, structured answer on the second internal
iteration. Key points from that answer:
- Ruled out `sec_debug` as the gate (post-hoc logger only)
- Candidate: a hardware watchdog armed late in kernel init, before `/init` runs; a
  substitute `/init` that never reaches whatever "disarm point" real AOSP init hits
  gets silently reset
- Confirmed (matches our own reasoning): symlink-vs-regular-file is irrelevant to
  `execve` - pathwalk resolves symlinks before the ELF loader ever sees the file
- `panic=20` interpretation: reset at ~20s → genuine kernel panic → our program DID
  exec and die (userspace problem). Reset at a different/unchanged time → kernel never
  panicked at all → external/hardware watchdog fired before our code ever ran
- **New concrete experiment** (not yet tried before this suggestion): don't just swap
  `/init` for a different program - make `/init` a trivial wrapper that does ONE benign
  thing (write to kmsg) then chain-`execve()`s the REAL, unmodified AOSP init kept at a
  fresh path (`/real_init`). If this ALSO crash-loops, it proves the kernel/watchdog
  reacts to anything running before real init, independent of program identity/content.

Manually found (not from AI consultation - just reading the actual working ramdisk's
own init.rc files) a second concrete candidate: `init.recovery.s5e9925.rc` explicitly
starts `service watchdogd /system/bin/watchdogd 10 20` very early - a REAL userspace
daemon (confirmed present as a dynamically-linked ELF at `/system/bin/watchdogd`) whose
entire job is petting a 30-second hardware watchdog every 10 seconds. None of our
custom `/init` replacements have ever started this. Caveat: V8 already loaded the
KERNEL DRIVER (`s3c2410_wdt.ko`) that auto-refreshes the same underlying watchdog once
loaded, and crashed identically anyway - so this exact theory already took one hit,
but `watchdogd` might be a distinct/additional mechanism worth testing cleanly.

## Two new builds ready for next session (NOT flashed - user was away from device)

- **V13** (`native_recovery_v13.img`, SHA256
  `798d327fae2ce04bb9b95bff1c0156430642a17542228a50af0b32d8af278e5`): full working
  ramdisk tree, `/init` = static C binary that forks+execs the real
  `/system/bin/watchdogd 10 20` first, then does the same fsync'd cache logging as V12.
  cmdline includes `panic=20 initcall_debug ignore_loglevel loglevel=8`.

- **V14** (`native_recovery_v14.img`, SHA256
  `6daf0910621ef10c6e909f3788d5681a69ecc1c0edd03e3d698dd5f086897d8`) - **recommended to
  try first**: full working ramdisk tree, real AOSP init preserved at `/real_init`,
  `/init` = a ~15-line wrapper that mounts devtmpfs, writes one line to `/dev/kmsg`,
  then `execl("/real_init", "/init", NULL)`. Same `panic=20` cmdline. This is the
  cleanest possible test of "does anything running before real init break things,
  independent of what it does."

Recommended order: flash V14 first (most informative, cheapest to interpret). If it
boots fine, the mystery is specifically about REPLACING what real init does, not about
"anything running first" - try V13 next. If V14 crash-loops, that's a major new finding
in itself (points squarely at a watchdog/hardware mechanism keyed on early execution
timing, not program identity) and would make V13 lower priority.

## Update — Opus 5 independent review (major corrections)

Consulted Opus 5 (via Agent tool with model override) for an independent second review,
explicitly asked to find mistakes rather than validate conclusions. It unpacked the
actual boot images and extracted the real kernel's embedded config (IKCONFIG) rather
than trusting our defconfig source tree. Found two genuine methodological errors that
overturn conclusions stated earlier tonight:

### Error 1: This kernel has no devtmpfs
`CONFIG_DEVTMPFS is not set` in the actual running kernel's embedded config. Every
custom init before V12 called `mount("devtmpfs", ...)`, which always fails with ENODEV
on this kernel. V10 in particular (`cinit.c`) has zero `mknod` calls at all - verified
via `grep -c mknod cinit.c` = 0 - meaning it could not have produced a single byte of
log output no matter how well it ran. "Zero evidence" from V10 was never evidence of
anything; it's fully explained by logging that could never have worked. V12-V14 did add
`mknod` for character devices (kmsg, console) after Sol's earlier feedback, but NONE of
our builds ever `mknod`'d the raw block device (`/dev/block/sda33`) - they all relied on
devtmpfs to create it automatically, which never happened. So the cache-partition
logging was broken in every single build for this reason, independent of the by-name
vs. raw-path fix in V9. Real AOSP `first_stage_init.cpp` doesn't use devtmpfs either -
it mounts tmpfs on /dev and mknods everything itself, which is exactly why it's
unaffected by this.

### Error 2: V8's watchdog module test was invalid
`s3c2410_wdt.ko` depends on `dss.ko` and `exynos-pmu-if.ko` (confirmed directly via
`modules.dep`: `s3c2410_wdt.ko: dss.ko exynos-pmu-if.ko`). V8 did a bare `insmod
s3c2410_wdt.ko` with no dependencies loaded first - this fails on unresolved symbols
before the driver ever probes. The "loading the kernel module didn't help, so the
watchdog theory took a hit" conclusion from earlier tonight was wrong - the module
almost certainly never loaded successfully at all, so the theory was never actually
tested. (One specific factual claim in the same review, that `clk_exynos` loads AFTER
`s3c2410_wdt` in `modules.load`, was checked directly against the file and found
incorrect - `clk_exynos` is at position 4, `s3c2410_wdt` at position 6, so clk_exynos is
fine. The dependency-chain finding on dss/exynos-pmu-if is independently verified
correct via modules.dep though.)

### Also clarified: the real pstore mechanism
No `ramoops` DTB node exists on this device - Samsung uses `debug_snapshot` reserved
memory instead. The actual crash-log persistence mechanism is `/proc/last_kmsg`, backed
by `dss.ko` (the debug-snapshot driver) using reserved DRAM that survives warm resets.
Confirmed live: `/proc/last_kmsg` on the current known-good boot returns real (if
garbled/corrupted) content - the mechanism genuinely exists and has never been used by
any of our custom-init attempts, since `dss.ko` was never loaded by any of them.

### Opus's ranked theory (differs from prior "watchdog vs anything-before-init" framing)
Most likely: PID 1 runs completely fine and the SoC is reset by an **unmanaged hardware
watchdog** - `CONFIG_WATCHDOG_HANDLE_BOOT_ENABLED=y` means the kernel auto-pings a
"boot-enabled" watchdog forever, but ONLY once a driver registers it with the watchdog
core. Since `s3c2410_wdt` never successfully loads in any of our builds (see Error 2),
a bootloader-armed hardware watchdog runs completely unmanaged and fires on its own
hardware timeout, independent of any panic/crash in our code at all. This is considered
MORE likely than an actual PID 1 crash, since simple mount/mkdir/sleep code has nothing
obvious to fault on.

### Two new builds, ready, NOT yet flashed
- **V16** (`native_recovery_v16.img`, SHA256
  `efe77940c820d052c81085c2fe76e2d546dee31ab317448f90070d8bed24a91`) - the cheapest
  possible disambiguator (Opus's suggestion): exact working ramdisk tree, `/init` = a
  byte-identical copy of the real `/system/bin/init` binary as a REGULAR FILE (not a
  symlink), confirmed via `cmp` to be byte-for-byte identical to the working init. If
  this crash-loops, the bug is in our own file-creation/packaging pipeline, not program
  identity, and every other conclusion needs re-examination. If it boots fine (expected),
  it cleanly confirms symlink-vs-regular-file was never the variable and our tooling is
  sound for this specific change.

- **V15** (`native_recovery_v15.img`, SHA256
  `00a03e613523921f4338b91d874a565123ead606ebfd0d165ceb0697c002972`) - the real fix
  attempt, addressing both errors above: mounts tmpfs (not devtmpfs) and manually
  `mknod`s every device needed including the raw cache block device
  (`/dev/block/sda33`, confirmed major 259 minor 17 from `/proc/partitions`); loads
  `exynos-pmu-if.ko` then `dss.ko` FIRST (deliberately out of official order) so
  `/proc/last_kmsg` has the best chance of capturing everything from this boot onward
  if it dies later; loads the remaining real module chain in official dependency order
  through the USB-critical set; opens the real `/dev/watchdog` device and pets it
  directly every second, AND forks the real `watchdogd` binary as a second independent
  mechanism; logs to both kmsg and a properly-mounted (now that the block device node
  actually exists) cache partition file. Critically includes Opus's **self-reboot
  timing oracle**: after 45 seconds of successfully petting the watchdog and logging
  heartbeats, it deliberately calls `reboot()` itself. If the observed reset timing
  matches ~45 seconds, that's conclusive proof PID 1 was alive and healthy that whole
  time - meaning the "crash-loop" framing has been wrong all along, and something else
  (the unmanaged watchdog) is resetting the SoC on its own schedule, nothing to do with
  our code failing.

Recommended order: **V16 first** (30 seconds to interpret, disambiguates tooling vs.
program identity), **then V15** (the actual fix + decisive timing oracle).

## Update — Codex (GPT-6 Astra medium) review of V15, before flashing

Consulted Codex with full sandbox access to read files directly (not pasted context).
It found real, concrete bugs in V15 that would have wasted the flash cycle:

1. **V15 never loaded the UFS storage controller (`ufs-exynos-core.ko`) at all.** A
   block device node is just a name and device number - it doesn't initialize the
   storage controller. V15's cache mount was doomed regardless of the device-node fix,
   because nothing ever told the kernel the storage hardware exists.
2. **V15's "dependency-respecting because it's the official list order" claim was
   false.** `modules.load` lists requested modules; AOSP's actual loader resolves
   dependencies via `modules.dep`, which straight-line `finit_module()` calls do not
   replicate. Verified independently: simulating V15's exact sequence against the real
   `modules.dep` showed 23 of 47 attempts had unmet dependencies at the point they were
   attempted.
3. **The module-error logger had a real bug**: `syscall()` returns -1 (not -errno) on
   failure, so `log_mod(name, ret==0, ret==0?0:-ret)` always logged "errno=1" regardless
   of the actual failure reason - verified and fixed.
4. **PID 1 and a forked `watchdogd` cannot both open `/dev/watchdog`** - the kernel
   enforces single-open access (EBUSY). V15's belt-and-suspenders approach meant only
   one could ever actually work, silently.
5. **Corrected the watchdog theory itself**: this driver's `tmr_atboot` defaults to 0,
   and its probe explicitly STOPS any watchdog left running by the bootloader. The
   "kernel auto-feeds a boot-enabled watchdog once the driver loads" claim requires
   `WDOG_HW_RUNNING`, which this Samsung driver does not set. The unmanaged-watchdog
   theory remains plausible but is not proven by config alone - and there's a
   *separate* fast path: this driver's own panic notifier can arm a 5-second watchdog
   reset directly, independent of `panic_timeout`/`panic=N`, which would explain why the
   `panic=20` cmdline test never showed the expected ~20s timing.
6. Confirmed `CONFIG_PANIC_TIMEOUT=-1` in the real embedded kernel config (compiled
   default, before any cmdline override).
7. The 45-second timing oracle measured loop iterations plus untracked module-loading
   time, not true monotonic elapsed time since PID1 start - fixed with
   `clock_gettime(CLOCK_MONOTONIC)`.
8. Verified via static checks (not requiring a flash): V15/V16 images' kernel/DTB/DTBO
   payloads match the shipping image exactly; packaged `/init` matches source exactly
   in both; V15 is confirmed static ELF with no dynamic interpreter; diffing V11 vs.
   V15/V16 archives shows only `/init` differs - packaging hygiene is sound.

### Fix: discovered busybox modprobe was silently broken
Independently (verifying Codex's suggestion to test locally before flashing), found
`CONFIG_DEFAULT_MODULES_DIR=""` in our busybox build - an empty string, meaning
`busybox modprobe` would always fail immediately with `can't change directory to ''`
regardless of module placement. Rebuilt busybox with
`CONFIG_DEFAULT_MODULES_DIR="/lib/modules"` and verified via a local chroot +
binfmt_misc test that dependency resolution now works correctly: requesting `dss`
correctly loads `exynos-pmu-if` first (its dependency), and requesting
`ufs-exynos-core` correctly walks its entire ~26-module transitive closure in
dependency order (verified against the real `modules.dep`, all module load failures
were "Function not implemented" - expected, since we were testing ARM64 `.ko` files
against an x86_64 host kernel purely to validate the RESOLUTION LOGIC, not real loading).

### V17 - supersedes V15, all known issues fixed
`native_recovery_v17.img`, SHA256
`8e28ae6b537c41cabfd4e95e35bf07e303dce6dcd55ea77bfb5ab2660af343f1`, exactly
100,663,296 bytes (fits the partition exactly after AVB footer padding).

- tmpfs (not devtmpfs) + manual mknod for kmsg/null/console/watchdog/the cache block
  device, exactly as V15, but now console is `dup2`'d onto fds 0/1/2
- `/dev/kmsg` opened WITHOUT `O_CREAT` - fails loudly instead of silently writing to a
  fake regular file if mknod somehow didn't happen
- Module loading now via `fork()+execl("/busybox","modprobe",name)` for each real
  target (`dss`, `ufs-exynos-core`, `s3c2410_wdt`, `phy-exynos-usbdrd-super`,
  `dwc3-exynos-usb`, `usb_typec_manager`, `usb_notify_layer`, `usb_notifier`,
  `usb_f_conn_gadget`) - busybox's own dependency-resolution logic handles the full
  transitive closure correctly for each, verified locally as above
- Busybox rebuilt with the `CONFIG_DEFAULT_MODULES_DIR` fix; modules placed at the
  proper `/lib/modules/5.10.260-g4e5c5ad7d950/` path matching the real kernel's
  `uname()` release string
- Polls `/proc/partitions` for `sda33` to actually appear (bounded, 15s) before
  attempting the cache mount, instead of assuming it's immediately available the
  instant the controller module loads
- Single watchdog owner: direct `open()`+`ioctl(WDIOC_GETTIMEOUT)`+periodic `write()`
  petting only, no forked `watchdogd` competing for the same fd
- Monotonic-clock timing oracle (`clock_gettime(CLOCK_MONOTONIC)`) at a real T=45s,
  logged with proper fractional-second precision
- Early log lines (before cache is mountable) buffered in memory and flushed once the
  mount succeeds, instead of being lost

Recommended flash order unchanged: **V16 first** (cheapest disambiguator, byte-identical
real-init-as-regular-file), **then V17** (supersedes V15 entirely - do not flash V15).

## Update — 3-round Opus<->Astra adversarial review loop, converged

Per explicit instruction, ran an iterative loop: Opus reviews -> findings passed to
Astra (Codex GPT-6, medium reasoning) for cross-check -> corrections applied ->
repeat. Both reviewers had live root ADB access to the phone (still on known-good
LineageOS recovery) throughout and verified claims empirically, not just by reading
source.

### Round 2 (Opus, live-tested against real hardware)
Confirmed via real on-device modprobe/rmmod cycles that busybox's dependency
resolution genuinely works on ARM64 hardware (not just the earlier x86 chroot logic
test). Found: watchdog module loaded too late in V17 (after UFS+cache, undermining the
very theory being tested), the 45s timing oracle was relative not absolute, and
proposed a "POWER_OFF" oracle (a HW watchdog can only reset, never power off) via
`modprobe("exynos-reboot")`. Also found `/proc/last_kmsg` is confirmed corrupted/
bit-rotted on live inspection - real mechanism, unreliable content, don't treat as
primary evidence.

### Round 3 (Astra cross-check, live-tested)
Confirmed round 2's watchdog-ordering and timing-oracle criticisms, but found Opus's
own POWER_OFF proposal was wrong: `exynos-reboot`'s `pm_power_off` assignment is
guarded by `!CONFIG_SEC_REBOOT`, and this kernel has `CONFIG_SEC_REBOOT=m` - so
`exynos-reboot` alone installs nothing; `sec_reboot.ko` owns the real callback here,
and it does a power-key-wait / PMIC-helper sequence that deliberately restarts after 5
failed attempts rather than guaranteeing power-off. Also found a certain (not
probabilistic) bug in `cinit6.c`: `g_kmsg_fd` opened before console `dup2()`, silently
clobbered by fd-0 reuse on this ramdisk's empty `/dev`. Corrected Opus's "zero
headroom" claim by decoding the actual AVB footer math (real free space ~18.5MB).

### Round 4 (Opus final adjudication, live-tested)
Resolved the power-off dispute completely: **PSCI** (`CONFIG_ARM_PSCI_FW=y`, built in,
zero modules needed) already installs both `pm_power_off` and the restart handler at
boot, confirmed via DT (`psci { compatible = "arm,psci-1.0" }`) and kernel source
(`drivers/firmware/psci/psci.c`). Loading `sec_reboot.ko` would *override* these clean
handlers with Samsung's messier retry path - so the plan explicitly avoids loading it.
This also means the primary reset-timing oracle needs zero modules to be trustworthy.
Confirmed the kmsg-fd bug's exact fix. Confirmed on-device that the flat top-level
`/lib/modules/*.ko` copies are dead weight - busybox modprobe only ever reads
`/lib/modules/<uname release>/`, proven by removing the versioned dir and watching it
fail with "can't change directory to '5.10.260-g4e5c5ad7d950'" while the flat copies
sat unused.

### V18 - the converged result, built from `cinit7.c`
`native_recovery_v18.img`, SHA256
`e8c51a1e4e832d507b512ef94014a98f114fec702ea3b914642e7b7edb97d63`, exactly
100,663,296 bytes (payload 68,362,240 bytes, ~32MB free before the AVB footer - up from
V17's ~18.5MB after removing the dead duplicate module copies).

Every agreed fix applied:
- Console wired to fds 0/1/2 **before** opening kmsg; kmsg forced off fd 0-2 via
  `F_DUPFD_CLOEXEC` regardless
- `s3c2410_wdt` loaded and `/dev/watchdog` opened immediately after `dss`, before any
  UFS/cache work - not deferred behind a 15s partition wait and an ext4 mount
- Watchdog petted via a `WNOHANG` `waitpid()` poll loop during **every** modprobe
  child, not just the partition-wait loop
- `modprobe()` child's stderr redirected to the kmsg fd instead of `/dev/null`
- Module load success verified against `/proc/modules` (hyphen->underscore
  normalized) in addition to exit code, since exit 1 was proven on-device to mean
  "already loaded" as often as "real failure" with this busybox's config
- Bound-device sanity check via `/sys/bus/platform/drivers/<name>/` for the three
  drivers that matter most (watchdog, USB, UFS)
- Absolute monotonic-clock deadline (`while (now_mono() < 45.0)`) instead of a
  relative checkpoint taken after setup work already consumed unknown time
- Deliberately does NOT load `sec_reboot.ko` - preserves the clean built-in PSCI
  restart handler the timing oracle depends on
- Ramdisk deduplicated: only the correctly-versioned `/lib/modules/5.10.260-g4e5c5ad7d950/`
  directory ships; the dead flat copies are gone

Verified post-build: unpacked image's `/init` is byte-identical to `cinit7` source;
mkbootimg header args match; versioned module dir has exactly 324 `.ko` files; flat
copies confirmed absent.

**This supersedes V15 and V17 entirely. V18 is the build to flash next**, followed by
V16 (still valid as the cheap disambiguator, unaffected by any of this).

All of this was research/build/local-ADB-verification only - nothing was flashed to
the phone during the entire 4-round loop.

### Rounds 5-6 (Opus, then Astra cross-check) - two real bugs found in V18 itself

Per the user's instruction to keep looping until no corrections are needed, V18
(`cinit7.c`) was passed back through Opus 5 for a fresh round-5 review, then Opus's
findings were cross-checked by Codex/Astra (round 6). Both had live root ADB access
to the phone (still safely on LineageOS recovery) to test claims empirically rather
than theorize.

**Round 5 (Opus) - cosmetic bug, real but low-impact:**
- `platform_bound()`'s driver names were wrong: `check_drivers[] =
  {"s3c2410-wdt", "dwc3-exynos", "ufs-exynos-core"}` - only the watchdog entry is a
  real driver name. Verified live against `/sys/bus/platform/drivers/` on the
  phone: the USB driver is actually named `exynos-dwc3` (not `dwc3-exynos`,
  which is the `.ko` filename, not the registered driver name) and the UFS driver
  is `exynos-ufs` (not `ufs-exynos-core`, again a `.ko`-name/driver-name mixup).
  This meant two of three "bound" diagnostic checks always logged false negatives
  even when the drivers had probed correctly - a misleading log line, not a boot
  failure, but exactly the kind of thing that would send a future debugging round
  down the wrong path.

**Round 6 (Astra) - the significant one, missed by round 5 despite reviewing the
same function:**
- `module_present()` read `/proc/modules` with a single `read()` call into a
  16KB buffer and assumed that returned the whole file. `/proc/modules` is a
  `seq_file` in the kernel - a single `read()` only returns one internal page's
  worth of data (empirically ~4KB on this kernel), not the full listing,
  regardless of buffer size. Astra didn't just theorize this: it compiled the
  actual `module_present()` function out of `cinit7.c` (via a wrapper `#include`
  trick), pushed the resulting binary to the live phone over ADB, and ran it
  against the real, live `/proc/modules` with all 9 target modules already
  loaded. Result: **all 9 reported "absent."** Had V18 been flashed as-is, every
  `modprobe()` diagnostic log line in the entire boot trace would have read
  "proc_modules=no" regardless of whether loading actually succeeded - completely
  invalidating the log as evidence for or against any hypothesis, on the one
  build where getting believable logs is the entire point.
- Astra separately flagged a related but distinct latent bug in `modprobe()`:
  `status` defaulted to 0 and was only ever set by `waitpid()`, so a `fork()`
  failure or a `waitpid()` error (e.g. `EINTR` not retried) would fall through
  to logging a false `exit=0` "success" - the code had no path that distinguished
  "child exited 0" from "we never actually found out."

Both fixes were incorporated into a new build, `cinit8.c` (V19):
- `module_present()` now loops `read()` in a static 32KB buffer until it
  returns 0 (true EOF), exactly like reading any other file - not a seq_file
  special case, just correct file I/O.
- `platform_bound()` now takes `(driver, device_instance)` and checks
  `/sys/bus/platform/drivers/<driver>/<device_instance>` via `access()` - proof
  that *this specific device* bound to the driver, not just that the driver
  registered. Call site updated with the four correct, live-verified pairs:
  `{"s3c2410-wdt","10050000.watchdog_cl0"}`, `{"exynos-dwc3","10b00000.usb"}`,
  `{"exynos-ufs","11100000.ufs"}`, `{"phy_exynos_usbdrd","10aa0000.phy"}`.
  (`exynos-ufs` has `suppress_bind_attrs` set, so the device-instance symlink is
  the only available proof of binding for it - driver-directory existence alone
  proves nothing.)
- `modprobe()` now explicitly handles `fork() < 0` (early return, distinct log
  message, no `waitpid()` call at all) and tracks a `reaped` flag through the
  `WNOHANG` poll loop so `status` is only trusted when a real child exit was
  observed; an unreaped child (persistent `EINTR` aside, genuine `waitpid()`
  error) logs "waitpid FAILED errno=N" instead of a fabricated result.
- `pet_watchdog()` now tracks `g_last_pet_ok` (monotonic timestamp of the last
  successful watchdog write) and this is logged explicitly right before the
  final self-reboot call, giving direct evidence of watchdog health at the
  moment that matters most instead of only inferring it from heartbeat log
  density.

**Empirical re-verification (this session, not requested by either reviewer but
done anyway before considering this converged):** the fixed `module_present()`
was compiled out of `cinit8.c` using the same wrapper-`#include` technique Astra
used, pushed to the live phone, and run against the real live `/proc/modules`
with the same 9 modules loaded. All 9 now correctly report present. This closes
the loop on the round-6 finding with the same standard of evidence Astra used to
find it, not just a code-review-level "this looks right."

`native_recovery_v19.img`, SHA256
`5adfb337ea928edd151492489113fa165e639cd399a50bcdaf67bb574fc31c1`, exactly
100,663,296 bytes (payload 68,372,480 bytes - marginally larger than V18 only
because of the larger static buffer in `module_present()`; module directory
layout unchanged, still 324 `.ko` files only in the versioned dir, zero flat
copies).

Verified post-build: unpacked image's `/init` is byte-identical to `cinit8`
source; mkbootimg header/load-address arithmetic checks out
(`load_addr = base 0x10000000 + offset`, matching V18's proven offsets exactly);
versioned module dir intact; flat copies confirmed absent.

**V19 supersedes V18.** Note that round 6 found a real, previously-missed bug in
round 5's own reviewed code - meaning strict "loop until no corrections are
needed" has not yet reached a round with zero findings. The user's specific
request for "one more round on both" is satisfied by rounds 5+6; whether to run
a round 7 (Opus reviewing V19/`cinit8.c` fresh) before flashing is an open
question for the user rather than assumed either way.

Nothing has been flashed to the phone. It remains on the known-good LineageOS
recovery boot, reachable via root ADB, throughout all of rounds 5-6 and the V19
build.

### Rounds 7-8 (Opus fresh review, then Astra independent re-derivation) - two
long-held project assumptions overturned, four new significant bugs found

The user asked for "another round" on V19 (`cinit8.c`). Round 7 (Opus, given
zero hints that V19 was "already reviewed" beyond the file itself and told to
actively hunt for anything new rather than confirm prior fixes) found several
things no earlier round caught, two of which contradict facts this project had
treated as established for many rounds. Round 8 (Astra) independently
re-derived every claim from kernel source and live ADB evidence rather than
trusting Opus's report, refining several findings and adding four of its own.

**Two "known facts" turned out to be wrong:**

- **`tmr_atboot` is not 0 by default in the way that mattered.** The compiled
  default genuinely is 0, but `/proc/cmdline` on live boot shows the
  bootloader passes `s3c2410_wdt.tmr_atboot=1`. Stock AOSP boot therefore
  starts the watchdog per the bootloader's instruction. This build's busybox
  (`CONFIG_MODPROBE_SMALL`, no `CONFIG_FEATURE_CMDLINE_MODULE_OPTIONS`) never
  reads `/proc/cmdline` at all, so every prior custom-init build silently
  probed the watchdog with the opposite policy from stock - a genuine,
  previously invisible divergence between custom and AOSP boot. Opus's first
  proposed fix (pass `tmr_atboot=1` as an `execl()` argument) does not
  actually work with this busybox - Astra traced `modprobe-small.c` and
  confirmed extra argv is discarded; the only mechanism it honors is an
  `/etc/modules/<name>` options file (`modprobe-small.c:723-728`).
- **`/dev/console` returns ENODEV on this real device.** Confirmed live:
  `echo hi > /dev/console` → "No such device"; `/proc/consoles` has exactly
  one entry, `dss-1`, which has no `.device` callback (traced in
  `debug-snapshot.c` and `printk.c`). Every build back to V17 unconditionally
  logged "console wired" / "console+kmsg fds fixed" regardless of whether the
  open actually succeeded - that claim was false on every single prior run.

**Four new significant findings (Astra, N1-N4):**

- **N1 - the "t=45s deadline" was never actually enforced.** `modprobe()`'s
  `waitpid()` poll loop had no time cap, so a hung child could block the
  entire boot sequence indefinitely; `mount()`/`sync()` are also unbounded
  blocking calls with nothing capping them. Reaching the final log line
  proves execution got there eventually, not that it happened at a
  predictable time - undermining part of the timing-oracle's value as
  originally designed.
- **N2 - `sec_debug.ko` (the module that writes `PANIC_INFORM`/
  `UPLOAD_CAUSE_KERNEL_PANIC` on a real kernel panic) is not in this build's
  module closure.** So `/proc/cmdline`'s `reset_reason` field is useful
  context about the *previous* boot but not a proven panic classifier for a
  crash produced by *this* program specifically.
- **N3 - `CONFIG_S3C2410_SHUTDOWN_REBOOT=y` rearms a separate 30s watchdog**
  on any `reboot()`-triggered `device_shutdown()`, independent of whether
  this program ever opened `/dev/watchdog` itself. A stall between calling
  `reboot()` and PSCI actually taking over could produce a watchdog reset
  that looks identical to "PSCI restart" in the log but isn't.
- **N4 - a `waitpid()` failure's `errno` could be clobbered** before it was
  logged, since `module_present()` (which does its own `open()`/`read()`
  calls) ran in between the failure and the `snprintf()` that reported it.

**Also refined/corrected via round 8's independent re-derivation:**
Opus's "opening `/dev/watchdog` disables the kernel's own auto-ping" theory
(F2) was itself wrong in its mechanism - this driver never sets
`WDOG_HW_RUNNING`, so there never was a kernel-side pre-open feeder to
disable - but userspace petting genuinely is the only thing keeping the SoC
alive once opened, and several blocking operations (ext4 mount, log fsync,
`sync()`) never pet, which is real and unchanged. D7 (fatal-signal handlers)
was confirmed useful only for PID1's own synchronous faults, not for kernel
panics, watchdog bites, or PMIC resets - a real but narrower win than first
framed. L1's claim of a fully "clean PSCI-only" handler chain was corrected:
`exynos-reboot.ko` is pulled in transitively by three of the modprobed
modules and does overwrite `pm_power_off`, though PSCI still wins the
*restart* handler race (verified priority 129 vs exynos-reboot's 128) so the
timing oracle itself is unaffected.

All fixes incorporated into `cinit9.c` (V20):
- D1: dumps `/proc/cmdline` verbatim into the log at t≈0, before any module
  loading - free, persistent, bootloader-authored context.
- D2/D3: a `/cache/v20_boot_count.txt` counter + boot-attempt banner line so
  concatenated crash-loop log runs are attributable; version tag/filename
  corrected from the stale "v18" string to "v20" (confirmed via `strings` on
  the actual V18/V19 binaries that this was a real, not cosmetic, bug).
- D4: `log_line()`'s cache open now has `O_CREAT`; `g_cache_ok` is only set
  after `flush_early_log()` returns a confirmed success, and is cleared again
  if any later cache write fails, so the in-memory fallback resumes instead
  of silently losing lines.
- D5: explicit `umount("/cache")` before `reboot()`, not just `sync()` -
  `sync()` alone does not clear ext4's `needs_recovery` flag, so every prior
  build left the partition needing journal recovery on every crash-loop
  iteration.
- D6: fixed the *second* seq_file single-`read()` site (`wait_for_partition()`
  reading `/proc/partitions`) that round 6 had left unfixed when it fixed the
  same bug in `module_present()`; also switched from raw `strstr()` to a
  line-anchored match.
- D7: `SIGSEGV/SIGBUS/SIGILL/SIGABRT/SIGFPE` handlers that write one line +
  `fsync` before re-raising, using only async-signal-safe calls.
- D8: `platform_bound()` now returns 3 states (no-driver / unbound / bound)
  instead of collapsing the first two into the same `bound=0`.
- F1: writes `/etc/modules/s3c2410_wdt` containing `tmr_atboot=1` before
  loading the module, matching the bootloader's own policy instead of
  silently diverging from it, and logs which policy was chosen.
- F3: logs the real console/kmsg fd numbers and errno instead of an
  unconditional (and, on this device, false) "console wired" claim.
- N1: bounded `modprobe()`'s wait loop to a hard 20s cap per module
  (`MODPROBE_MAX_WAIT_S`) - a stuck child now costs at most 20s of wall time
  instead of blocking forever, with the child deliberately left running
  rather than killed (killing mid-`probe()` risks a worse-defined state).
- N4: `errno` is captured immediately at the `waitpid()` failure site before
  any other syscall can clobber it.
- L1: corrected the header comment's overstated "clean PSCI-only" claim.
- L2: logs explicitly when the 45s deadline has already passed before the
  heartbeat loop, instead of silently skipping straight to the reboot line.
- L4: `g_wd_fd` now gets the same fd 0-2 escape treatment as `g_kmsg_fd`.
- L6: stripped the double-newline inconsistency across log call sites.
- L7: bound-check loop now iterates by `sizeof(checks)/sizeof(checks[0])`
  instead of a hardcoded count.
- L3, L5 noted as latent/low-impact and left as documented risk given the
  measured live headroom (32KB cap vs 23.6KB actual `/proc/modules`; buffer
  size vs actual formatted-message length) rather than adding more code for
  a margin that isn't currently being exceeded.

**Empirical re-verification this session** (not requested by either reviewer,
done anyway before considering this converged): compiled `wait_for_partition()`
and `platform_bound()` out of `cinit9.c` via the same wrapper-`#include`
technique used in rounds 6 and the V19 re-check, ran them on the live phone.
`wait_for_partition("sda33",1)` → 1, `wait_for_partition("nonexistent99",1)` →
0 (no false positive from the line-anchored match), `platform_bound()`
correctly returns 2/1/0 for bound/unbound/no-driver test cases against real
and fabricated driver/device names.

`native_recovery_v20.img`, SHA256
`0c14dffc3cd2581a35b8803d7403d93b13ad0ecf83838daa86967ab8456e84c4`, exactly
100,663,296 bytes. Verified post-build: unpacked image's `/init` is
byte-identical to `cinit9` source; module directory layout unchanged (324
`.ko` files in the versioned dir only, zero flat copies).

**V20 supersedes V19.** Round 8 found real new issues (N1-N4) that round 7
missed despite reviewing the same file, and round 7 itself overturned two
assumptions that had survived six prior rounds - so by the project's own
"loop until no corrections are needed" standard, convergence has still not
formally been reached at a round with zero findings. Whether to run a round 9
(fresh Opus pass on V20/`cinit9.c`) before flashing, or to consider two
increasingly narrow/low-severity rounds in a row (7→8 mix of tier-0/1 vs the
declining severity trend across L1-L7) sufficient to proceed toward flashing
V16 (disambiguator) then V20, is an open question for the user.

Nothing has been flashed to the phone. It remains on the known-good LineageOS
recovery boot, reachable via root ADB, throughout all of rounds 7-8 and the
V20 build.

### Round 9 (Opus, on V20) + the switch to an Astra-only loop (rounds 1-4, V21-V24)

The user asked for one more round on V20, then mid-flight redirected: drop
Opus from the loop entirely and run 4 consecutive Astra-only review/fix
cycles instead. Round 9 (Opus, already launched before the redirect) still
completed and its still-relevant findings were folded into V21/V22 rather
than discarded: watchdog-arm logging (F-6), early-syscall errno logging
(F-7), `g_cache_ok`/`g_kmsg_fd` as `volatile sig_atomic_t` (F-9), and
installing signal handlers after kmsg opens instead of before (F-10). Its
other findings (cmdline truncation, cache-mount/logging-flag conflation,
signal-handler safety) were superseded by Astra's own round-1 findings on
the same file, which arrived independently and covered the same bugs with
its own live proof.

**None of the 4 Astra-only rounds converged. Every single round found real,
previously-missed bugs - including two occasions where a fix from the prior
round turned out to only narrow a race rather than close it.** Builds:

- **V21 (`cinit10.c`)**, from round 1 on V20: fixed a 3-buffer truncation
  chain that defeated the entire point of the V20 cmdline dump (a 32-byte
  `logf_uptime` buffer, then a 160-byte early-log cap, both smaller than the
  live 3329-byte cmdline); `log_line()`/`flush_early_log()` not handling
  short writes; the boot counter's read/write/rename calls being completely
  unchecked and non-atomic (reproduced a real corruption path); `g_cache_ok`
  conflating logging-health with mount-ownership (a log-write failure
  skipped the pre-reboot unmount entirely); the fatal-signal handler calling
  `snprintf` with floating-point formatting (not async-signal-safe) and
  relying on `raise()`, which the kernel doesn't guarantee terminates a
  global-init task; and `wait_for_partition()`'s match only checking the
  trailing boundary, so searching for `"da33"` falsely matched inside
  `"sda33"`.
- **V22 (`cinit11.c`)**, from round 2 on V21: the counter validation still
  permitted destructive resets on any I/O error (not just a missing file)
  and an integer-overflow path; the boot-attempt banner still landed after
  its own attempt's early lines despite being queued first, because
  `flush_early_log()` walks the queue forward from index 0 - fixed by
  writing the banner directly to the file before the flush, not just
  queuing it first; the fatal handler's writes still used a single raw
  `write()` with no short-write handling; a failed `umount()` incorrectly
  cleared mount-ownership tracking; and a real signal-delivery race
  immediately after a successful `umount()` could still write into the
  now-detached ramdisk (proven live by timing a `SIGABRT` exactly there).
  Also folded in the surviving Opus round-9 findings listed above.
- **V23 (`cinit12.c`)**, from round 3 on V22 (first attempt hit a transient
  model-capacity error and was retried cleanly): the counter's read loop
  treated a real I/O error identically to normal EOF, so a read that
  returned some valid digits then failed with `EIO` was silently trusted;
  the 7-digit counter cap validated the stored value but not the
  *incremented* candidate, so `9999999` produced an unrepresentable
  `10000000` that the very next boot would then permanently reject; two new
  diagnostic lines exceeded the 160-byte early-log cap and got silently
  truncated; the watchdog options-file write's return value was discarded,
  so a real write failure still logged a false success; and `modprobe()`'s
  forked child inherited PID1's fatal-signal handlers until `exec()`
  replaced them, so a crash in that pre-exec window produced a fabricated
  "PID1 dying" record from a process that was never PID1.
- **V24 (`cinit13.c`)**, from round 4 on V23 (the last of the 4 planned
  rounds): **two of V23's own fixes were proven incomplete, not wrong** -
  resetting the child's signal dispositions one-by-one narrows but cannot
  atomically close the window before every reset executes, so a fault
  landing in that gap still produced the same false "PID1 dying" record
  (properly fixed this time by having the handler check real process
  identity via `getpid()` against a `g_pid1_pid` recorded at startup -
  correct regardless of any timing, not just narrower); and `g_cache_ok`
  was set *after* `close(fd)` in the banner-write path, leaving a shorter
  version of the same evidence-loss gap round 2's fix closed for the
  longer flush window. Also fixed: a failed options-file write could leave
  a malformed fragment that `modprobe-small` would still forward to the
  kernel's parameter parser, potentially aborting the module load entirely
  rather than falling back to the compiled default as claimed; the
  `/proc/cmdline` read loop had the identical read-error-vs-EOF conflation
  already fixed in the counter code; and the fatal handler's hand-assembled
  version tag was discovered to still literally spell out `'v','2','2'`
  from V20 - built character-by-character rather than as a matchable string
  literal, so none of the `sed`-based version bumps across V21-V23 ever
  touched it, meaning every fatal record from three consecutive builds
  mislabeled its own version.

Every fix across all 4 rounds was empirically re-verified against the live
phone before moving to the next round, using the same technique Astra
pioneered in round 6 of the earlier Opus loop: compiling the actual changed
function out of the real source via a wrapper `#include`, pushing it to the
phone, and running it against real `/proc`, `/sys`, and file-I/O conditions
(including deliberately fabricated files, real disk-space limits, and real
signal timing) rather than trusting static review alone.

`native_recovery_v24.img`, SHA256
`32a4e4821f2bec49229dbb277aa440eff76d519fd0afa2022e637fcbc8434fd0`, exactly
100,663,296 bytes. Verified post-build: unpacked image's `/init` is
byte-identical to `cinit13` source; module directory layout unchanged (324
`.ko` files in the versioned dir only, zero flat copies).

**V24 is the current head of the diagnostic-build lineage, but the review
loop has explicitly NOT converged.** This is not a subjective judgment call -
it is a direct, repeated empirical result: all 4 of the Astra-only rounds the
user asked for found genuine bugs, and the most recent round found that two
of the previous round's own fixes only narrowed races rather than closing
them. There is no evidence in this project's history that a 5th round would
be the first to find nothing. Whether to run further rounds, accept V24 as
"good enough for a diagnostic build whose job is to produce a trustworthy
log rather than be bug-free," or proceed toward flashing V16 (the cheap
disambiguator, unaffected by any of this) followed by V24 the next time the
user is physically at the phone, is an open decision for the user - this
project has deliberately not made that call unilaterally at any point.

Nothing has been flashed to the phone. It remains on the known-good LineageOS
recovery boot, reachable via root ADB, throughout all of rounds 9 and the
4-round Astra-only loop and the V21-V24 builds.

### Decision: skip V16, flash V24 directly

After the 4-round Astra-only loop concluded without convergence, the user
decided to flash V24 directly on the next physical session rather than
flashing V16 first as a separate disambiguation step. Reasoning: V16's
entire purpose was to rule out the packaging/build pipeline itself (mkbootimg
header args, AVB footer, cpio/lz4 packaging) as the source of the crash-loop,
by flashing a byte-identical copy of the real working `/system/bin/init` as
a regular file through that same pipeline. That question has already been
answered without needing a flash: every build from V15 onward has had its
kernel, dtb, and recovery_dtbo verified byte-identical to the known-working
LineageOS build via `cmp`, with only `/init` and the module set differing -
confirmed via static header inspection (`unpack_bootimg.py`) on every single
build in this log, not just V15/V16. Additionally, V24's own timing oracle
subsumes V16's diagnostic value: if V24 manages to log even a handful of
heartbeats before resetting, that alone proves the packaging pipeline is
sound (a genuinely broken pipeline wouldn't let anything execute at all,
regardless of program identity) while simultaneously testing the real
hypothesis this project exists to test. Flashing V16 separately would only
have spent an extra Odin/Download-Mode cycle confirming something already
established by other means.

**Next physical step: flash `native_recovery_v24.img` directly.** No
further review rounds are planned before this attempt - see the "not
converged" note above for the known-open, low-severity signal-timing
residuals that remain unfixed. What to capture immediately after the
attempt: physical reset timing (does it match ~45s?), then reboot back into
LineageOS recovery and pull `/cache/v24_log.txt` and `/cache/v24_boot_count.txt`
via root ADB before doing anything else.

### First real flash of a diagnostic build (V24) - inconclusive, but the biggest new information in the whole project

V24 was flashed to RECOVERY and tested on the real device for the first
time. Visually, it looked identical to every prior build: Samsung logo,
resetting, three times, before the phone fell back into Download Mode. This
looked like the same unbroken crash-loop pattern all 24 builds have shown.

**It wasn't.** The phone's own persistent bootloader log
(`/proc/boot_reset`, the `BORE` history) records the actual boot-mode
selection for every power transition with real timestamps, and reading it
after the fact told a completely different story than what was visually
observed:

| Time | Record | Mode |
|---|---|---|
| 17:43:01 | 505 | DOWNLOAD (V24 flash begins) |
| 17:51:52 | 506 | **NORMAL** |
| 17:52:00 | 507 | **NORMAL** |
| 17:52:12 | 508 | **NORMAL** |
| 17:52:30 | 509 | **RECOVERY**, software-triggered (`REBOOTMODE4`, not a button combo) |
| 17:52:49 | 510 | DOWNLOAD, manual button reset |
| 17:55:45 | 511 | RECOVERY selected by key - the current, working LineageOS boot |

**The three visually-observed "reboots" were NORMAL Android boots, not
RECOVERY-partition boots at all** - V24 was never running during them. The
phone's own boot-loop-protection logic automatically switched to RECOVERY
mode after the third failed normal boot (`REBOOTMODE4` is a
software-requested mode switch, not a key combo), and *that* is the one and
only time V24 actually got to run. It ran for approximately **19 seconds**
(17:52:30 to 17:52:49) before a manual button-press forced a reset into
Download Mode - interrupting it mid-run, not observing it crash on its own.
19 seconds is nowhere near instant, and nowhere near the 45s timing-oracle
deadline either - it's squarely in the range where the early boot sequence
(tmpfs/mknod, console/kmsg setup, cmdline dump, dss+watchdog modprobe)
would plausibly still be running.

**Conclusion: today's flash attempt did not actually test whether V24
crash-loops. It was interrupted before we found out.** The 24-build "every
custom init fails identically and near-instantly" narrative was built on
this same kind of visual-observation-without-a-persistent-log methodology
for every single prior build. This does not retroactively prove any of
those earlier attempts also secretly ran longer than observed - the deep
first-principles review that surfaced this (see below) rates it as the
single most likely explanation for *today's* specific confusion, not a
provenance claim about V10-V23.

### Separately: a real packaging bug found and fixed - V19 through V24 had wrong boot-image headers

The same first-principles review (a dedicated, environment-hardened xhigh
Astra session - the first two attempts at this crashed on unrelated tooling
issues: Codex's own workspace-snapshot step choking on the 23GB `stock/`
firmware directory, then a self-inflicted bug where markdown backticks in
the prompt were interpreted as live shell command substitution by the
invoking script) found a second, independent, real bug: starting at V19,
every build's boot image header has had load addresses double what the
known-working LineageOS reference uses, and was missing the OS
version/patch-level fields entirely.

```
                     reference        V19-V24 (before fix)
kernel load addr     0x10008000       0x20008000
ramdisk load addr    0x11000000       0x21000000
tags load addr       0x10000100       0x20000100
dtb load addr        0x11f00000       0x21f00000
os version           16.0.0           (missing)
os patch level       2026-09          (missing)
```

Root cause: `mkbootimg.py` computes each load address as `base + offset`,
default `base=0x10000000`. Every build script since V19 passed the
already-absolute reference addresses (`0x10008000` etc.) as the *offset*
arguments without also passing `--base 0x00000000`, silently doubling every
address. This went undetected for 6 builds because every verification in
this log checked kernel/dtb/recovery_dtbo *payload* byte-equality, never
the header fields themselves - a real gap in the verification methodology,
not just a build-script typo.

Whether this alone could cause a crash-loop is unclear - the review noted
the live kernel reports its actual ramdisk address as `0x85100000`
regardless of what the header says, suggesting Samsung's bootloader
relocates/reinterprets these fields rather than using them literally. But
it's a confirmed, real, unintended difference from the reference that
should not have been present, and removing it removes a variable before
the next attempt.

**Fixed and rebuilt as `native_recovery_v24b.img`** (same `cinit13.c`
source, byte-identical `/init` confirmed via `cmp` - only the packaging
changed): added `--base 0x00000000 --os_version 16.0.0 --os_patch_level
2026-09` to the `mkbootimg` invocation. Post-build header now matches the
reference exactly in every field. SHA256
`5e58e5e5e8511c54fc197da523189db8a6fe8e92f01d66616562891d79395db2`, exactly
100,663,296 bytes.

### Explicit recommendation from the review: do not build another PID1 logic revision

The review's own conclusion, worth stating plainly: 24 iterations of
PID1-level logic fixes have not moved this forward, and the evidence does
not support treating this as a kernel-rejects-every-replacement-init
problem. The next flash should not be "V25 with more init fixes" - it
should be a repeat of the *exact same* V24b, this time letting it run
uninterrupted for at least 45-60 seconds once it's actually in RECOVERY
mode (confirmed via `/proc/boot_reset` immediately after, the same way
today's confusion was resolved) before touching any buttons, so the
question "does V24 actually crash on its own" finally gets a real answer.

Other hypotheses the review substantially weakened with concrete on-device
evidence rather than reasoning alone: static-ELF/CPU/libc-startup
incompatibility (ruled out - the exact V10 and V24 `/init` binaries were
extracted from their boot images and run under native ptrace-instrumented
execution on the real device, reaching `main()` correctly); CPIO/LZ4
packaging defects (ruled out - direct binary audit of the image-contained
archives found no format defects, and V11's historical archive serves as a
same-toolchain control); pre-policy SELinux/IMA/per-file-signature
enforcement (ruled out via kernel source - SELinux access decisions are
granted before policy load, which happens after first-stage init runs, so
no such check could gate the very first exec); recovery-specific AVB
rollback/hash-tree mismatch (ruled out - both reference and V24 verify
correctly with matching algorithm/rollback-index/hash-descriptor
structure). The strongest remaining open hypothesis, unresolved either way:
missing early hardware/watchdog handoff, based on a separate observation
that several much older historical RECOVERY-mode boot-reset records are
spaced 80-86 seconds apart - matching this device's 80-second watchdog
timeout almost exactly.

Nothing has been flashed since V24b was built. The phone is on the
known-good LineageOS recovery boot, reachable via root ADB.

### 2026-09-19 continuation — read-only boot-selection and Linux feasibility audit

Read this log in full before inspecting the live phone. No device writes,
reboots, or flashes were performed. BORE still ends at record 511; V24b remains
untested. Full findings and a proposed test sequence are in
`docs/NEXT_STEPS_2026-09-19.md`.

Fresh checks establish:

- The live RECOVERY partition is byte-hash identical to the local known-good
  recovery.img. Its complete SHA256 is
  `b5bf01c4a47091eb95078fc69b133b44c2b453b31c23433594c5b605e3747b55`.
  Earlier handoff/document strings omitted the final `5`.
- V24b's hash matches the handoff; fresh unpacking verifies reference-matching
  load addresses, OS fields, kernel, DTB and recovery DTBO. Its packaged /init
  matches the local cinit13 executable. Both images' AVB internal hash checks
  pass. Ramdisk sizes and dependent offsets legitimately differ.
- Ordinary `adb reboot recovery` is not necessarily MISC-write-free. The init
  source pinned by the shipping build manifest writes boot-recovery when the
  BCB command is empty; the live first 32 MISC bytes are zero. The matching BCB
  writer contains no signing operation. This does not prove that an in-place
  runtime BCB update would fail like the historical raw Odin MISC flash.
- The live sec_reboot module and matching kernel source provide an independent
  PMU-based recovery selector for a Linux RESTART2 request. This is a candidate
  for a control test, not a tested zero-button path.
- The current recovery runs from RAM, and RECOVERY is unmounted and not marked
  read-only. Staging a verified image in /tmp and writing only RECOVERY through
  root ADB could avoid Download Mode on entry. It has not been attempted.
- V24b requests an untargeted reboot at roughly kernel uptime 45 seconds and
  starts no adbd. An undisturbed observation interval is not a guarantee of
  either 60 seconds of continuous uptime or automatic return to ADB. BORE mode
  intervals must be paired with actual PID1 logs before claiming execution.
- Live recovery exposes sgpu, DRM nodes, a connected DSI panel, npu/vertex10,
  and approximately 7.1 GiB MemTotal. This does not prove native Linux graphics
  or NPU inference support.

Execution remains pending clarification of the inherited write boundary:
unchanged V24b intentionally writes CACHE, while the literal safety wording
allows writes only to RECOVERY. Ordinary recovery bookkeeping may also write
MISC. The proposed next step is a known-good recovery selector/control test,
followed by unchanged V24b with at least 120 seconds free of manual resets and
physical rescue available if needed. No new PID1 revision was built.

### 2026-09-19 execution — zero-button recovery and hardware Alpine validation

The owner subsequently authorized autonomous implementation and Luna-agent
delegation, with Linux on the phone and minimal owner effort as the outcome.
Image writes remain restricted to RECOVERY; no bootloader/security partition
was changed. V24b remains untested: a headless control image and a native
handoff with automatic rescue are being prepared instead of relying on its
untargeted reboot and absent remote shell.

Before writing, captured live state under
`evidence/native-linux-20260919/`, backed up existing CACHE recovery logs
(read-only/no-journal mount, then unmounted), verified the original RECOVERY
hash, and staged its verified rollback image in phone RAM.

At host time 2026-09-19 22:40:45 +03:00, wrote
`builds/headless_recovery_key.img` from RAM to
`/dev/block/by-name/recovery` using root ADB and `dd ... conv=fsync`.
Full partition read-back SHA256:
`cb8bd4cf1c47027c28278a30ae526667d6ba0fe55bdec6e026e6f2df6de8b654`.
Fresh independent unpacking verified reference headers/kernel/DTB/DTBO and
exactly two CPIO changes: embedded host public ADB key and its restorecon
action. Original recovery remains available unchanged on the host.

At approximately 22:40:54 +03:00, invoked the static ARM64 helper
`/tmp/s22-restart2 recovery` after `sync`. It uses Linux RESTART2 directly,
not Android BCB bookkeeping. Root ADB returned automatically about 30 seconds
later without a button press. BORE advanced from 511 to 512, recording:
`260919 19:41:01 / RP / R / SOFT, INFORM3(12345674) > RECOVERY`.
The bootloader appends a generic "Set by key" phrase, but this experiment
used software only; the explicit PMU value and recorded intervention are the
evidence. Read-back hash still matches. Evidence:
`headless-first-boot/` and `headless-first-boot-usb.txt`.
Live `/adb_keys` remained labeled `rootfs`, not `system_file`; automatic
authorized ADB nevertheless worked. `ro.adb.secure=1`, recovery override
unset. Ordinary `adb reboot recovery` has NOT been tested in this continuation.

Alpine 3.24.2 ARM64 rootfs was unpacked into phone RAM and tested in a chroot.
Native Python 3.14.7, Git 2.54.0, SSH key login and ~7.1 GiB RAM reporting
work. This is NOT yet a native-PID1 boot: `/proc/1/exe` is still Android init.
Public keys only are embedded in host-built artifacts; SSH host keys for this
test were generated on the phone.

A reversible 90-second composite ADB+ECM USB test now works. The first probe
failed with UDC EBUSY and restored ADB. The instrumented retry holds
`sys.usb.config=s22-ecm-test` to prevent Android init's FunctionFS-ready action
from racing manual UDC rebinding. ECM uses phone MAC `02:73:22:00:00:02`, host
MAC `02:73:22:00:00:01`, phone `10.55.0.2/24`, host `10.55.0.1/24`.
Direct SSH over USB Ethernet was verified on actual hardware, independently
of ADB forwarding. Evidence: `alpine-hardware-chroot.txt` and
`alpine-hardware-ecm-ssh.txt`. Neither test is proof of GPU/NPU compute or a
graphical desktop.

### Native handoff V1 — real Alpine boot; timed rescue transition failed

Built `native_handoff_embedded.img`, preserved as
`builds/native_handoff_v1.img`, SHA256
`7e9ef0cc9c160020c79a8466db99bfa9ea0b8de5148b9ce441b77fef7aab557a`.
Original AOSP `/init` is a byte-identical regular binary, loading the complete
shipping kernel-module set. The second-stage `selinux_setup` exec is intercepted
by a static wrapper, which starts a native watchdog-owning PID1 guardian and
Alpine SSH. No Android services are started on the native path. Android recovery
files are retained as rescue payload; this is not a mainline-kernel port.

The Alpine tar.xz (22,123,828 bytes) is embedded, so this boot needs no CACHE
write or rootfs file on any other partition. Helpers, archive paths, original
init copies, reference headers/kernel/DTB/DTBO and AVB hash verification were
checked before boot. Native V1 source snapshot and manifest are under
`evidence/native-linux-20260919/native-v1-*`.

At 22:54:33 +03:00 wrote only RECOVERY through root ADB and verified its complete
read-back hash above. At 22:54:39 issued direct RESTART2 `recovery`. At 22:55:10
the native ECM gadget appeared and root SSH was reachable (kernel uptime 15.15s).
Evidence under `native-first-no-ack/` proves:

- `/proc/1/exe` is `/system/bin/native-guardian`, with no running Android init,
  recovery, adbd or service-manager processes.
- Alpine 3.24.2 ARM64, Python 3.14.7, Git 2.54.0 and HTTPS access work.
- Interactive SSH PTY allocation works; a later check records uninterrupted
  kernel uptime 70.45 seconds with `/run/native-ready` still absent.
- The guardian owns the hardware watchdog with a 30-second timeout.
- Native `/proc/boot_reset` reads failed: the generic device scan lacks
  `/dev/block/by-name/debug`, which the kernel's BORE reader opens. The DT and
  driver source confirm that exact path. Creating the alias from actual sysfs
  `PARTNAME=debug` metadata is the appropriate RAM-only correction; do not guess
  a numeric partition. Consequently no post-native BORE record was obtained.

This first test deliberately withheld the host ACK to exercise the 120-second
fallback. At 22:56:58 the native gadget disconnected, but recovery ADB did NOT
return during the full 210-second observation. The host reports repeated USB
descriptor/address errors (-71; also timeout diagnostics), not a usable USB
device. Do not describe fallback as successful or claim the current phone state
from its screen: it is currently remotely unreachable, and the exact on-phone
failure is unobserved.

After the observation, attempted host-only recovery of the exact phone link:
USB2 port 5-1 disable/enable, paired USB2/USB3 port cycling, and rebind of the
dedicated xHCI controller PCI `0000:c7:00.3` (buses 5 and 6 only, no other attached
devices). All port/controller settings were restored. These did not restore
enumeration. No further phone partition write or reboot command was possible.
Automatic native reconnection monitoring was started; any verified reappearing
native guardian can be ACKed to keep it running.

The rescue source review finds the AOSP argv/SELinux transition contract intact.
Unverified likely fault area: immediate configfs teardown/recreation, ignored
cleanup errors and retained mounts during the hot transition from native USB
back into Android recovery. No phone logs after the disconnect are available
to establish causation. V2 is being prepared to leave a successfully started
native SSH service running after the ACK window rather than trigger this
unnecessary hot transition. This does not fix or prove the startup-failure
rescue path.

Additional pre-native RAM/chroot tests: USB internet sharing works using host
NetworkManager profile `s22-linux-usb` (MAC-specific, 10.55.0.1/24), and signed
Alpine packages install. Native libdrm identity queries report card0 `amdgpu`
3.40.0 and card1 `exynos-drmdpu` 1.0.0. `modetest -M exynos-drmdpu -c -p` enumerates
the connected DSI panel, connector 191/CRTC 184 and formats. Earlier attempts
using `exynos`, `exynos-drm` or a pathname in `-D` used the wrong matching value.
No modeset/pageflip, graphical desktop, GPU compute or NPU inference was tested.
An independent Weston/Pixman package addon is prepared but has not been run.

#### End-of-run recovery preparation (23:08 +03:00)

V2 is built and statically verified, but NOT flashed or boot-tested:
`builds/native_handoff_v2.img`, SHA256
`c30b106133c53a1adccb4ca00f4c7339eee00b21206094084cd0d8b90b5323a4`.
It adds sysfs-derived RAM block aliases, hostname `s22-linux`, and retains
working native Linux after 120 seconds without host ACK. ACK confirmation and
timeout expiry are separate states. After the grace period it restarts a failed
SSH listener instead of switching to Android. The early-startup-failure Android
fallback remains unproven; no claim is made that its USB problem is fixed.
Fresh packaging verification confirms exact final helpers/startup script,
reference boot fields and payloads, and valid AVB internal hashes.

Installed and enabled host user service `s22-native-auto-ack.service` at
23:07:53 +03:00. It runs
`tools/linux-rootfs/auto-ack-native.sh`, checking the native USB VID/PID, exact
ECM interface/MAC and direct-link route before SSH. Only after verifying PID1
`/system/bin/native-guardian` does it touch the RAM `/run/native-ready` marker.
This should keep a reappearing V1 Linux boot from hitting the known-bad timeout
transition; the service is active but its automatic ACK has not yet been
observed on hardware. Existing host SSH credentials are used, never copied into
the phone image. Ephemeral USB-only host-key trust is explicitly documented in
the helper. Stop it with
`systemctl --user disable --now s22-native-auto-ack.service` when no longer needed.

Last confirmed phone image is V1; current physical runtime state is unknown.
ADB, SSH and Samsung Download Mode detection remain unavailable. Both physical
USB port settings and the host controller are enabled/restored. A physical USB
reconnect is the least disruptive next recovery attempt; if insufficient, a
forced restart into RECOVERY is required. Do not flash V2 blindly or claim the
phone currently runs Linux until its live identity/uptime are read again.
No persistent Linux root/data partition, GUI, GPU or NPU acceptance is complete.

### 2026-09-19 continuation — native V2 stable, real Weston KMS, cache preparation

The user physically rebooted. Native V1 reappeared on USB Ethernet at 23:21
+03:00 and the host auto-ACK service confirmed guardian PID1 at uptime 16.53s.
BORE shows record 514 NORMAL at 20:20:22 UTC, then record 515 RECOVERY at
20:20:44 UTC. This does not establish the exact physical button sequence.
The earlier V1 timeout transition had not caused a repeating native crash loop;
the boot remained running until the physical restart, but USB was inaccessible.

At approximately 23:24 +03:00, flashed only RECOVERY from the live native SSH
session to V2, with complete partition read-back SHA256 matching
`c30b106133c53a1adccb4ca00f4c7339eee00b21206094084cd0d8b90b5323a4`.
Direct Linux RESTART2 `recovery` successfully selected RECOVERY again, without
a hand-written BCB or physical buttons. BORE record 516 is RECOVERY, SOFT,
INFORM3 `12345674`, at 20:24:21 UTC. Native SSH returned and the automatic
ACK succeeded at 23:24:46, uptime 16.73s. The V2 guardian hash matches the
built helper. Linux remained alive beyond ten minutes, with no Android
services and with working SSH, PTYs, package installation and USB internet.
Evidence: `native-recovered*`, `native-v2-flash-readback.txt`,
`native-v2-live.txt` under `evidence/native-linux-20260919/`.

Installed the signed Alpine Weston 14.0.2 desktop addon and eudev. Local APK
installation required `--force-non-repository` because the root is RAM-backed;
signature verification was not disabled. An online installation attempt hit
a transient DNS error; subsequent eudev installation over USB internet worked.
Weston uses card1 (`exynos-drmdpu`), Pixman CPU rendering, the external seatd
daemon with `SEATD_VTBOUND=0`, DSI-1 at 1080x2340, scale 2. `/dev/dma_heap/`
contains the required system-uncached allocator. KMS debug state confirms a
Weston-allocated XR24 linear scanout framebuffer and enabled, active CRTC 184.
No GPU acceleration is claimed.

The initial desktop launch lacked input enumeration. After installing/starting
eudev, triggering input devices and restarting Weston, libinput accepted
`sec_touchscreen` event7 as a touch device and Weston associated it with DSI-1.
The proximity sensor was rejected for invalid axis bounds; it is not the touch
panel. Evidence: `weston-first-launch.txt`, `weston-input-kms.txt`,
`weston-touch-start.txt`. Actual touch gestures and photographed panel output
have not yet been verified; KMS state and device acceptance are narrower proof.

CACHE was identified from GPT metadata as `/dev/sda33` (259:17), ext4 UUID
`4407af69-34ae-4723-bbd4-2242385f528e`, size 629145600 bytes. Mounted it first
read-only with `noload`, finding approximately 563 MiB free. Backed up all
existing files to `evidence/native-linux-20260919/cache-before-persistence.tar.gz`,
verified gzip integrity and SHA256
`6b0fff5fc0fd014789e30d76dfc368237a3cdce252495bf40ff3205b991db5e9`.
Persistence is being prepared as ordinary files exclusively under the new
`/cache/s22-linux` directory, without formatting CACHE or altering existing
folders. Raw/image writes remain restricted to RECOVERY. The current desktop
root is still RAM-only until a subsequent persistent image actually boots.

#### V3 persistent image and first framebuffer capture (23:45 +03:00)

`tools/drm-capture.py` successfully read the active card1/CRTC184 scanout buffer
through libdrm and a PROT_READ mapping, without modesetting. Its PNG shows the
actual Weston desktop and terminal. A second capture after maximizing the
terminal shows the native-Linux welcome screen and live shell prompt.
Evidence: `weston-screen-v2.png` and `weston-screen-v2-maximized.png`.
This proves framebuffer contents; it is not an external photograph of the panel.

Seeded the installed root into `/cache/s22-linux/upper` as ordinary files,
excluding mounts and transient archives. It uses 381.6 MiB with about 181 MiB
remaining in CACHE. A live OverlayFS smoke test over the embedded Alpine lower
root successfully ran Python 3.14.7 and Weston 14.0.2; it was unmounted afterward.
Preserved SSH host keys, installed packages and a persistence-test marker.
GUI launcher uses a maximized terminal, external seatd and the compositor's
single automatically launched Weston keyboard; closing the terminal no longer
terminates the compositor. Added a native-only `s22-reboot` wrapper which calls
`sync` before the raw RESTART2 helper.

Built `builds/native_handoff_v3.img`, SHA256
`1a827b43d29141efb47f530902dd4e4ee2b6d780515893c9ecd676ad27efd7d1`.
It retains V2 wrapper/guardian and embeds the separate persistent startup script
(SHA256 `2bf666a0fdea56e23793f0acfb06cd6056008accf1c18b4440918a87b5337b01`).
The script mounts the cache-backed overlay, uses 256 MiB RAM for `/tmp`, writes
RAM storage-mode evidence, and launches the optional desktop independently of
SSH. Cache/overlay failure falls back to RAM Linux. The untested early Android
fallback remains a known limitation.

Independent final-image checks: comparable reference header fields match;
only ramdisk size, dependent recovery-DTBO offset and image ID differ. Kernel,
DTB and recovery DTBO are byte-identical. Final compressed/decompressed ramdisk,
all embedded helpers/startup/archive and unchanged CPIO records match inputs.
AVB internal recovery hash verifies over 92,305,408 bytes, algorithm NONE.

At 23:44:55 +03:00, completed a RECOVERY-only write from V2 and verified the
whole partition SHA256 matches V3. Original Lineage rollback was staged in RAM
with its verified complete hash before the write. CACHE was synced and cleanly
unmounted before the restart. At approximately 23:45:21 issued software
RESTART2 recovery. Boot acceptance and persistent-state proof are pending in
this entry; do not infer them from successful flashing.

#### V3 boot acceptance

Native SSH returned automatically at 23:45:49 +03:00, uptime 14.89s. BORE
record 517 explicitly confirms RECOVERY at 20:45:28 UTC with INFORM3 12345674.
The embedded persistent startup hash matches V3; PID1 is native-guardian;
`/run/native-storage-mode` is `overlay`; `/` is the expected CACHE-backed
OverlayFS and `/tmp` is the separate 256 MiB tmpfs. The seeded marker and exact
SSH host public key survived. Weston, seatd and the terminal started themselves
on this boot. Recorded uninterrupted uptimes 87.43s and 104.39s, with stable SSH
and no intervening reset. Evidence: `native-v3-live.txt`,
`native-v3-acceptance.txt`, `native-v3-stable.txt`.

Installed Python pip/virtualenv, tmux, htop and nano persistently. A native
Python virtualenv smoke test in `/tmp` reports aarch64 and successfully opens
an HTTPS endpoint with certificate verification. This is a working development
environment, not evidence of a configured agent or model inference.

Weston-terminal lacks text-input protocol support, so the compositor's existing
on-screen keyboard cannot type into it. Installed Xwayland, xterm and
matchbox-keyboard as an alternative. Xwayland software rendering works under
Weston/Pixman; its first failed start was caused by the missing volatile
`/tmp/.X11-unix` directory. The launcher now creates it mode 1777. Keyboard
window positioning remains under test: Weston ignores managed X11 clients'
move requests, and changing override_redirect after mapping does not reset the
existing Wayland surface role. Do not claim touch typing acceptance yet.

At this stage root storage has approximately 116 MiB free. No userdata/BOOT
formatting, image write or bootloader modification was performed. The final
normal-power-on boot target has not been changed; use the tested software
recovery selector to reboot into this Linux installation.

#### Working embedded terminal and keyboard

The initial managed-window layout helper was replaced before use: it could not
overcome Weston's positioning policy and its ctypes string ownership needed
correction. The current `start-s22-x11.sh` creates a fresh override-redirect
container, embeds xterm with its supported `-into` option and embeds
matchbox-keyboard using its reported `-xid`. It owns/cleans up only its own GUI
children. The actual framebuffer now shows a dark terminal and full-width
keyboard below it (`native-console.png`). An XTest pointer click on the visible
keyboard's `1` key produced `1` in the terminal (`keyboard-input-test.png`). This
tests the on-screen keyboard-to-terminal path, not a physical finger gesture.
Hardware touch is accepted by libinput but a real touch gesture remains
unobserved. Linux remained up continuously through all desktop restarts.

The installed environment is Alpine 3.24.2 ARM64 + Weston/Pixman, not Arch,
Hyprland or Omarchy. GPU/NPU execution is still unproven. The current Omarchy
ARM initiative targets Apple Silicon; that is not an S22 installer or evidence
of Exynos driver support. The normal Omarchy disk installer must not be run on
this phone under the project's RECOVERY-only image-write boundary.

### 2026-09-20 — Isolated Omarchy trial and measured acceleration blockers

User requested trying Omarchy and expressed concern about agent performance
without GPU/NPU acceleration. Preserved the working V3 installation and took
an ordinary-files Alpine root backup before installing diagnostics:
`evidence/omarchy-trial-20260920/alpine-working-root-backup.tar.gz`, SHA256
`b67bc36440a587691981020fdf56b964c575b878629baf25af7086ca77cb5699`.
It contains device SSH keys; mode 0600, excluded from Git, not for publication.
Original Lineage rollback was also restaged in phone RAM with its full hash
verified. No RECOVERY flash or other raw partition write occurred in this trial.

Signed Alpine Mesa/Vulkan diagnostic packages were installed. Forced stock
RADV enumeration failed (exit 1): Mesa 26.1.6 rejects the Samsung SGPU DRM
interface 3.40.0, requiring 3.54.0 or newer. No Vulkan physical device was
detected. `eglinfo -B` exits 3 and reports llvmpipe for working software paths;
hardware paths fail. Complete combined stdout/stderr evidence is in
`stock-radv-enumeration.txt` and `egl-complete-probe.txt` under the trial evidence
directory. The initial missing-loader probe is not the authoritative result.

Staged official Omarchy v4.0.4 source at commit
`c668141e9c42b13c80c9ca4ea108e11708c5e8a5`; no normal installer executed.
Correction to the preceding entry: official Omarchy ARM work now also covers
Snapdragon laptops (Omarchy Dragon announcement, September 18). This is still
not evidence of an Exynos S22 port. Current configuration uses Hyprland Lua
and Quickshell; Alpine's available version/package combination is unsuitable.

Fetched official Arch Linux ARM AArch64 userspace. The default HTTPS download
host has a certificate-name mismatch; valid TLS mirrors ca.us and de3 work.
Verified its detached signature against the independently documented build
fingerprint `68B3537F39A313B3E574D06777193F152BDBE6A6`. Archive SHA256:
`42a4eeaa038994ffd31fa173256ef2f0ef511358eeb41b9ea1f8626391b9b319`.
Saved verification in `arch-rootfs-signature.txt`. Generic boot/kernel/firmware
payloads are excluded from host userspace staging; the S22 kernel is retained.
Arch ARM provides Hyprland 0.56.2 and Quickshell. Package staging and a bounded
RAM-only phone trial are in progress, not yet accepted as working Omarchy.

Source audit identifies an NPU VS4L driver and NCP v25 compiler-produced graph
format, but no proven native ENN runtime/compiler/model execution. Existing
device nodes alone are not acceleration acceptance. GPU patched-Mesa research
and host-only preparation are separate from the installed working desktop.
Further details and evidence pointers: `docs/OMARCHY_TRIAL.md`.

#### Omarchy trial result on actual hardware

Host staging installed signed Arch ARM Hyprland 0.56.2-3, Aquamarine 0.15.1-1,
Quickshell 0.3.1-1 and Mesa 26.2.3-1. Pacman required its documented
`--disable-sandbox` compatibility flag under QEMU's unsupported Landlock path;
package signatures remained required. Generic kernel and firmware package
records were removed. Root and alarm passwords were locked. The initial
stage helper remains a prototype, not a complete unattended build script.

Assembled `rootfs/arch-omarchy-trial.tar.gz` (554.4 MiB), SHA256
`ecb5ab0c75abb0bbb5e41cb1b9c017229974b87ec061c51110a580435439104d`.
Transferred to a separate 3 GiB tmpfs on the phone, checked that hash again,
and extracted. Arch binaries ran natively in a chroot, not QEMU/Android. This
was not a separately booted or persistent Arch installation. Exact versions
are in `arch-phone-versions.txt`.

Actual Omarchy Lua configuration passed `Hyprland --verify-config` on the phone
(exit 0, `config ok`), with default provisioning/autostart/keybindings disabled.
Nested startup against Weston/Pixman exited 134: missing required Wayland
protocols and no allocator (`hyprland-nested-logged.txt`).

A second, bounded direct-display test temporarily stopped only the desktop,
gave the trial DRM nodes/read-only udev metadata and a separate seatd, selected
DPU card1, and forced software rendering. Hyprland started and reported
OpenGL ES 3.2 / llvmpipe LLVM 22.1.8. It detected DSI-1 but repeatedly failed
EGL device matching and display commits (`Can't create renderer, no matching
devices found`; `Failed to update renderer state for DSI-1 on applyCommit`).
The planned 20-second timeout returned 124; this is not a success or a crash.
Evidence: `hyprland-direct-on-phone.txt` and `direct-seatd.txt`.

Cleanup automatically restored Alpine Weston, xterm and matchbox-keyboard.
The framebuffer captured after restoration is `after-omarchy-restored.png`;
it is NOT an Omarchy screenshot. The temporary Arch RAM root was unmounted
after evidence collection, recovering its RAM; the host archive remains for
future trials. No phone reboot or physical button press occurred. Final BORE
remains 517, RECOVERY; uptime 2854.90 seconds; original V3 RECOVERY partition
SHA256 still `1a827b43d29141efb47f530902dd4e4ee2b6d780515893c9ecd676ad27efd7d1`.
Final evidence: `final-phone-state.txt`. The Alpine login banner is updated
for subsequent terminals to say GPU blocked / NPU unproven.

A separate host-only patched RADV build did not produce a driver artifact
because of Clang/musl header-search issues. No patched GPU driver was run on
the phone. Omarchy is therefore **not yet usable**, and GPU/NPU acceleration
remains unproven; the next meaningful work is the Exynos graphics driver and
buffer-presentation path, not reinstalling another desktop distribution.

#### Standalone Hyprland control requested by user

Reviewed the supplied Exynos/model-performance text and tested Hyprland with
a minimal independent Lua configuration: no Omarchy imports/autostarts,
animations, blur, shadows or Xwayland. Validation passed. The first harness
attempt stopped before Hyprland because the existing launcher's TERM trap
was deferred behind its foreground terminal controller. Corrected the harness
to terminate the verified owned Weston too and avoid duplicate restoration.
Stopped the known redundant launcher/controller from the failed attempt.

The real retry ran for its bounded 20 seconds, reported llvmpipe LLVM 22.1.8,
and reproduced the same EGL device-match and DSI-1 presentation errors as the
Omarchy-configured run. Timeout exit 124 was expected. Evidence under
`evidence/omarchy-trial-20260920/`: `hyprland-minimal-config.txt`,
`hyprland-minimal-direct-retry.txt`. Source: `hyprland-minimal.lua`.
Alpine's desktop/keyboard were restored; only one controller remains. The
temporary RAM root was unmounted. `after-hyprland-minimal.txt` records BORE517,
uptime3434.29 seconds and native Weston; no reboot or partition flash occurred.

CPU flags include ARM dot-product (`asimddp`) and `i8mm`. Qwen token-speed
estimates are not measured acceptance. Headless Vulkan compute is independent
of desktop presentation, so future GPU inference need not wait for Hyprland.
No CPU/GPU/NPU model inference was benchmarked in this control test.

### 2026-09-20 — Native model benchmarks, touchscreen path, and driver trials

User authorized Luna workers, model downloads/benchmarks, Omarchy trials and
driver work while preserving a usable native phone. Main alone operates the
phone. No partition was flashed or formatted in this round. BORE remains 517;
V3 RECOVERY readback still matches
`1a827b43d29141efb47f530902dd4e4ee2b6d780515893c9ecd676ad27efd7d1`.
Work below is incremental; later entries supersede ongoing trial status.

#### Touch and CPU inference: measured on the actual S22

`sec_touchscreen` exposes 10 MT slots and X/Y ranges 0–4095. A synthetic
evdev tap through that exact device produced `1` via the visible matchbox
keyboard into xterm; a second tap on backspace removed it. This validates
the downstream input path, not physical finger sensing. Source helper:
`tools/linux-rootfs/touch-check.py`; screenshots and metadata under
`evidence/driver-model-20260920/`. Weston launcher now avoids starting another
udevd on every desktop restart (existing older duplicates not removed).

Built llama.cpp `e613ef2c81bae98d59850d061ac29e6e3e88cb00` for ARM64.
Baseline is generic CPU; optimized build adds
`GGML_CPU_ARM_ARCH=armv8.6-a+dotprod+i8mm`, with native/OpenMP disabled.
Both use an isolated glibc loader/library closure on Alpine; system musl is
unchanged. Runtime/model hashes and provenance are under
`evidence/model-bench-20260920/`.

Verified weights:

- ggml-org Qwen3.5-0.8B Q4_0: 563036064 bytes, SHA256
  `57d1997790d1744fba5b40a7317df71ea5e2acee28c47e78f0cce39c0703f8cf`.
- Unsloth Qwen3.5-2B Q4_0: 1214873856 bytes, SHA256
  `cd70221bebaee0503e0f6717e174250cd7825aa88438b3aabec9ad55731d9bb1`.

Phone-native benchmark: pp256/tg64, 2 repetitions, mmap, no GPU layers,
4 threads pinned to cores 4–7, unless stated otherwise. These short,
empty-depth tests are NOT sustained or 4K-depth throughput measurements.

| Model/runtime | Prompt tok/s | Decode tok/s |
| --- | ---: | ---: |
| 0.8B baseline, 4 big cores | 28.06 | 15.65 |
| 0.8B baseline, all 8 cores | 15.37 | 13.73 |
| 0.8B optimized, 4 big cores | 126.12 | 20.21 |
| 2B baseline, 4 big cores | 11.18 | 6.94 |
| 2B optimized, 4 big cores | 87.63 | 10.13 |

Battery sensor ranged about 29.9–36.2 C in these tests; no overclock or
governor override. A separate optimized 2B generation used 4096 allocated
context and produced a correct shell explanation. The pinned completion
frontend's `--reasoning off` did not suppress thinking in the first attempt;
an explicit Qwen non-thinking ChatML prefix did. `s22-chat.py` is a local
chat client, NOT an autonomous agent and never executes generated commands.
Its piped question/quit smoke test passed. Runtime/weights remain RAM-only;
host copies are preserved. `stage-phone.sh` restores the verified 2B path.

#### GPU: enumeration works; compute correctness FAILED

Successfully built pinned experimental Mesa/RADV 24.3.4 at
`d1b295e8c61c013c454e8015983a1d6b6df82bf2`, ACO enabled, LLVM/WSI/Gallium
disabled. Original ICD SHA256:
`17d159f9f9f5228bef6919cbefa577555c158c923e67384d7c0e7b847ef233b3`.
It is staged separately at `/opt/radv-xclipse` in tmpfs; stock Mesa is intact.
Native `vulkaninfo --summary` exits 0 and enumerates AMD/RADV VANGOGH,
vendor 1002/device 73a0, Vulkan 1.3.296. This is not compute acceptance.

A bounded compute probe initializes 256 uints to A5, dispatches `i*3+7`,
inserts a shader-to-host memory barrier and waits on a fence before readback.
It FAILED: `VK_ERROR_UNKNOWN (-13)`. `strace` shows CS/WAIT_CS/SYNCOBJ_SIGNAL
succeed, then SYNCOBJ_WAIT returns EINVAL. The kernel reports a GPU TCP
write fault at `0x0000ff78f6c54000` (mapping/permission fault). Crucially,
the upstream experimental patch forces `expired=1` and manually signals
syncobjs; its completion log is synthetic, not proof of GPU completion.
Do not bypass the fence or claim usable GPU inference. No model was offloaded.

A diagnostic build with descriptor logging was run in PREPARE_ONLY mode:
no commands submitted. Storage VA `ffff800100010000`, descriptor words
`00010000,00008001,00000400,31016fac`. A separate musl Vulkan llama runtime
is built but has not been run on the GPU. Worker initially inverted libdrm's
`expired` semantics in a proposed patch; corrected before any application.
Only `expired != 0` means completed. Relevant trace/analysis files are under
`evidence/driver-model-20260920/` and `tools/omarchy-trial/radv-sync-*`.

#### Display and NPU investigation still in progress

Arch trial restaged in RAM, using the previously verified archive. One
staging mistake copied before mounting tmpfs and filled CACHE with a partial
116459520-byte archive. Only that partial file was removed, returning free
space to 94.9 MiB; subsequent transfers were mountpoint-gated. No image flash
or other filesystem content was changed by that failed transfer. Installing
strace/gdb later intentionally consumed about 20 MiB of persistent space.

Opt-in Aquamarine patch skips an EGL renderer for sole `exynos-drmdpu`,
analogous to its display-only EVDI path. First replacement passed config/
loader checks but crashed in actual startup. Native gdb located the fault in
`std::__format::_Sink<char>::_M_write`, called by Aquamarine onReady. A
consistent-GCC16 rebuild is pending; do not infer the display patch itself
is disproven yet. Every test restored Weston automatically. The image named
`hyprland-display-patch.png` was captured AFTER restoration and is NOT proof
of Hyprland rendering. Debugger harness errors (SIGSTOP in timeout, then
pending SIGSTOP not ignored) were corrected before the useful backtrace.

Read super metadata O_RDONLY, validated its geometry/header/table SHA256s,
and streamed only vendor extents to host `rootfs/live-vendor-readonly.img`:
1640783872 bytes, SHA256
`6dfe677119792e95a37b016092c327ad62bc0dc7759851424ad05e6bcb31b206`.
No stock archive was opened and no vendor block was written. Vendor is F2FS.
Initial dump tools silently produced zero-filled data for compressed assets;
these are invalid extractions, not missing firmware. Cluster-aware LZ4
recovery is ongoing. No NPU open/boot/graph ioctl or inference was attempted.

#### Follow-up: real Omarchy UI, GPU lifecycle fix, clean software reboot

The Aquamarine display-only patch WORKED after rebuilding with GCC16
libstdc++ throughout (one large unit used ARM Clang22 with those same headers
because ARM GCC16 crashed under QEMU). Library SHA256:
`6fb3f4377ac3d14e4d74a18bdc5316e0acc23779e6894a1dab449cdb294d42ca`.
Hyprland 0.56.2 then rendered directly on DSI-1 via llvmpipe. Adding DejaVu
fonts fixed missing glyphs and allowed foot to launch. Both the minimal
configuration and the pinned real Omarchy defaults had empty `configerrors`.
Two bounded 60-second sessions exited via timeout and restored Weston.
`hyprland-gcc16-fonts.png` and `omarchy-gcc16-display.png` show actual frames,
corroborated by live Hyprland/foot PIDs and mapped-window properties.

The real Omarchy Quickshell bar, foot, and Squeekboard subsequently ran
together. Squeekboard needs an explicit `sm.puri.OSK0.SetVisible(true)` call
for this terminal setup; its live D-Bus interface was introspected before
use. The keyboard includes Ctrl, Alt, arrows and a terminal layout. Synthetic
taps through `/dev/input/event7` typed `hi` into the actual on-screen local
chat, and later submitted it. This is downstream touch-path evidence, NOT a
physical finger test. Screenshots: `omarchy-keyboard-hi.png` and
`post-reboot-chat-complete.png` under `evidence/driver-model-20260920/`.

UI dependencies were verified from signed Arch ARM packages on the host.
The phone's local pacman signature-helper startup hung BEFORE a package
transaction. Its child PID 13499 remained runnable with SIGKILL pending;
the cause is not established (its actual open-file limit was only 1024, so
do not claim the hypothesized huge-FD scan was proven). No signatures were
disabled. Used a SHA-verified, host-extracted `usr/` runtime payload instead,
without package scripts/hooks or services; a small supplement supplied the
missing GNOME desktop library and generated enum schema. The first payload
alone was not sufficient. Some desktop-portal/accessibility/audio services
are absent; a visible Omarchy bar is NOT acceptance of those services.

GPU command recording exposed a second, concrete bug: the experimental RADV
fork used `amdgpu_bo_cpu_map()` but raw `munmap()` on release. Strace showed
double unmaps; gdb located a later pipeline-cache crash in memory reused from
a former shader BO mapping. Pairing with `amdgpu_bo_cpu_unmap()` fixed the
record/teardown test (exit 0). The observed descriptor-pointer high bits matched
exactly, so the suggested address32 mismatch was NOT supported.

Combined lifecycle + real-fence artifact SHA256:
`741d88f6747816c07838a7b9f84aa55398c66bcb19dbd45ad4df937451928698`.
The forced `expired=1` was removed. A single bounded real compute retest
still FAILED: query returned `ret=0, expired=0`, Vulkan wait returned -13,
and the kernel reported a command-fetch permission fault at
`0x000080010003e000`, then timeout and successful GPU reset. No model offload
was attempted. Parent regenerated the combined patch from actual git diff
and validated `git apply --check` against the pinned base; the worker's
earlier handwritten patch was invalid and was replaced.

Recovered 67 vendor asset files (including duplicates/variants), with 49
valid ELFs, by respecting F2FS logical zero slots and 4-page LZ4 clusters.
Recovered ENN libraries are Android/Bionic-oriented. `AIE.bin` and
`dsp_reloc_rules.bin` are available; the public matching-family source has
a `vectors.bin` request, but absence does not prove every execution mode
requires that file. `NPU.bin` is a conditional/default name, not a proven
missing requirement of this configuration. No NPU boot/inference was tried.
See `evidence/npu-audit-20260920/runtime-feasibility.md` and source audit.

Longer CPU test: optimized 2B, depth4096 + generation256, 3 repetitions,
four fast cores, averaged **5.467 tok/s** (samples 5.646, 5.386, 5.369).
Battery temperature rose from 32.2 C to 39.7 C. This is distinct from the
empty-depth short test's 10.13 tok/s. No overclock or governor override.

At 23:02 UTC the parent used the already-tested `s22-reboot recovery` to
clear the hung pacman child and accumulated test state. **No buttons and no
flashing.** BORE advanced from 517 to **518**, recording
`260919 23:02:18`, `INFORM3(12345674) > RECOVERY`. Native guardian, persisted
SSH host key, Alpine overlay and Weston returned automatically. The hung
task was gone, and only one udevd remained. Known-good rollback image was
hash-verified before reboot; RECOVERY itself was not rewritten.

Host helpers then restored the 2B model and Arch UI to fresh RAM. Parent
review fixed staging-path, chroot, mount-check, and fail-fast errors before
accepting the UI helper; its actual post-reboot run reached staged-ready.
DNS was set from the native USB resolver, and Arch resolved github.com.
At 23:06 UTC an unbounded, explicitly selected RAM desktop session started
under the same restoration harness. Local chat answered `hi` on screen.
The first CLI-based request took 31 seconds including startup, versus about
5.1 seconds in the model's own log. A resident loopback model server is the
next optimization; it is not yet an accepted result in this paragraph.

The native boot remains Alpine/guardian with an Arch userspace chroot for
the desktop. The full Omarchy installer was never run. Arch UI, model weights
and model runtimes remain RAM-staged; the approximately 583 MiB CACHE overlay
cannot hold them alongside the preserved fallback. Cold-power boot routing,
Wi-Fi, cellular, audio, suspend and cameras are not accepted features.

#### Follow-up: resident model server and final Omarchy handoff

The API-only ARM64 llama-server build completed from llama.cpp
`e613ef2c81bae98d59850d061ac29e6e3e88cb00`, using the optimized ARMv8.6
dotprod/i8mm CPU backend and a private glibc closure. Server executable SHA:
`3532573016a7d26a45179b9a108a11aaa3073f766cc6c94494e0b6f6098219a6`.
Deployment archive SHA:
`21426e1eb355c66ee01ee36161072dff4702f5e4fe363f38188876ff0c29fa6b`.
Transferred to `/mnt/model-bench/server` only after verifying tmpfs, native
PID1, the archive hash and an unused loopback port. The server loaded the
existing 2B model, 4096 context, one slot, four fast cores, CPU only, nice 0.
`/health` returned ok and netstat showed ONLY 127.0.0.1:8089 for its listener.
It is not a persistent boot service and has no browser UI.

The new `tools/linux-rootfs/s22-chat-http.py` keeps model loading out of each
request, bypasses ambient HTTP proxies, streams `/completion`, caps history
and output, and executes no commands. Two native tests measured first text
0.6 / 0.4 seconds and complete responses 1.1 / 3.7 seconds. These sample
latencies supersede the slow per-request CLI for interactive use, not the
separate sustained decode benchmarks. Evidence: `resident-server-chat.txt`.

Stopped the verified old Hyprland session, confirmed Weston restoration,
then restarted the Arch/Omarchy session with the HTTP client. The delayed
Squeekboard visibility request worked without manual D-Bus intervention.
Timezone is Europe/Athens. Synthetic `hi` plus Enter through event7 produced
a real visible answer in 1.3 seconds, first text 0.7 seconds. Touch contacts
were released and `physical_touch_verified` remains false. Frame evidence:
`omarchy-resident-ready.png` and `omarchy-resident-chat.png`. Empty Enter
events left repeated prompts in that capture; the session remains usable.
The compositor reported no configuration errors. Disabled continuous
debug/stdout logging for the indefinite session and verified its log stopped
growing (202577 bytes at two successive checks).

The desktop is left running, with `/quit` opening the terminal shell.
GPU compute and NPU inference are NOT accepted. No further GPU submissions
occurred after the clean reboot. RAM restoration commands and limitations
are documented in `docs/OMARCHY_TRIAL.md`. The resident-server staging helper
guards against a still-running previous server; an immediate restart attempt
correctly refused while termination was still pending, then was retried.
No additional image flash, partition format or bootloader change was made.

Final server-helper rerun reached `/health` ready with `--no-webui`, PID
9484. A completion invoked from the actual Arch client returned `Hello!` in
0.6 seconds (first text 0.5 seconds). Final read-only partition resolution
used sysfs PARTNAME=recovery and validated major/minor before opening
`/dev/sda16`; the whole-partition SHA256 still matched preserved V3 exactly.
The Android-style `/dev/block/by-name/RECOVERY` alias does not exist in this
Alpine boot, so the first final-state hash lookup failed without reading or
writing a partition. The resolved readback is separately recorded in
`final-recovery-readback.txt`. Final battery temperature was 32.1 C and
MemAvailable about 1.65 GiB with desktop and resident 2B server running.
The client terminal has ECHO, ICANON, ICRNL, ONLCR and OPOST enabled; the
repeated empty prompts in the screenshot are not an established raw-TTY bug.

### 2026-09-20 — Persistence assessment and public repository update

User asked how to make the installation persistent/permanent and to update
GitHub with the work. Read-only live inspection reconfirmed native guardian,
the running RAM desktop/model mounts, and CACHE overlay usage of 495.9 MiB
out of 582.6 MiB, leaving 74.6 MiB. Sysfs identifies userdata as 221257728
512-byte sectors (about 105.5 GiB). No decrypted mapper volume was present;
read-only blkid did not identify a filesystem on userdata. The matching
device fstab configures F2FS, file and metadata encryption, and wrapped keys.
This is not empty/free storage and was not mounted or modified.

`docs/PERSISTENCE.md` separates nonvolatile storage, automatic desktop/model
startup and cold-power-on boot selection. It proposes either an external
storage trial or an explicitly authorized, backed-up userdata conversion.
The latter is destructive and requires changing the current RECOVERY-only
write boundary. BOOT changes need a separate gate; BOOT is 64 MiB and RECOVERY
96 MiB, so a recovery image is not a drop-in BOOT image. No formatting,
partition-table change, device file deployment, reboot or image flash was
performed during this publication/persistence assessment.

Updated README and STATUS from the obsolete pre-native-boot state, added
publication exclusions, and retained source/configuration, small patches,
logs, screenshots and provenance. Private backups/keys, firmware/partition
images, model weights, built runtimes, package archives, source clones and
raw recovery-cache forensics remain local and are not deleted. Public keys
and known-host records are not private credentials. See `docs/PUBLICATION.md`.

Exported the exact current Aquamarine patch from its pinned source diff;
reverse apply-check passed against the patched checkout. Publication review
found the older standalone real-fence patch had a corrupt hand-written hunk;
regenerated it from the tested combined patch. Both RADV patches reverse
apply-check against the experimental source. Corrected stale candidate/build
notes, and verified the OSK supplement's logical size is 348160 bytes (340 KiB).
Focused validation: 32 shell syntax checks, 10 Python AST parses, six C
translation-unit syntax checks, patch checks, source/documentation whitespace
checks and a filename-only credential/ELF scan. Raw diagnostic output, public
key material and required diff-context whitespace were preserved unchanged;
they are excluded from the whitespace gate. These do not certify all historical scripts
as safe to run or establish new hardware functionality.
