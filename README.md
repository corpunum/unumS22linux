# unumS22linux

Turning a Samsung Galaxy S22 (SM-S901B/DS, Exynos 2200, codename `r0s`,
bootloader genuinely unlocked) into a native Linux handheld with **no Android
userspace** — eventually Arch Linux ARM + Wayland + Hyprland + Omarchy,
running directly on the hardware instead of inside Android.

## Vision

Most "Linux on phone" projects run inside a chroot or container on top of a
still-booted Android kernel/userspace. This project's goal is different and
harder: replace Android's `/init` and userspace entirely, so the phone boots
straight into a normal Linux distribution the same way a laptop does — full
control of the boot chain, no Android services running underneath, no
compatibility shims. The Exynos 2200 recovery partition is being used as the
target because it's a separate, independently-flashable boot slot (not
A/B-paired with the main boot chain), making it a safe place to iterate
without touching the phone's primary boot path.

## Safety model

- **Human-in-the-loop for anything that writes to the device.** Flashing and
  crash-loop recovery both require someone physically at the phone; this is
  a deliberate constraint, not a missing feature. Nothing in this repo's
  tooling attempts to bypass that.
- **Hard boundaries, never touched:** PIT table, EFS, IMEI, bootloader (BL),
  secondary bootloader (SBL), TrustZone. Only the RECOVERY partition is ever
  written by this project's own builds.
- Every build in this repo has a "FLASH GATE" — an explicit go/no-go point
  before anything reaches the device — and a known-good rollback path (a
  verified LineageOS recovery image) is always one flash away.

## Where things stand

The bootloader is unlocked and a **known-good reference boot** is
established: LineageOS's own (unmodified) recovery image boots successfully
on this device with full USB/display/touch, and remains the current state of
the phone. Nothing custom has been flashed.

The open mystery this project is chasing: **any** substitute for AOSP's
`/init` — a busybox script, a minimal script, or a from-scratch static C
binary — crash-loops identically on the same kernel/dtb/ramdisk-base that
boots fine under the real, unmodified `/init`. This has been true regardless
of what the substitute program actually does; even a version that did
nothing but create a few directories failed the same way. That question can
only be answered by an actual flash-and-observe cycle on real hardware.

Since that cycle requires someone physically at the phone, the effort so far
has gone into making sure that when it happens, the resulting evidence (a
persisted boot log surviving the crash-loop) is actually trustworthy — not
just building the fix would-be, but proving each diagnostic mechanism (device
node creation, module loading, log persistence, the watchdog, the reset
timing) works correctly *before* spending a real flash attempt on it.

That work has gone through 24 build iterations (`cinit.c` → `cinit13.c` /
V10 → V24) and a long adversarial multi-model review process: Claude,
Opus 5, and Codex/GPT ("Astra") reviewing each other's work, each with live
root ADB access to the actual phone to empirically prove or disprove claims
rather than reason about them abstractly. The 4 most recent rounds were run
Astra-only, back to back, each reviewing the previous round's fixes — and
**every single round found new, real, live-proven bugs**, including two
cases where a previous round's own fix turned out to only narrow a race
condition rather than close it. The full round-by-round history, every bug
found, and the reasoning behind every fix is in [`EXPERIMENTS.md`](EXPERIMENTS.md) —
that file is the canonical project log; this README is just the front door.

## Repo layout

- `initramfs/cinit*.c` — the sequence of custom `/init` implementations
  (V10 → V24), each a strict improvement on the last, documented in detail
  in `EXPERIMENTS.md`.
- `EXPERIMENTS.md` — the master experiment log. Every build, every bug
  found, every review round, in order.
- `evidence/` — captured output from live device sessions (dmesg, module
  lists, partition dumps, hardware deep-dives) and the adversarial-review
  test harnesses/results.
- `docs/` — feasibility analysis (kexec, recovery-image comparison).
- `DEVICE.md`, `STATUS.md`, `FLASH_LOG.md`, `MODULES.md` — supporting
  reference docs.

Not included in this public repo: extracted Samsung stock firmware
binaries, LineageOS build artifacts, and compiled ramdisk staging trees —
these carry copyright/redistribution concerns and aren't this project's own
code. See `.gitignore`.

## Nothing has been flashed

At every point in this project's history, the phone has remained on the
known-good LineageOS recovery boot, reachable via root ADB. All build,
review, and verification work has been done through static analysis and
live (but read-only-to-the-device) ADB testing.
