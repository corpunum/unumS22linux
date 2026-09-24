# S22 HCI-only candidate and controlled trial — 2026-09-24

This is a host-built, RECOVERY-only candidate. Nothing was flashed or rebooted
for this preparation. Source/build checks and module compatibility are not
hardware acceptance.

## Candidate identity and build evidence

The candidate changes only `net/bluetooth/hci_sock.c` relative to the pinned
Lineage kernel source `4e5c5ad7d950e4de0688b5663965f2075654b2ad`. The clean,
committed candidate source is `f52cbbd7e2783d529e1e5742d94e0fd64889bbdf`;
the recorded patch is
[`bt-hci-socket-restore.patch`](../tools/hardware/bt-hci-socket-restore.patch).
No NPU patch is included. The base init/bootstrap, ramdisk, 324 ramdisk
modules, DTB and recovery-DTBO are preserved byte-for-byte.

| Item | Value |
|---|---|
| Recovery image | `builds/bt-hci-loader-compatible-20260924-repro/recovery.img` (local, ignored build artifact; not published) |
| Recovery image SHA-256 | `42da267f3dd9f94f30f62a95fb2ac13f91d4cf98f1a2307f7cc14e45d9c49be5` |
| Embedded kernel `Image` SHA-256 | `7738564db77e4a6183ffaa875fc12f8168a58e8135a3bdc86e27b127b47fc05c` |
| Kernel release | `5.10.260-g4e5c5ad7d950` (intentionally stable so the existing exact-release boot/helpers and module paths remain valid; the actual candidate source commit and image hash are recorded separately above and in the local manifest) |
| `.config` SHA-256 | `d762d5fc71e369013ee36657d007063ce1f0faca9707d5f9ccfba2597b7fcd16` |
| `Module.symvers` SHA-256 | `15fc69e815cb4da6cb3372f4b5141005ab2e770b0414b67f230f1b185bf03df7` |
| Compiler / linker | Ubuntu clang 18.1.3 / Ubuntu LLD 18.1.3; binary hashes are in the local manifest |
| Build config | `CONFIG_MODVERSIONS=y`, `CONFIG_BT=y`, `CONFIG_BT_HCIUART=y`, `CONFIG_BT_HCIUART_QCA=y`, `CONFIG_LOCALVERSION_AUTO=n`, `CONFIG_LOCALVERSION="-g4e5c5ad7d950"` |
| Pinned recovery base SHA-256 | `758fc9d30491e17b7c829a89d338ba69476efa15a1280deb8a1b9b8009687f4b` |
| Preserved ramdisk SHA-256 | `0dd9dda696c26ccf4c99d77f9d24f334f0c4baece5e19312bcc12f2963841e5d` |
| Partition length | `100663296` bytes |

The checked-in packaging tool records source commit/cleanliness, build config,
`Module.symvers`, kernel bytes, compiler/linker hashes, image and unchanged
payload hashes. It verifies the AVB footer/hash with the pinned tool, then
re-unpacks the image and checks header and payload preservation. Its
`algorithm NONE` footer is not proof of Samsung authentication or bootability.
No image, module tree, firmware or private build artifact is part of the Git
change.

## Pinned module-loader verdict

Reference: LineageOS kernel commit
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`, `kernel/module.c` functions
`setup_load_info()`, `same_magic()`, `check_modinfo()`,
`check_modstruct_version()` and `check_version()`. The loader passes presence
of `__versions` to `same_magic()`. With version records, it compares the
vermagic bytes starting at the first space, not the release prefix. Without
version records (or with `CONFIG_MODVERSIONS` disabled) the full string is
compared. It checks the module's `module_layout` CRC before allocation and
compares each recorded import CRC; an absent per-symbol record warns and is
accepted by this kernel, so the host gate reports evidence completeness
separately and does not use force-load/ignore flags.

The host-only preflight passed:

- all 324 candidate ramdisk modules have `__versions`, matching flags, and
  matching recorded import CRCs;
- the selected Lineage 20260915 `wlan.ko` is a separate 325th known
  candidate-required source, SHA-256
  `cbf8932d079e97006a5b7aae0e1b5acfe65b3e8b5113ab8fa095daa36796738d`, and
  all 495 recorded CRCs match;
- 17,042 total recorded symbol-version entries and all 325 `module_layout`
  records match the candidate `Module.symvers`;
- the stock FYI3 WLAN binary is a known inactive alternate, not part of the
  selected runtime source set; its CRCs do not match (218 mismatches, two
  missing candidate exports), so it must not be substituted;
- release-only vermagic differences are loader-compatible under the pinned
  rule when version-section presence and all remaining flags match.

The known candidate source inventory is 325 modules; actual candidate-boot
`/proc/modules` membership and any other runtime source remain unknown until a
live read-only query. This host verdict is not execution of the kernel loader.
Preflight also reports clean source/artifact provenance separately from
loader compatibility and leaves operational helper assumptions as a separate
check.

## Exact-release/helper compatibility audit

The candidate deliberately retains the running clean release string.
Candidate boot/rootfs helpers and firmware directories were audited for
release literals and lookup assumptions: `deploy-audio-recovery.py`,
`run-bt-version-once.py`, Wi-Fi staging/autostart/firmware helpers, audio
diagnostic guards and initramfs `modprobe` paths all expect or resolve the
current `5.10.260-g4e5c5ad7d950` identity. Keeping it avoids a new lookup path;
the new source commit and exact kernel/image hashes make the changed kernel
explicit rather than disguising it. The recovery `s22-reboot` helper flushes
filesystems and selects Samsung `RESTART2 recovery`; it has no kernel-release
string check. It must be invoked only as `s22-reboot recovery`, never `normal`.

The independent rollback is the host-side native RECOVERY image
`builds/audio-extra-v2-20260922/recovery.img`, SHA-256
`758fc9d30491e17b7c829a89d338ba69476efa15a1280deb8a1b9b8009687f4b`.
That file was historically read back from `/dev/block/sda16` byte-identically
at 2026-09-24 05:09 UTC; it was re-hashed on the host during this preparation,
but the phone was not queried again. A separate known-good Lineage recovery
remains available at SHA-256
`b5bf01c4a47091eb95078fc69b133b44c2b453b31c23433594c5b605e3747b55`.
Neither rollback writes Android userdata or any other partition.

## Recovery-host procedure and single trial gate

Installed `samloader` help confirms `detect` returns immediately by default
and `flash` supports explicit `-p RECOVERY <image>` plus `--no-reboot`.
`detect --verbose` is read-only. There is no need to use `reboot-download`,
`dump-pit`, repartitioning, `--skip-size-check`, or any other partition.

1. Before connecting, verify the candidate image hash above, exact partition
   size, and the host rollback image hash/availability. Keep the rollback
   path at hand. The current native rollback was re-hashed locally.
2. With the owner present and USB connected to the actual recovery host, put
   the phone into Download Mode. Run only
   `/home/corpunum/s22-linux/tools/samloader/samloader detect --verbose` and
   require successful detection. This is the independent-rescue qualification;
   USB SSH/Tailscale through the running kernel is not a substitute.
3. Before any partition write, test the return path on the currently installed
   native RECOVERY image. The candidate is not involved yet. Use the physical
   RECOVERY handoff in step 5 and require a live shell plus `/proc/boot_reset`
   and BORE confirmation of actual RECOVERY. Then, while the owner remains
   present, return to Download with the existing native helper
   `s22-reboot download`, reconnect/check the screen, and require a second
   successful read-only `samloader detect --verbose`. The helper accepts only
   `recovery|download|normal`; this download-target trial has not yet been
   tested on the phone. If either transition or detection fails, stop without
   flashing.
4. Do not flash until the owner has separately authorized this specific
   RECOVERY write. Then the only candidate-write command is:

   ```sh
   /home/corpunum/s22-linux/tools/samloader/samloader flash --verbose --no-reboot -p RECOVERY /home/corpunum/s22-linux/builds/bt-hci-loader-compatible-20260924-repro/recovery.img
   ```

   The command leaves the device in Download Mode. Do not auto-reboot or
   select Android BOOT.
5. Keep the USB data cable connected. The proposed handoff is: hold Volume
   Down + Side for about 7 seconds until the screen goes black; release
   Volume Down while keeping Side held, then press/hold Volume Up until
   RECOVERY appears. This is an inference combining Samsung's Download-exit
   instruction with an S22+ guide's Volume Up + Side Recovery keys; neither
   source documents this exact combined transition, so it must first pass the
   no-write test in step 3 on this actual device
   ([Samsung Download-mode exit](https://www.samsung.com/us/support/troubleshooting/TSG01212623/),
   [S22+ Recovery keys](https://devicesupport.three.co.uk/guides/device/Samsung/GalaxyS22Plus5G/scenario/clear-cache-partition)).
   If the screen does not clearly enter RECOVERY, stop; do not navigate the
   boot menu or infer the mode. No recovery-menu item should be selected.
6. After boot settles, immediately read `/proc/boot_reset`, BORE, `uname -r`,
   uptime, `/proc/modules`, USB/assistant health and Wi-Fi. Continue only if
   this is genuinely RECOVERY and the ordinary baseline is healthy. Do not
   trust a logo or inferred boot target.
7. Run a single raw socket create/close test, before controller attachment,
   scanning or pairing:

   ```sh
   python3 -c 'import socket; s=socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI); s.close(); print("raw HCI socket create/close: PASS")'
   ```

   On any failure, stop; do not retry or attach a controller. This tests only
   HCI raw-socket lifecycle, not Bluetooth radio operation.
8. To roll back, return to Download Mode with the owner present, run
   read-only `samloader detect --verbose`, verify the rollback SHA and exact
   RECOVERY target, and—after the separate rollback authorization—flash only
   the native rollback with `samloader flash --verbose --no-reboot -p RECOVERY
   /home/corpunum/s22-linux/builds/audio-extra-v2-20260922/recovery.img`.
   Use the same button transition to RECOVERY, then confirm BORE/`boot_reset`;
   do not choose normal Android BOOT. If the candidate boots successfully,
   `s22-reboot recovery` is the existing tested software return path, but it
   is not the independent rescue mechanism and should not be relied on if
   USB or PID 1 is unhealthy.

## Current gate

The host candidate, packaging verification and module compatibility checks
are complete. Independent Download Mode detection from the actual recovery
host, a fresh baseline, physical RECOVERY-entry confirmation and
operation-specific authorization remain open. No candidate flash, reboot,
Bluetooth socket operation or pairing occurred. The next owner action is one
attended session: connect the phone to the recovery host, put it in Download
Mode and confirm the Download screen. The coordinator will do read-only
detection and hash checks first, then test the no-write handoff into the
currently installed native RECOVERY and its return to Download while the owner
is present. Candidate flashing is a later, separate RECOVERY write and still
waits for its own explicit authorization.
