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

## Follow-up (same day): graph path default, the phone speaks, reboot check

Changes (commit 49910cb and the commit after it):
* `s22-say`/`s22-rec` use the firmware-graph path by default (calliope_2 /
  calliope_10, 480-frame periods). `S22_AUDIO_PATH=direct` keeps RDMA6/WDMA4.
  s22-rec records 4 channels, so the raw 4-slot format and slot 0 = main mic
  are unchanged for s22-converse/s22-assistant.
* `s22-say` raises a muted amp to `S22_SAY_LEVEL` (default 4/10) for the
  playback only and restores the previous raw value on exit. `s22-keepalive`
  skips its mute check while `/run/s22-say.lock` is held.
* An OFFLINE ABOX is reset automatically (`/proc/abox/failsafe/reset`).
  s22-say retries once, then falls back to RDMA6. s22-rec resets before and
  after a failed capture.
* `s22-touchd`/`s22-ui tts --play [--out O]` render and then speak. The
  OpenUnum `tts-s22` plugin 0.2.0 speaks by default (`play=false` renders
  only) and adds the Greek engines.
* `bluetooth` keepalive service (`s22-bt up`, needle = HCI holder, health =
  `/sys/class/bluetooth/hci0`).

| # | Test | Result | File |
|---|------|--------|------|
| 19 | Default s22-rec + s22-say (`S22_SAY_LEVEL=2`), pre-reboot | amp 0 → 561 during playback → 0; pcm102/pcm110 real-time; slot-0 tone 1000 Hz ~620–750× noise for 1.5 s | default-graph-loopback.txt |
| 20 | `S22_AUDIO_PATH=direct` s22-say (level 0) / s22-rec | rc 0 / rc 0 | fallback-and-auto-recovery.txt |
| 21 | Induced Calliope fault (6000-frame periods + SBLL), then s22-say | OFFLINE → "failsafe reset" → rc 0, ONLINE | same |
| 22 | Induced fault again, then s22-rec | 2nd induction did not fault; 3rd did → s22-rec reset → rc 0, ONLINE | same |
| 23 | `s22-reboot recovery` (boot_id 4d4db876… → f7fe0035…) | back on SSH after ~30 s | — |
| 24 | After boot, no manual steps | ABOX ONLINE/suspended; sectiongraph staged; keepalive (new), touchd, OpenUnum running; **hci0 up** via keepalive (1st attempt holder exit rc 1, retry OK, pid 2501) | post-reboot-state.txt |
| 25 | Post-boot default loopback (level 2 tone) | rc 0/0, volume restored 0; tone 1000 Hz ~480–710× noise for 1.5 s | post-reboot-loopback.txt |
| 26 | `s22-ui tts --play` (touchd → s22-say, graph), English Piper (lessac), level 2, mic recording | played rc 0; speech energy 0.0015–0.0034 for ~5.5 s, **but ASR failed** (Parakeet empty, whisper "foreign"). The render itself transcribes exactly. | speech-loopback-plugin-path.txt, speech-asr.txt |
| 27 | Greek el-antonis, same path | played rc 0; whisper large-v3-turbo (rig, el): **"Καλησπέρα, το τηλέφωνο μιλάει τώρα ελληνικά με τη φωνή του Αντώνη."** (exact) | same |
| 28 | English repeat | played rc 0; whisper (rig): **"The phone can talk now. This sentence was spoken by the Piper voice on the Galaxy S22."** (exact); on-phone Parakeet: "…the piper voice on the Galaxy S twenty two." | same |
| 29 | OpenUnum plugin registry; keepalive log | tts-s22 0.2.0 running; no mute event during any playback (one mute after a playback, on my test preset, as intended) | post-reboot-keepalive-openunum.txt |

Not verified: an OpenUnum agent turn that calls `text_to_speech` (tool path
tested via s22-ui/touchd plus plugin unit tests), level-4 playback
acoustically (tests capped at level 2 by the owner's rule), and the
earpiece/top/bottom routes over the graph. The failed first English take
(#26) is unexplained; the repeat passed.

## Regression fix (same evening): graph playback "Unable to install hw params"

**Symptom (coordinator, ~18:52+):** every s22-say graph playback failed
hw_params and fell back to RDMA6.

**Root cause (my bug):** s22-say played the graph through the `s22graph`
*plug* PCM. When the WAV is not already 48 kHz stereo S16 (all TTS renders are
22.05/24/44.1 kHz), alsa-lib's plug/rate plugins open the slave `hw:0,102`
with **MMAP access**. In `abox_vdma_mmap()` the ION-backed calliope buffer
is mapped with `dma_buf_mmap()`, which replaces the VMA's `vm_ops`. The ALSA
core had installed `snd_pcm_vm_ops_data` (its close callback decrements
`substream->mmap_count`) and incremented `mmap_count` after the driver's
mmap. munmap therefore never decrements it, and `mmap_count` leaks
permanently. `snd_pcm_hw_params()` then returns **-EBADFD** for every later
open of that substream (state OPEN with `mmap_count != 0`).
- Proof: speaker-test on hw:0,102 → "File descriptor in bad state";
  hw:0,100 (never mmapped) → OK. Nothing holds the device; the substream is
  closed; the chroot device nodes are correct (197/197).
- First trigger: the first Piper (22.05 kHz) playback in test #26 at ~18:56.
  That is also the take whose recording was unrecognizable.
- Tests #27/#28 reported played=true **only through the silent RDMA6
  fallback**. My earlier verification did not check which path played.

**Fix:**
- s22-say converts the WAV offline to 48 kHz stereo S16 raw. It uses the new
  `s22conv:FILE` PCM (plug → file → null; about 20 ms per 5 s of audio).
- It then writes the raw stream to `hw:0,100` (calliope_0 → VPCMOUT_DAI0 →
  bdmixer → RDMA3, the stock primary playback) with plain RW access, so
  there is never an mmap on a calliope device.
- `s22graph` is removed from alsa-s22.conf. `S22_GRAPH_PCM` selects the frontend.
- `/run/s22-say.last` records `graph|direct|direct-fallback`.
- s22-rec was already safe (arecord directly on hw:0,110, RW).

**Not fixed:** the kernel bug itself, which remains for any client that
mmaps a calliope device. **pcm102 stays leaked until the next reboot.** I did
not reboot, to leave the UI worker's Hyprland/touchd/fastui session
undisturbed; s22-say no longer uses pcm102.

| # | Test | Result | File |
|---|------|--------|------|
| 30 | 6 consecutive silent s22-say runs, alternating the 48k tone and the 22.05k Piper render | 6/6 `graph pcm=100 rc=0`; pcm100 hw_params OK after each | fix-mmap-repeat-silent.txt |
| 31 | 3 consecutive tone loopbacks (level 2, concurrent s22-rec on the graph) | 3/3 graph; tone 1000 Hz for the full 1.5 s (run 1 ratio 70–300 with a raised room noise floor, runs 2–3 ~620–870×) | fix-mmap-tone-loopback-x3.txt |
| 32 | touchd `tts --play`, English Piper + Greek el-antonis, level 2, recorded | both `graph pcm=100 rc=0`. Whisper: EN "The phone can talk now. The sentence was spoken by the Piper voice on the Galaxy S22." (1 word off); EL exact. Phone Parakeet EN weaker | fix-mmap-speech.txt |
| 33 | Coordinator repro `S22_SAY_LEVEL=0 s22-say "ok"` ×5 | 5/5 graph, no retry or fallback; pcm100 OK, pcm102 still EBADFD (leak until reboot); ABOX ONLINE; volume 0 | fix-mmap-repro-x5.txt |

## After the UI worker's reboot (23:14 local): audio, Bluetooth, disk, Talk button

**Audio** (reboot2-pcm102-and-crash-recovery.txt, reboot2-speech.txt)
- hw:0,102 healed by the reboot (speaker-test OK; `S22_GRAPH_PCM=102 s22-say` →
  `graph pcm=102 rc=0`), and it was still OK after the crash tests (the crash
  helper writes 48k stereo straight to hw:0,102: no plug, no mmap).
- Forced Calliope fault on the NEW code: s22-say → "failsafe reset" →
  `graph pcm=100 rc=0`, ONLINE; s22-rec → reset → rc 0, ONLINE.
- touchd TTS EN/EL, both `last=graph pcm=100`. Whisper: "…spoken by the Piper
  voice of the Galaxy S22." (1 word off); "…με τη φωνή του Ατώνη." (1 letter off).

**Bluetooth did not come back.** Root cause: the holder's shared-WLAN-rail
gate (`votes_match`) required exactly two `qcom,cnss-qca6490-vreg_wlan`
consumer lines in regulator_summary. On this boot CNSS held the rail through
one (`vreg_wlan 1`, `bt 0`, `cnss 1`), so every attempt failed the gate with
-EBUSY. That was printed only as `baud_probe_result=-5`, because rc starts at
-EIO. All other gates were true (wlan module and wlan0, CNSS
`0x420107 … COLD_BOOT_CAL_DONE`, ecm0 carrier 1, no hci). Keepalive backed
off and would have stopped after 5 failures per 30 min.

Fix:
- Patch the deployed static holder (2 bytes: `ccmp w0,#2` →
  `ccmp w0,w0`, vaddr 0x401920; sha 66c7863c… → 3ec69d2f…; original kept as
  `.orig-66c7863c`; tool `bt/patch-hci-hold-cnss-lines.py`) so that only the
  line-COUNT term is dropped. The rail-use, BT-vote and CNSS-vote-sum checks
  are unchanged. The source has the same change, but it cannot be rebuilt:
  the NVM payload header is gone.
- `s22-bt up` waits up to 45 s for the prerequisites.
- keepalive has per-service `max_restarts`/`window_s`/`backoff_max_s`;
  bluetooth = 1000 / 300 s.

Result: a manual `s22-bt up` gave hci0 in 4 s, with Wi-Fi unaffected (gateway
ping OK; votes rail 2 / bt 1 / cnss 1). After another `s22-reboot recovery`
(BT up at the time), hci0 came up by itself about 60 s after boot. The first
try reached the chip but timed out (`-110`) and powered off cleanly; the
retry 24 s later succeeded (reboot3-state.txt). **The ECM gate is still in
place: with the USB cable to the rig unplugged (ecm0 carrier 0), Bluetooth
will not start.** That is the rescue-path safety rule from the original
review; changing it is the owner's decision.

**Disk** (native-root-reclaim.txt): the root overlay's upper dir is on the
583 MB CACHE partition. Nothing was growing quickly (about 1 MB in 4 days,
`/var/log` 32 KB). It was full because of installed Mesa/LLVM: libLLVM.so.22.1
alone is 172 MB. `native-root-reclaim.sh` copies each item to
`/srv/s22/archive/native-root-20261021/`, verifies its sha256 (MANIFEST,
19/19 re-verified), then:
- symlinks libLLVM, libgallium and libvulkan_radeon (no running process mapped
  them; dlopen of libgallium → LLVM via the symlink works; they survived the
  reboot);
- removes the moved unumsearch v0.1.{2,5,6} .bak files, /var/cache/apk,
  /usr/lib/debug and the mesa shader cache.

**/ went from 96 % (20.8 MB free) to 55 % (262.7 MB free).** Guard:
s22-keepalive `root_guard` checks every loop. Below 40 MB free it caps logs on
/ (/var/log, /root/*.log) at 1 MiB and empties regenerable caches (mesa shader
cache, apk cache, pip cache), logging `root_low` with before/after.

**Talk button (unum-fastshell)** (talk-button-test.txt)
- An injected tap on Talk (`fs-inject.py` takes NORMALISED coordinates:
  0.1759, 0.6107) started s22-assistant. rec-ready came after 200 ms.
- A pre-rendered Piper question was played into the open mic window.
  Parakeet heard "It is the capital of France." The agent replied "Paris is
  the capital of France."
- Filler "Just a second…" at 1.22 s, then the answer.
- All five s22-say plays of the run (cue, question, earcon, filler, answer)
  were `graph pcm=100 rc=0`.
- Whisper on a mic recording that covered the answer: "Paris is the capital
  of France." (quiet: level 2 via `/run/s22-say.level`). The filler played
  before that recording started.
- My first attempts failed only because I passed pixel coordinates.

s22-say additions: `/run/s22-say.level` (runtime speech-level override) and
`/run/s22-say.history` (last 50 paths). The first history version never
renamed its temp file, because `tail` failing on the missing file made the
group's status non-zero; this is fixed and verified.
