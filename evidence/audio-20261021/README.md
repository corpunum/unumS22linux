# Audio: firmware-graph DMA path solved, concurrency race fixed — 2026-10-21

Device: SM-S901B/DS native RECOVERY (kernel 5.10.260-g4e5c5ad7d950, ~4.5 days
uptime). No reboot, no partition write, no module load/unload. The amp volume
was the owner's setting (`/srv/s22/buttons/volume` = 0, i.e. the amps' digital
volume = 0 = muted). It was raised to level 2/10 (raw 561, −32 dB) only during
the loopback tests marked "level 2" and restored to 0 each time. Test tone:
1 kHz, 0.2 full scale, 1.5 s.

## TL;DR
* **Why RDMA2/RDMA3 "never advanced":** they are not frontends. In the stock
  firmware graph (`sectiongraph_tplg`), `ABOX RDMA2_A` / `RDMA2_1` are *source
  selectors*: route `rdma2-vpcmout` = `calliope_2 → VPCMOUT_DAI2 → VDSP3 → RDMA2 → SPUS`.
  The base topology binds `VPCMOUT_DAIn` to the virtual PCM `calliope_n`
  (`"VPCMOUT_DAI2, , calliope_2 playback"`). The stock HAL never opens RDMA2.
  `libaudioproxy2.so` `get_pcm_device_number()` returns 100 / 102 / 105 (and
  6/8/9/26) for playback, and 110/111/112/115/116 for capture. Earlier trials
  wrote PCM data into the graph's *output* DMA and waited for a firmware pointer
  IPC that only comes when the frontend is open.
* **Fix:** play into `hw:0,102` (calliope_2) or `hw:0,100` (calliope_0) and
  record from `hw:0,110` (calliope_10), using the stock mixer routes and
  HAL-sized periods (480–960 frames). DPCM then opens RDMA2/RDMA3/WDMA1 as
  backends by itself, and the firmware graph runs.
* **Acoustic proof (phone's own mic):** stock-equivalent playback calliope_2 →
  speaker, captured through stock-equivalent calliope_10 → the 1 kHz tone at
  ~1050× the noise floor for the full 1.5 s, at the right pitch and timing.
* **Bug fixed:** s22-say and s22-rec raced on the once-per-boot `defaults`
  batch (different locks). A concurrent first use re-applied defaults
  mid-capture (NSRC4 = RESERVED), so arecord got EIO. Now they share
  `/run/s22-audio-defaults.lock`.

## Experiments (in order)
| # | Test | Result | File |
|---|------|--------|------|
| 1 | RDMA6 playback + WDMA4 mic concurrently (first audio use this boot) | RDMA6 hw_ptr 18000→36000→54000 (≈48 k/s). **Capture EIO** | rdma6-wdma4-loopback.txt |
| 2 | WDMA4 capture alone | hw_ptr ≈48 k/s, 2 s WAV written | wdma4-capture-alone.txt |
| 3 | Repeat concurrent (warm) | both advance, 5 s WAV | concurrent-retry.txt |
| 4 | Repeat run-1 timing | both advance; log shows run 1 coincided with the defaults batch | concurrent-run1-timing.txt |
| 5 | FFT of the run-3/4 recordings | **no tone**: amps muted by owner volume 0 (Digital PCM Volume = 0) | (analysis inline in report) |
| 6 | Loopback at level 2 via s22-say (RDMA6) | **tone 1000 Hz, ~1000–1100× noise for 1.5 s**; volume restored to 0 | loopback-level2.txt |
| 7 | Defaults race fix deployed; marker removed; rec + say 1.2 s apart ×2 | 2/2 complete 4 s recordings (was EIO) | defaults-race-fixed.txt |
| 8 | calliope_2 (hw:0,102), VDSP3 `rdma2-sbll-vpcmout`, aplay default 6000-frame periods, muted | backend RDMA2 opened by DPCM, frontend hw_ptr 18000, then **Calliope data abort at 0x82800000** (firmware DRAM window +40 MiB), EIO, ABOX OFFLINE | calliope2-muted-run1.txt, calliope2-abort-followup.txt |
| 9 | Same with `rdma2-vpcmout` while still OFFLINE | invalid: no firmware reload, EIO | calliope2-vpcmout-muted.txt |
| 10 | `printf CALLIOPE > /proc/abox/failsafe/reset` (stock HAL recovery) | silent reset, failsafe_end, **ONLINE**/suspended | failsafe-reset.txt |
| 11 | calliope_2 `rdma2-vpcmout`, 6000-frame periods, muted | rc 0, no abort, pointer ~1.7× real time | calliope2-vpcmout-muted-run2.txt |
| 12 | #11 with loopback at level 2 | tone 1000 Hz ~1000× noise but only ~0.75 s of 1.5 s | loopback-calliope2-level2.txt |
| 13 | calliope_2 `rdma2-vpcmout`, periods 960×4 and 480×4, level 2 loopback | **real-time pointer, full 1.5 s tone 1000 Hz ~1000–1090× noise** | loopback-calliope2-periods.txt |
| 14 | calliope_2 `rdma2-sbll-vpcmout`, 480×4, muted | rc 0, real-time, **no abort** (the #8 abort needs the large period) | calliope2-sbll-small-periods.txt |
| 15 | calliope_10 capture (hw:0,110), stock media-mic route | DPCM opened WDMA1 (24-bit 4 ch ION); frontend ≈48 k/s; rc 0 | calliope10-capture-silent.txt |
| 16 | **Full graph loopback**: calliope_2 play + calliope_10 record, level 2 | **tone 1000 Hz ~1010–1080× noise for 1.5 s**, clean stereo, no 4-slot distortion | loopback-fullgraph-level2.txt |
| 17 | calliope_0 (hw:0,100), bdmixer-normal → RDMA3, 480×4, muted | DPCM opened RDMA3 (32-bit); real-time pointer; rc 0 (not acoustically checked) | calliope0-bdmixer-muted.txt |
| 18 | `S22_AUDIO_PATH=graph s22-say --wav tone`, level 2 loopback; default path re-run muted | graph: pcm102 real-time, aplay_rc 0, tone 1000 Hz ~650–740× noise for 1.5 s; default rc 0 | s22say-graph-optin.txt |

ABOX ended ONLINE after every run from #10 on.

## What the Android backups contain that matters for audio
* Raw partition images under `~/s22-private-backups/20260920T062514Z/`:
  `super.img` (11.7 GB, holds vendor), vendor_boot, boot, recovery, dtbo.
  EFS/sec_efs/keystorage were not touched.
* Vendor audio, already recovered on 09-22 in `rootfs/audio-hal-audit-20260922/stage`:
  `audio.primary.s5e9925.so` (32-bit primary HAL), libaudioroute, `mixer_paths.xml`,
  `mixer_gains.xml`, audio policy, `abox_tplg.{bin,conf}`. Newly recovered
  today (private, git-ignored `rootfs/audio-hal-proxy-20261021/`):
  **`libaudioproxy2.so`** (Exynos AudioProxy, the actual ABOX/PCM logic),
  `libaboxpcmdump.so`, and init rc files (`vendor.audio-hal`,
  `vendor.secaudiohal-aidl`, `exynosaudiodump_service`). These start only the
  HAL services: there is no separate ABOX-handshake daemon.
* ABOX firmware: all 25 images the phone loads (calliope_sram/dram,
  abox_tplg, sectiongraph_tplg and 21 ext-bins) are **byte-identical** to
  stock vendor (re-extracted from super.img with f2fs-tools 1.16 + the repo's
  LZ4 recovery). `atune.bin` (ext-bin 6) is absent on stock vendor too.

## Remaining
* Default `s22-say`/`s22-rec` still use the hardware-direct RDMA6/WDMA4 path
  (owner-accepted). The graph path is opt-in (`S22_AUDIO_PATH=graph`) for
  s22-say. A graph-path recorder exists as a tool (`audio-calliope-rec.sh`).
* Large periods (6000 frames) on the graph frontends are unsafe: they lost
  audio, and with SBLL they crashed Calliope. Always use ≤ 960-frame periods.
* Not tested: calliope_5 (105), voice-call/VTS paths, haptics PCM,
  earpiece over the graph, and behavior after a reboot (nothing boot-time
  changed).
