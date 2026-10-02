# Host audio recovery packaging attempt — 2026-10-03

## Result

The one coordinator-authorized host packaging invocation **failed before
publication**. The pinned `mkbootimg.py` produced a temporary image of
101,259,264 bytes. Pinned `avbtool add_hash_footer` rejected it because the
maximum pre-footer image size for the 100,663,296-byte RECOVERY partition is
100,593,664 bytes. The command exited 2 after 33 seconds.

The requested output directory was absent before the run and remains absent;
no manifest or final image exists. The packager's temporary directory was
removed by normal exception cleanup. There was no retry, resize, input change,
or second packaging attempt. Private stdout/stderr remain on the host; only
their digests are recorded in the sanitized evidence JSON.

## Exact source, command, and validated inputs

- Worktree HEAD: `e1117db571875e8403e57ea33770707eef2d14bd`, tree
  `147e9a2e7aa6d98b920a3458f9dcf2151b257928`, clean before invocation.
- Builder SHA-256:
  `2824a0adc2198262e90d549036d1d72a5aef42b6f833672401d3e79b66d95ee2`.
- Exact command:

  ```text
  /usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C TMPDIR=/tmp /usr/bin/python3 -I -S -B /home/corpunum/s22-workers/audio-coherent-package-build-20261003/tools/hardware/build-audio-coherent-recovery.py --out-dir /home/corpunum/s22-linux/builds/audio-coherent-recovery-host-20261003
  ```

- Start/end UTC: `2026-10-02T22:52:51Z` / `2026-10-02T22:53:24Z`.
- Exit: `2`. The only stderr diagnostic was the AVB partition-size rejection;
  stdout was empty.
- Host preflight just before execution: output path absent; MemAvailable
  25,160,848 KiB; builds filesystem free 30,796,918,784 bytes.
- Interpreter: CPython 3.12.3, isolated, no site, optimization 0; executable
  SHA-256 `e50d468e8b0adfb05733f5b87b3cff34829c4a8c1aea50c865aa8bdfe4bb150f`.
- The builder loaded and hash-checked all four pinned project helpers. Input
  hashes, tool hashes, and sizes are recorded in
  `evidence/s22-audio-coherent-package-build-20261003.json`.

The base B104 image hash and its pinned input artifacts matched. The builder
completed base-image/header checks, exact three-module in-memory CPIO
replacement checks, and the pre-mkbootimg static ABI pass before reaching
footer addition. Independently reviewed identical pinned inputs had passed
the full 324-module/16,569-import scan and the external WLAN's 495-import
scan. The final repacked-image header/payload/CPIO round-trip checks and
candidate footer verification were not reached; there is no final image hash
or package manifest hash to report.

## Scope and limits

This is a failed host artifact attempt, not an audio driver/runtime result.
No module was loaded; no phone, SSH, ADB, firmware, deployment, flash, boot,
reboot, or NPU bundle operation occurred. Static MODVERSIONS checks do not
prove loader behavior, current-phone membership, runtime PM/IPC/DMA/PCM
progress, physical audio, Samsung authentication, or bootability.

The precise next technical issue is image sizing: the actual pre-footer
candidate exceeds the partition's AVB maximum by 665,600 bytes. This receipt
does not authorize changing the mkbootimg inputs, partition, AVB settings, or
packager. Any correction and another packaging invocation require separate
review and explicit coordinator authorization. The failed attempt's private
logs and no-output state are preserved; no partial package was published.
