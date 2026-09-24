# S22 HCI-only candidate and controlled trial — 2026-09-24

This records the pre-trial host build and compatibility review for a
RECOVERY-only candidate. No device write or reboot had occurred when that
preparation record was first written; the later authorized attempt and exact
rollback are recorded at the end. Source/build checks are not hardware
acceptance.

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
Before the trial, that file had historical byte-identical readback evidence
from `/dev/block/sda16` at 2026-09-24 05:09 UTC and was re-hashed on the host.
It was subsequently staged and fully read back from the live device during
the authorized rollback recorded below. A separate known-good Lineage recovery
remains available at SHA-256
`b5bf01c4a47091eb95078fc69b133b44c2b453b31c23433594c5b605e3747b55`.
Neither rollback writes Android userdata or any other partition.

## Owner authorization for one unattended native trial — 2026-09-24

The owner has explicitly authorized one unattended installation of this exact
HCI-only candidate to RECOVERY, one targeted `s22-reboot recovery`, and the
gated raw-HCI socket create/close check. The owner also accepts one
conditional restoration of the exact native rollback image if a usable native
shell and the reviewed write/readback route remain available. This narrow
authorization supersedes the physical-attendance and Download-Mode
demonstration prerequisites below for this trial only. It does not authorize
another partition, Android BOOT, Download Mode, BCB/MISC edits, another kernel,
or a retry after an unknown write or reboot outcome.

Independent hardware rescue is still **not demonstrated**. USB/Wi-Fi SSH
through the currently running native kernel are not independent recovery
paths. If the candidate does not return a usable native shell, remote rollback
is not guaranteed and the device may remain inaccessible until the owner is
physically present. At the start of the trial, the rollback image was verified
on the rig and matched RECOVERY; the completed rollback readback is recorded
below.

## Pre-trial recovery-host procedure (historical; superseded for this attempt)

The following Download-Mode/button sequence was the proposed owner-attended
rescue qualification before the narrow unattended authorization. It was not
performed and remains unproven, but it was explicitly superseded as a
prerequisite for the one RECOVERY trial recorded below. It is not a pending
step for that completed attempt.

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

## Pre-trial gate (historical)

The host candidate, packaging verification and module compatibility checks
are complete. Independent Download Mode rescue remains unproven and is not
claimed. The owner has accepted the specific unattended RECOVERY-only trial
and its loss-of-remote-access risk. Before its one write, the coordinator must
complete the reviewed adapter/observer tests, refresh the native baseline,
stage both exact images on `/srv/s22`, and preserve receipts and a phase
journal on the rig. The coordinator must then revalidate RECOVERY's full
baseline hash and target, write only the candidate, verify full readback, and
issue the single targeted recovery reboot as a separate checked action.
This was the pre-trial checklist and is superseded by the execution record
below. It must not be read as saying the authorized attempt did not occur.

## Actual owner-authorized trial and conditional rollback

The owner authorized one unattended write of the exact HCI-only candidate to
RECOVERY, one targeted `s22-reboot recovery`, a gated raw-HCI create/close
test, and one conditional exact rollback. Independent hardware rescue was
not demonstrated and is not claimed.

- The candidate and rollback were staged on `/srv/s22`; both staged files
  were independently checked at 100663296 bytes and against their exact
  manifest SHA-256 values. The rollback staged alongside the candidate was
  the exact image below.
- The candidate was written once to `/dev/sda16` (RECOVERY, 259:0). The
  existing deployment helper flushed the write and verified a full-partition
  readback matching `42da267f3dd9f94f30f62a95fb2ac13f91d4cf98f1a2307f7cc14e45d9c49be5`.
  No reboot was combined with the write.
- Exactly one targeted candidate recovery reboot was requested. SSH
  disconnected, so the request receipt was `UNKNOWN` and no retry was made.
  A new BORE RECOVERY record and the candidate GNU build ID
  `b2dda820b18d410d9bf12f1bd2584567d545991d` subsequently confirmed that the
  intended candidate booted.
- Candidate dmesg contained ten `blocked for more than 120 seconds` warnings
  and ten call-trace markers for TrustZone worker/log threads. Across
  saved candidate samples through approximately 433 seconds of candidate
  uptime, the Pi process existed but its tmux server did not; the Pi assistant was therefore
  not ready. These observations triggered the authorized conservative
  rollback. Source review found that the TrustZone worker's uninterruptible
  wait is intentional; these warnings alone do not establish a new kernel
  defect or implicate the HCI change. The pinned source path is
  `drivers/misc/tzdev/core/kthread_pool.c` (worker wait/schedule path) and
  `drivers/misc/tzdev/core/iwlog.c` (log event wait). A paired full rollback
  dmesg capture was not made. Pi tmux also remained absent after rollback, so that readiness
  issue is not shown to be candidate-specific. HCI testing was not attempted:
  zero raw-socket attempts, controller attachments, scans, or pairings.
- The conditional exact rollback was staged, re-read, and checked. It was
  written once to RECOVERY with the reverse profile; a complete readback
  matched `758fc9d30491e17b7c829a89d338ba69476efa15a1280deb8a1b9b8009687f4b`.
  Exactly one targeted rollback recovery reboot was requested. That SSH
  request also returned `UNKNOWN`; it was not retried. The phone then exposed
  a new BORE RECOVERY record, the exact rollback partition hash, and the
  rollback kernel GNU build ID `b651f4a3df19b10a0ce633b166e01d2758de44de`.
- A private, address-redacted, ten-minute rollback observation completed with
  58/58 valid snapshots on the same RECOVERY boot and expected rollback build
  ID. Full-partition hash reads at the observation start and final sample both
  matched the exact rollback image. Native PID 1, persistent storage, health and
  idle checks, Hyprland, WLAN, required modules/components, and power remained
  ready; the bounded dmesg classifier reported no serious-fault patterns.
  At the final sample (about 960 seconds uptime), `pi` and `ttyd` were present
  but `tmux` remained absent. The native rollback is verified, but full
  resident-assistant restoration is not.

Host hardware-free regressions passed in normal and optimized Python modes;
the three AVB-fixture tests were skipped because the pinned local avbtool
fixture is unavailable in this review worktree. The rollback and candidate
flash receipts are kept privately on the rig. Only the sanitized receipt
summary in [`evidence/s22-hci-trial-20260924.json`](../evidence/s22-hci-trial-20260924.json)
is published; no image, firmware, private trace, boot ID, or network identifier
is included.

Hardware acceptance remains **unproven**. Independent hardware rescue remains
**not demonstrated**. The HCI candidate is not accepted for continued use.
Before any later candidate trial, determine whether these TrustZone warnings
represent a real liveness fault and restore a healthy resident Pi/tmux session.

## 2026-09-24 continuation: corrected readiness and TrustZone review

This section records the new user authorization and preflight evidence for a
second, distinct trial. It does not claim that the second trial has started.
The owner authorized exactly one additional installation of the same HCI-only
candidate to RECOVERY, one targeted `s22-reboot recovery`, one gated raw-HCI
socket create/close test, and one conditional exact rollback. The guarded
existing Pi web helper may be used once on the current baseline and once after
the candidate boot if needed. No Android BOOT, other partition, NPU BOOTUP,
controller attachment, scan, pairing, retry, or independent-rescue claim is
included. The first-trial authorization remains consumed.

The new identity is `hci-candidate-20260924-second`. Deployment receipts use
that identity; reboot, helper-start and HCI one-shot markers are under its own
marker subdirectory, leaving the first-trial root-level markers and receipts
unchanged. The device-wide `/run/s22-recovery-operation.lock` remains the
exclusive operation lock. The new marker directory and forward receipt path
were absent at preflight. At this checkpoint there have been **zero** second-
trial writes, reboot requests, or raw-HCI attempts.

Fresh read-only USB evidence identified the live installation as the exact
known-good rollback: kernel release `5.10.260-g4e5c5ad7d950`, rollback GNU
build ID `b651f4a3df19b10a0ce633b166e01d2758de44de`, a current RECOVERY BORE
record, and full `/dev/block/by-name/recovery` SHA-256
`758fc9d30491e17b7c829a89d338ba69476efa15a1280deb8a1b9b8009687f4b`. The
resolved target is `/dev/sda16`, major:minor `259:0`, 196608 sectors
(100663296 bytes), and it is not mounted. The exact candidate and rollback
host files both re-hash to their pinned values and sizes. `/srv/s22` has
101,979,549,696 available bytes and 1,652,502 free inodes; both second-trial
staging paths are absent. The nearly-full root overlay has 34,844,672 bytes
free and 29,780 inodes, but is not a staging destination and was not changed.
The live WLAN module resolves to
`/srv/s22/hardware/wifi-20260920/modules/wlan.ko`, SHA-256
`cbf8932d079e97006a5b7aae0e1b5acfe65b3e8b5113ab8fa095daa36796738d`, matching
the selected module in the candidate compatibility inventory. There are 325
loaded modules. Independent Download Mode recovery remains **not demonstrated**
and is an explicitly accepted risk for this one trial.

At the read-only snapshot, PID 1 was `native-guardian`; persistent storage,
Hyprland, the exact desktop Pi executable/UID, the Pi model API, idle slots,
loopback browser service, WLAN, required modules/components and target identity
were ready. Battery was Full/100%, 27.3 C, and the latest maximum thermal-zone
reading was 41.0 C. The dedicated browser tmux session was `absent`, which is the designed
on-demand standby state, not evidence that the desktop Pi process or kernel is
broken. The current owner instruction supersedes the earlier interpretation
above that absence itself made the Pi assistant unready. The exact installed
launcher source was found in the preserved owner checkout at
`tools/pi-web/start-agent-web.py`, SHA-256
`9f3cf35237ba40574dc9efd50c7ef43e53786d961a4fa1ef103518a21fa9fb3c`, matching
the pinned installed helper. Its guarded `--start` first verifies the native
guardian, persistent mount, exact ttyd/tmux/library hashes, ownership and
model health. If ttyd is already healthy it returns its existing PID; otherwise
it may start the loopback-only unprivileged ttyd. It does not create the tmux
session, submit an agent task, or request inference. The session is created by
`pi-web-session` only when the browser terminal is actually opened. The guard
was invoked once on the current baseline and reported the already-running
terminal; no duplicate process or interactive session was created.

TrustZone evidence is separated rather than suppressed. The private candidate
capture (255180 bytes; its hash remains private) spans
dmesg timestamps 176.66–354.90 seconds and contains ten hung-task warnings,
ten call-trace markers, and no fatal indicator. All ten warning stacks match
the exact pinned wait paths `tz_worker_handler`, `tz_iwlog_kthread_handler`,
and `handle_log_kthread`; the match requires schedule and expected caller
frames, not a thread name alone. In the pinned source, those functions sleep
on request/event waitqueues. The relevant TrustZone and CHUB files are byte-
identical between base `4e5c5ad7d950e4de0688b5663965f2075654b2ad` and candidate
`f52cbbd7e2783d529e1e5742d94e0fd64889bbdf`; the candidate source delta remains
only `net/bluetooth/hci_sock.c`. This supports treating the observed stacks as
the existing event-wait paths, but it does **not** measure wake/progress and
does not erase the hung-task warnings: `progress_measured=false` and liveness
remains unresolved in every receipt.

A fresh full available `dmesg` read from the rollback was saved privately on
the rig (255691 bytes; its hash remains private), covering
timestamps 8132.85–8429.39 seconds of the current boot. That ring tail has no
fatal or hung-task entries, but it is not full-boot coverage; earlier records
were overwritten and the candidate/rollback captures are not a synchronized
same-uptime comparison. The current unchanged sysctls are timeout 120 seconds,
`hung_task_panic=0`, `hung_task_warnings=0`, `panic=-1`,
`panic_on_oops=1`, and `panic_on_warn=0`. The pinned kernel defaults the warning
budget to 10, and no project sysctl-file or boot-command-line override was
found. Therefore the zero remaining budget is consistent with (but does not
prove the identity of) early rollback warnings that have since rotated out of
the ring. No policy was changed. This is evidence of a repeated baseline-class
warning, not proof that all TrustZone work progresses normally.

The observer now classifies the entire available `dmesg` output up to a 4 MiB
bound, requires complete retrieval and parsing of that available ring, records
that full-boot coverage is false, preserves fatal/warning/trace classes, and
labels exact wait stacks `pinned_wait_stacks_matched_progress_unmeasured`.
Unknown or changed stacks fail the readiness gate; exact source waits remain
explicitly unresolved rather than being reported as healthy progress. The
intended conclusion is narrow: source review found no TrustZone change in the
HCI candidate, the warning stacks are exact existing event waits, and the
rollback's exhausted default warning budget is consistent with the same
baseline behavior. This justifies at most the newly authorized bounded HCI
socket experiment, not unrestricted use or a claim that TrustZone liveness is
proved. A fatal indicator, incomplete ring capture, unknown wait stack, failed
application service, changed boot/image identity, or any material regression
still stops the operation.

The deployment and observer code, tests, and CI allowlist are under independent
review. The hardware-free suite currently passes all 15 scripts in normal mode
and all 12 optimization-safe scripts under `-O`; the three AVB-fixture cases
remain explicit skips and the three normal-only scripts are documented. The
suite has not yet been published or run by hosted CI for this continuation.
The next action is to finish that review and exact-head CI, then re-read the
baseline and journal before staging. No new flash or reboot is implied by this
preflight record.

## Final host review and pre-deployment checkpoint

The independent Luna review found a trace-boundary defect: frames from
separate `Call trace:` blocks could be combined to explain one hung-task
warning. The classifier now accepts a source-wait match only when that
warning's interval contains exactly one trace marker. Hostile regressions split
the expected frames across two traces and across a later warning boundary;
both remain unmatched. Reclassification of the private candidate capture found
ten warnings, ten individually delimited traces, ten source-path matches, and
no measured progress. The warnings remain visible and liveness remains
unresolved. The reviewer approved this fix.

The observer's sequence is now explicit in code and tests: the forward flash
receipt pins the old rollback hash as `before_sha256` and candidate hash as the
full RECOVERY readback. The reboot observer runs after that flash and verifies
the already-installed candidate hash before its one reboot request, then again
after boot. The receipt's `baseline_recovery_sha256` therefore means
pre-reboot observer baseline (candidate), not pre-flash image (rollback). An
integration assertion verifies that hash read precedes the reboot request.
Independent review approved the sequence and test.

At this checkpoint, the latest read-only device snapshot still identifies the
known-good rollback by its RECOVERY boot record, GNU build ID, and full
partition hash. PID 1, persistent storage, Pi/model/browser services, Wi-Fi,
required components, battery/thermal limits, and target identity pass. The
optional dedicated Pi tmux session remains absent by design. `/srv/s22` is the
userdata ext4 mount with about 102 GB free; RECOVERY resolves to unmounted
`/dev/sda16` (259:0), exactly 100663296 bytes. Both trial staging paths, second-
trial marker directory, and second-trial receipt directory remain absent.
Independent Download Mode rescue remains unproven.

The final fixed host suite passed: 15 scripts normally, 12 optimization-safe
scripts under `-O`, zero failures, three explicit AVB-tool fixture skips, and
three documented normal-only checks. Runner policy passed 7/7. Observer tests
passed 30/30 normally, under `-O`, and with `PYTHONOPTIMIZE=1`; TrustZone
classifier tests passed 12/12 in all three modes. These are host checks, not
phone acceptance. No second-trial flash, reboot request, or HCI attempt has yet
occurred at this checkpoint.

The first hosted run for this source, GitHub Actions run `36017639971`, passed
runner policy and the other suites but failed the Pi-session fixture in normal
and optimized modes. The test had inherited the CI host's socket UID rather
than modeling the device's required UID 1000; production correctly rejected
it. The fixture now sets the simulated socket owner explicitly and adds a
wrong-owner/no-query regression. Independent review approved the fixture-only
change; all nine Pi readiness tests pass normally, under `-O`, and with
`PYTHONOPTIMIZE=1`, and the complete local suite again has zero failures. The
new hosted run is pending; no device operation has occurred.
