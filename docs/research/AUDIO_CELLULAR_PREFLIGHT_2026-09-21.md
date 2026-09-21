# S22 audio and cellular preflight — 2026-09-21

This began as a bounded read-only audit of the live S22 native boot and the
pinned Lineage source. Later parent-authorized operations staged the verified
core/topology files and restored the original runtime-power state; no audio
device node was opened and no PCM ioctl, service start, radio action,
partition write, or reboot was performed. A separate bounded read-only radio
image copy is recorded in the cellular section. The current checkout was not
cleaned or reset.

## Source and live evidence

Pinned source revisions:

| tree | revision |
| --- | --- |
| `lineage/android_kernel_samsung_s5e9925` | `4e5c5ad7d950e4de0688b5663965f2075654b2ad` |
| `lineage/android_device_samsung_r0s` | `142838b2bf6c1c8c69d9bee8cfcb4e846b0769bd` |
| `lineage/android_device_samsung_s5e9925-common` | `7be96871126c07d6d51d250a4e0048b58e94eb63` |

The live DT reports an ABOX node at `18c50000`, a `sound` node compatible
with `samsung,rainbow-prince`, and a CPIF node compatible with
`samsung,exynos-cp`. The live observations below supersede older BORE captures
where they differ.

## Audio: why there is no PCM card

### Initial pre-startup state

Before the bounded core-startup trial, the live ALSA inventory was:

```text
/proc/asound/cards
 0 [aboxvdma]: abox_vdma - abox_vdma
 1 [aboxdump]: abox_dump - abox_dump
/proc/asound/pcm
(empty)
```

`/sys/class/sound` contains only `card0`, `card1`, `controlC0`, `controlC1`,
and `timer`; there are no PCM playback/capture entries. This is not an audio
playback result: no PCM device was opened.

The important distinction is that the kernel closure is present. The live
module list includes `snd_soc_samsung_abox`, `snd_soc_samsung_vts`,
`snd_soc_samsung_slif`, `rainbow_prince`, `snd_soc_cs40l26`, and the Cirrus
amplifier modules. Platform metadata shows:

```text
/sys/bus/platform/devices/sound -> driver rainbow-sound
/sys/bus/platform/devices/0.abox-tplg -> driver abox-tplg
```

The ASoC debug component list includes `0.abox-tplg`, the ABOX DAIs, both
`cs35l41` codecs, and `cs40l26-codec`, while `/sys/kernel/debug/asoc/cards`
is empty. Therefore “machine/codec module absent” is not supported by this
audit. The machine driver is bound, but its card registration did not produce a
usable card. The current ring buffer contains no matching Rainbow/ABOX error
lines, so this audit cannot distinguish the final `-EPROBE_DEFER` retry from a
different registration failure.

Firmware state is a stronger measured blocker. The guardian root was checked
through `/proc/1/root` (the SSH process has a different root namespace):

```text
/proc/1/root/vendor/firmware: qca6490/, tsp_stm/, wlan/, WLAN ini files only
/proc/1/root/lib/firmware/sgpu: vangogh_lite_unified.bin only
```

The exact ABOX core/topology names are absent there:
`calliope_sram.bin`, `calliope_dram.bin`, `abox_tplg.bin`, and
`sectiongraph_tplg.bin`. The live ABOX metadata is also consistent with an
unstarted core: `calliope_version` bytes are `00 00 00 00 0a 00`,
`runtime_status=suspended`, `reset_count=0`, and `service=1`.

The kernel command line in the pinned DT sets
`firmware_class.path=/vendor/firmware`. `sound/soc/samsung/abox/abox_core.c`
parses each `samsung,name` child and requests it with
`request_firmware_direct()` (falling back to an asynchronous request).
`abox_tplg.c` requires `abox_tplg.bin` and treats `sectiongraph_tplg.bin` as
optional; a missing required topology returns `-ENODEV`. Finally,
`rainbow_prince.c` schedules card registration and retries ten times only for
`-EPROBE_DEFER`, then gives up. That source path explains why VDMA/debug cards
can exist without a Rainbow PCM card, but the exact registration errno remains
unproven without a fresh kernel log.

The host has matching recovered bytes. Core/topology hashes are:

```text
786ae058d4c439c01edf856abb52184a92e8d25e90b1b748e380186bed84796d  calliope_sram.bin
d2460b85f1e8ceabe6a23e103688932c03ca5aca6da0de5ed95ff78dfd8feccd  calliope_dram.bin
3690d21b6a7e91242ee423ea655604842969607c318769fe73fbc6db826eb9da  abox_tplg.bin
ddc3874ec797a085c8497a240d47b995fedbcc5c876483c94e2f236ecb79f782  abox_tplg.conf
```

They are under
`rootfs/bt-audio-vendor-assets/vendor/firmware/`. The extra-blob recovery is
documented separately in `rootfs/abox-audio-vendor-assets/README.md` and its
`recovery.log`; do not synthesize missing DT-named blobs.

### Core startup result and topology retry safety

The parent-authorized one-shot startup made the four files visible in the
guardian namespace and reached a live Calliope core. The private receipt
records the following bounded result without reproducing raw kernel output or
identifiers here:

```text
calliope_version: 6XH0
ABOX runtime: active during observation, restored to auto/suspended
ABOX reset_count: unchanged/clean
ABOX dump/debug PCM entries: 34 appeared
ALSA cards: still only aboxvdma and aboxdump
normal Rainbow playback/capture PCM: absent
same boot and 4B health: yes/healthy
```

This proves the recovered core firmware boots and the ABOX dump/debug path is
alive. It does not prove a normal machine card or usable playback/capture
path. The shipping kernel configuration is also confirmed live as product
shipping with ABOX debug disabled.

The pinned source does not make a dynamic topology retry safe:

* `samsung_abox_tplg_remove()` calls `snd_soc_tplg_component_remove()`,
  unregisters the component, releases firmware, and clears `dev_abox`, but
  `abox_tplg_remove()` itself is empty. Probe registers the IPC_SYSTEM handler
  through `abox_ipc_register_handler()`; there is no matching unregister in
  the topology remove path, leaving a stale handler across an unbind/rebind.
* Topology widget unload removes list entries, but topology-created dump
  registration is asynchronous and the source has no topology-side cancel or
  complete for the dump-registration work. A retry can therefore race stale
  dump/platform objects.
* `rainbow_sound_probe()` schedules `rainbow_register_card_work`; its remove
  callback does not cancel/flush that work and does not unregister a card. The
  worker can use the old card/device after a sound-driver unbind.
* The topology link loader silently accepts a failed
  `abox_vdma_register_component()` by storing a NULL platform device. That is
  a concrete metadata/DAI-closure mismatch path which can leave a topology
  component present without a valid PCM platform rather than producing a
  safe retry signal.

Consequently there is no safe minimal live unbind/bind sequence for this
kernel. The theoretical order would be sound remove first, then
`0.abox-tplg`, followed by topology bind and sound bind, but it must not be
attempted until the driver has explicit work cancellation, IPC-handler
unregistration, dump cleanup, and card unregister fixes. A clean reboot/session
with the files staged before the supported probe order is the safer future
experiment; do not mutate either platform device on the current boot.

### Original reversible audio experiment

The following was the bounded startup procedure used for the core milestone
(the dynamic retry in step 5 was not used):

1. Save the current cards/PCM list, `calliope_version`, ABOX runtime state, and
   a fresh dmesg baseline.
2. Temporarily expose the files for one boot/session. Do not make a persistent
   overlay and do not unbind the ABOX core.
3. Parent-authorized state-changing trigger: write `on` to
   `/sys/devices/platform/18c50000.abox/power/control`, then allow the core
   firmware request and topology retry windows to complete.
4. Re-read `calliope_version`, `/proc/asound/cards`, `/proc/asound/pcm`,
   `/sys/class/sound`, ASoC debug cards, and dmesg. Do not open a PCM node in
   this experiment.
5. The previously proposed unbind/bind fallback is **not safe on this pinned
   kernel** because the source review above finds uncancelled work and stale
   IPC/dump registrations. Never unbind either platform device or reload its
   module on this boot.

Acceptance requires all of the following: a Rainbow/machine card in
`/proc/asound/cards`; non-empty playback/capture entries in
`/proc/asound/pcm`; corresponding `pcmC*D*` and control entries in
`/sys/class/sound`; and no new firmware, topology, or ASoC registration errors.
This would establish a kernel PCM path only, not speaker output, microphone
capture, Bluetooth audio, or Android Audio HAL operation.

## Cellular: CPIF is initialized, not booted

### Measured state

The live CPIF platform device is bound to `cp_interface`, and
`/sys/devices/platform/cpif/modem_state` is `INIT`. Live DT metadata is:

```text
mif,name=s5133ap
mif,modem_type=0
mif,cp_num=0
mif,protocol=0
mif,ipc_version=0x32
mif,link_type=0
mif,link_name=shmem
cp_shmem/use_mem_map_on_cp=1
```

The kernel modules `cpif`, `cpif_page`, `exynos_cpif_iommu`, `mcu_ipc`,
`shm_ipc`, `direct_dm`, and `dev_ril_bridge` are live. The kernel-created
`/dev/umts_*` class entries and `rmnet0` through `rmnet7` exist, but all
observed `rmnet*` interfaces are `operstate=down`, CPIF packet queues report
inactive, and `toe/status` reports `hal_ready:0 ifaces_num:0`. These are
transport registrations, not modem or data-plane proof.

The pinned `s5e9925_defconfig` selects `SEC_MODEM_S5000AP=m`, shared-memory
link support, CP secure boot, and SIPC-related dependencies; S5100 and PCIe
modem support are disabled. The matching DT overlay names `s5133ap` and uses
`modem_type=0`, which maps to the S5000AP implementation in
`drivers/soc/samsung/cpif/modem_variation.c`.

The source boot path is not a passive probe. `modem_ctrl_s5000ap.c` transitions
to `STATE_BOOTING`, calls the link's normal-boot preparation/start hooks, and
only changes to `STATE_ONLINE` after the CP-side initialization checks. The
shared-memory link defines `CMD_INIT_START`, `CMD_INIT_END`, and
`CMD_PIF_INIT_DONE`; `link_device.c` gates `CMD_INIT_END` on `rild_ready()`. In
other words, a working CP image plus the matching userspace boot/RIL contract
is required; the existence of `umts_boot0` is not enough.

### Firmware and userspace closure audit

The common Android device recipe packages `cbd`, `libsec-ril`,
`secril_config_svc`, and `sehradiomanager` with `cbd` protocol `sipc`. Its
proprietary manifest names `vendor/bin/hw/rild`, `libsec-ril`,
`libsecril-client`, Samsung radio/sehradio VINTF manifests, and the Samsung
radio AIDL libraries. The native guardian root currently has none of
`cbd`, `rild`, `libsec-ril*`, or the matching RIL init fragments. The r0s
manifest has no CP image entry, and `TARGET_NO_RADIOIMAGE := true` is set in
`BoardConfigCommon.mk`.

During the initial cellular preflight, the saved-backup inventory was checked
by metadata only. It contains
metadata/boot/vendor_boot/recovery/dtbo/vbmeta, `super`, userdata, and optics/
prism images, but no separately saved `modem`/radio partition. The
`super.img` and userdata images were not opened or scanned. Consequently, no
exact CP firmware bytes were available in the small recovered host assets or
the saved-backup manifest. Do not infer that the large `super.img` contains a
usable image, and do not read it for this preflight.

A fresh live metadata-only check through the guardian root found a radio block
target even though the native root has no `modem` or `RADIO` by-name link:

```text
/proc/1/root/dev/block/by-name/radio -> ../sda17
  device 259:1, PARTNAME=radio, size=163840 sectors (80 MiB), read-only=0
/proc/1/root/dev/block/by-name/cp_debug -> ../sda18
  device 259:2, PARTNAME=cp_debug, size=15360 sectors (7.5 MiB), read-only=0
/proc/1/root/dev/block/by-name/cpefs -> ../sdd1
  device 8:31 (no corresponding native /sys/dev/block metadata exposed)
```

At that initial stage only symlink, device-number, partition-size, and uevent metadata were read;
no block device was opened and no bytes were extracted. Therefore the live
`radio` partition is a possible exact CP-image extraction source, not evidence
that its contents are suitable. A parent-approved, read-only extraction of
`/proc/1/root/dev/block/by-name/radio` could establish a hash and image identity
without downloading a large archive, after which the image must still be
matched to the S5000AP/`s5133ap` userspace closure. Do not read `radio`,
`cp_debug`, or `cpefs` as part of this preflight.

The parent subsequently performed that separately approved, bounded read-only
copy from `radio` (without opening `cp_debug`, `cpefs`, or EFS). The 83,886,080
byte host artifact is
`rootfs/hardware-reuse-20260921/radio-readonly.img`, with matching phone/host
SHA-256
`386155dc00d3034b22139b01e402515e71f4a0672df761d665ce74155c7b962e`.
The pinned 32-byte `struct cp_toc` layout identifies bounded
`TOC/BOOT/MAIN/VSS/NV/OFFSET/INFO` entries. Embedded strings include the exact
`S901BXXSIFYI3` build marker and `S5133AP_RAINBOWR0` board marker. These are
offline firmware identity evidence, not CRC/signature or boot compatibility
acceptance. This removes the “no CP bytes available”
blocker, but live `modem_state` remains `INIT` and the matching `cbd`/SIPC/RIL
userspace closure is still absent.

No claim is made here about SIM identity, data, SMS, calls, IMS, or VoLTE.

### Next reversible cellular experiment

The immediate reversible gate remains offline: the existing matching radio
image is now backed up and identified; recover and audit the matching
`cbd`/SIPC/RIL closure in a disposable Android-style root. Verify
ELF dependencies, init fragments, VINTF manifests, and the CP image/build
identity before any device boot. The current native root cannot pass this gate.

Only after that gate passes and the parent explicitly approves radio state
change should a one-shot boot test be considered: hold the rescue path, record
the CPIF baseline above, start only the matching Samsung `cbd`/baseband path,
and observe the bounded boot window (the kernel's `MIF_INIT_TIMEOUT` is 15 s).
Stop on timeout or crash; do not add data, SIM, telephony, or IMS operations.

The minimum acceptance is `modem_state: INIT -> BOOTING -> ONLINE`, a CP-side
`CMD_INIT_START`/`CMD_INIT_END` sequence without crash, and no new CPIF fault;
`rmnet` becoming present or administratively up is not sufficient. Data-plane
acceptance would be a separate, explicitly authorized experiment after this
boot gate. If the CP remains `INIT`, preserve the fresh kernel log and CPIF
status, then stop rather than retrying blindly.

## Bottom line

The ABOX firmware-visibility blocker is resolved and the authenticated DSP
boots as version `6XH0`. Only 34 dump/debug PCMs appeared: usable machine-card,
mixer, speaker and microphone paths remain unaccepted. Live driver rebind is
not safe given the reviewed cleanup defects.

Cellular has a bound shared-memory kernel transport and a verified read-only
backup of the existing FYI3 radio image. The modem remains `INIT`, with the
Samsung `cbd`/SIPC/RIL userspace closure still missing from the native runtime.
No SIM, mobile internet, voice, SMS or IMS operation is accepted.
