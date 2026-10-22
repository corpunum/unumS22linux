# Display, input, sensors and power

Part of the [porting guide](../PORTING.md).

## Display

The panel is driven by the vendor kernel's display driver (`exynos-drmdpu`). There is no working
open GPU driver for desktop rendering, so everything on screen is drawn by the CPU (or, in one
experiment, by Samsung's Vulkan driver, see [GPU and NPU](gpu-npu.md)). What we learned:

- A desktop compositor that insists on a GPU renderer needs to be told not to: Hyprland's backend
  library got a small display-only patch for this
  ([PUBLICATION.md](../PUBLICATION.md#pinned-third-party-sources),
  `tools/omarchy-trial/aquamarine-s22-display-only.patch`). Today the desktop is sway with its CPU
  (pixman) renderer ([STATUS.md](../../STATUS.md#fast-touch-shell-is-the-default--2026-10-21)).
- **Screenshots can lie.** The captured framebuffer was clean while the physical panel showed lines.
  The kernel source showed the display engine is told the visible width (1080 px) while the buffer
  rows are 1088 px wide; a narrow workaround fixed it, confirmed by the owner looking at the panel
  ([tools/omarchy-trial/s22-linear-stride.md](../../tools/omarchy-trial/s22-linear-stride.md)). Always
  have someone look at the real screen.
- The panel is a command-mode panel: half-drawn frames reached it until rendering was made
  synchronous ([STATUS.md](../../STATUS.md#current-checkpoint--2026-10-06)).
- A KMS-direct shell is much faster than a compositor with a CPU renderer: tap to page in ~9–12 ms
  instead of 63 ms ([evidence/fastshell-20261021](../../evidence/fastshell-20261021/README.md)).

Our Rust touch shell can be built for other Linux phones; see
[tools/unum-fastshell/PORTING.md](../../tools/unum-fastshell/PORTING.md).

## Touch and buttons

The touchscreen and keys are ordinary evdev devices once the vendor modules load
(`sec_touchscreen`, power/volume keys) ([INPUT_POWER_NATIVE.md](../INPUT_POWER_NATIVE.md)). The
touchscreen firmware is among the files already in the ramdisk's `/vendor/firmware`
([SENSORHUB_WORKING_2026-09-21.md](../research/SENSORHUB_WORKING_2026-09-21.md)). `s22-buttons` reads the key devices and
runs hooks (volume, push-to-talk, Power lock) ([STATUS.md](../../STATUS.md#current-checkpoint--2026-10-06)).
Physical presses must be identified by device and event code before you call them working;
synthetic input is not enough ([INPUT_POWER_NATIVE.md](../INPUT_POWER_NATIVE.md)).

## Sensors

The accelerometer, gyro, magnetometer and light sensor sit behind Samsung's sensor hub MCU. It needed
its firmware (`sensorhub/shub_pamir_rainbow.bin`) recovered from the vendor image, and the right
enable interface: the running driver is the older `drivers/staging/nanohub` with a **bitmask**, not
the newer driver in the same source tree
([SENSORHUB_WORKING_2026-09-21.md](../research/SENSORHUB_WORKING_2026-09-21.md)). Lesson: check which
driver is actually bound before reading its source. No calibration or auto-rotate yet.

## Battery and suspend

Battery and thermal data are normal sysfs (`/sys/class/power_supply/battery`, thermal zones)
([INPUT_POWER_NATIVE.md](../INPUT_POWER_NATIVE.md)).

Deep suspend works on the stock Samsung kernel with **no Android suspend daemon**. Our
`s22-autosleep` service, when the screen is off and nothing holds the phone awake, sets the
`powersave` governor and enters suspend-to-RAM in cycles, waking on Wi-Fi packets (Wake-on-WLAN) and
the RTC. Locked drain went from 192 mA (5.8 %/h) to 36 mA (1.1 %/h), and the phone still answered
SSH over Tailscale ([evidence/power-20261022](../../evidence/power-20261022/README.md),
[STATUS.md](../../STATUS.md#suspend-to-ram-and-autosleep--2026-10-22-early-morning)).

It stays awake while an SSH session is open, audio is playing, a call is active, the CPU is busy,
an inhibit file exists, the USB host is attached (configurable) or a kernel wakeup source is active.
`s22-autosleep off` is the kill switch.

How we measured drain honestly: Samsung's "slate mode" sysfs switch stops charging from USB while
keeping USB data, so the battery feeds the phone and `current_now` is the real drain; while asleep,
the fuel gauge's fine-grained raw state of charge was used instead
([evidence/power-20261022](../../evidence/power-20261022/README.md)). Restore slate mode afterwards.

**For your phone:** check `/sys/power/mem_sleep` for `deep`, list wakeup-capable devices, and test
that the network you rely on for access can wake the phone before you let it sleep on its own.
