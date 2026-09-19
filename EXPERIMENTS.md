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
