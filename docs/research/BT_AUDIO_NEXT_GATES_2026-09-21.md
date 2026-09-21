# Bluetooth and audio next gates — 2026-09-21

Read-only source audit for the next parent-reviewed step. No phone access,
ioctl, bind/unbind, reprobe, firmware operation, or network action was done.

## Bluetooth: `BT_CMD_PWR_CTRL(0xbfad)` and the WLAN prerequisite

The pinned kernel ABI is explicit: `BT_CMD_PWR_CTRL` is `0xbfad`
(`include/linux/btpower.h:89-93`). In `drivers/bluetooth/btpower.c`, the ioctl
casts its argument to a power mode and, when it differs from the global
`pwr_state`, calls `bluetooth_power()` (`1313-1347`). This is therefore a
state-changing operation, not a version-probe-only ioctl.

For `1`, `bluetooth_power()` enables the BT regulator, optional BT clock, and
BT GPIO setup (`547-582`). For `0`, it drives BT reset low, waits 100 ms,
frees BT GPIO/clock resources, and disables the regulator (`589-613`). The
Samsung path obtains the regulator specifically as `vreg_wlan`
(`795-831`), and enables/disables it with ordinary regulator APIs
(`836-899`). The r0s DT independently gives CNSS `wlan-en-gpio` and
`qcom,bt-en-gpio` and names `vreg_wlan` as the WLAN regulator (for example
`arch/arm64/boot/dts/samsung/r0s/r0s_eur_openx_w01_r21.dts:5825-5841`).

The regulator calls are ordinary balanced `regulator_enable()`/
`regulator_disable()` calls, so the source does not by itself prove that a BT
disable collapses a CNSS-held rail. The parent live read of regulator_summary
shows `vreg_wlan` with `use1 open3`, one CNSS `vreg_wlan` handle with count 1,
and the BT handle with count 0; this is evidence that CNSS currently holds an
independent vote. It is not proof that a transient BT operation cannot disturb
WLAN, nor that all relevant CNSS state is represented by that summary.

The compiled DT parsing is narrower than the alternate source branch:
`#if 0` disables parsing of `qcom,bt-reset-gpio` and `qcom,wl-reset-gpio`
(`btpower.c:929-942`). The active `#else` takes `of_get_gpio(..., 0)` as BT
enable/reset and leaves `wl_gpio_sys_rst` at its kzalloc default 0
(`1177-1198`, `942-977`). `bt_configure_gpios()` therefore writes only the BT
GPIO, but also reads GPIO 0 in its WLAN timing test (`418-495`); it does not
write the WLAN enable pin. The r0s DT's first BT `gpios` entry is BT enable,
followed by BT wake and host wake (`...r0s_eur_openx_w01_r21.dts:5867`). The
host-wake GPIO is converted to an IRQ and requested by LPM init
(`btpower.c:1132-1171`); BT wake is driven by the LPM path (`1024-1053`).

This supports a candidate, separately reviewed live-WLAN probe, not a claim
that the current one is safe: require independent USB/SSH recovery, a fresh
regulator-summary baseline proving CNSS's vote and no unexpected clients, BT
ownership of reset/wake GPIOs, and cleanup that restores only the BT vote/state
it acquired. The existing probe still requires WLAN absent because it
unconditionally owns BT power-off cleanup and has not been designed/tested for
concurrent CNSS operation. Do not remove that guard yet.

The version exchange itself remains unproven: `0xbfad` only controls power;
the H4 version command and QTI sequencing are a separate transport state
machine as documented by `HARDWARE_REUSE_BT_PREFLIGHT_2026-09-21.md`.

## Audio: deferred-probe trigger versus a never-bound machine device

The generic kernel `device_reprobe()` implementation first detaches an
attached driver, then rescans the bus (`drivers/base/bus.c:720-734`). It is
not a “retry only” operation. The generic deferred-probe worker retries only
devices placed on its internal deferred list (`drivers/base/dd.c:73-188`);
there is no userspace write that safely adds an already-bound platform device
to that list.

The live source topology has `sound` bound to `rainbow-sound` and
`0.abox-tplg` bound to `abox-tplg` (as recorded in the companion audio
preflight). `rainbow_sound_probe()` schedules a global card-registration work
item (`sound/soc/samsung/rainbow_prince.c:1547-1566,1568-1680`) and retries
only `-EPROBE_DEFER`. Its remove callback releases OF references but does not
cancel/flush that work or unregister a card (`1683-1715`). The topology remove
path unregisters/releases firmware but `abox_tplg_remove()` itself is empty
(`sound/soc/samsung/abox/abox_tplg.c:2339-2379`), while probe registers an IPC
handler (`2322`). These facts make detach/reprobe or unbind/bind unsafe on the
current boot, even if firmware is now visible.

The work item is a process-global `DECLARE_WORK` (`rainbow_prince.c:1566`),
not a per-device sysfs operation. Probe queues it once; after its ten one-
second attempts (`1550-1564`) there is no source path that queues it again
except a fresh probe. The live parent check confirms `sound`/`rainbow-sound`,
`0.abox-tplg`/`abox-tplg`, and `18c50000.abox`/`abox` are bound,
`devices_deferred` is empty, and the only ALSA cards remain `aboxvdma` and
`aboxdump`. The unbound `audio-codec-dummy` platform node is not evidence that
the machine card is deferred: its `snd-soc-dummy` component/DAI is already
registered. No safe “kick the stopped work” seam exists from userspace.

Therefore there is no safe deferred-probe trigger action in this audit. A
read-only check may confirm that an *unbound* device appears in
`devices_deferred`, but triggering retry is state-changing and is not
authorized here. It cannot retry the current bound Rainbow device. The next
feasible audio change remains a clean boot/session with the four
firmware/topology files visible before probe. Do not write `unbind`, `bind`, or
invoke `device_reprobe()` on this boot.

## Root-readable bound-state and dependency evidence

Before any future parent-approved action, record (read-only):

* `readlink -f /sys/bus/platform/devices/sound/driver` and
  `/sys/bus/platform/devices/0.abox-tplg/driver` (bound driver; absent means
  unbound).
* `/sys/bus/platform/devices/sound/uevent` and
  `/sys/bus/platform/devices/0.abox-tplg/uevent` (`DRIVER`, `MODALIAS`, and
  platform identity).
* `/sys/kernel/debug/devices_deferred` if debugfs is mounted; presence there
  is the available kernel evidence that a device is deferred. Absence is not
  proof of a successful probe if debugfs is unavailable.
* Supplier/consumer links under
  `/sys/bus/platform/devices/sound/` and `0.abox-tplg/` (where present), plus
  the ASoC component/card views and `/proc/asound/{cards,pcm}`.

These checks establish bound state and observable dependency links without
mutating the device. They do not make a bound-device reprobe safe, and they
do not prove normal PCM registration or speaker/microphone operation.

## Gate decision

1. Keep the WLAN-absent guard on the existing Bluetooth transport probe.
2. Do not attempt BT power/version ioctl while preserving the only remote
   network unless a parent-approved shared-rail design and recovery path are
   provided.
3. Do not trigger audio reprobe or unbind/rebind on the current boot. Stage
   firmware before a clean probe and inspect the bound/deferred evidence above.
