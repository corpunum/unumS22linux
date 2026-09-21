# Driver-loop continuation — 2026-09-22

These are further measurements on the same BORE762 recovery session, not
completion of the phone port. No additional reboot or partition write.

## GPU and Arch control access

The Samsung Vulkan shader ran again after the recovery update: checksum
99712 on Xclipse 920, 2.389 seconds, no new GPU faults, same boot and healthy
resident CPU4B. This is a numerical shader test, not another model benchmark
or accelerated Hyprland acceptance.

Only the verified ALSA control node was bound into Arch. Its installed
libasound enumerated 1736 controls in 0.756 seconds. The syscall trace proves
O_RDONLY opens and control enumeration, with no mixer writes or PCM opens in
that test. The first launch lacked the compatibility wrapper's required `--`
separator and exited before Python; the corrected invocation is recorded
separately, not substituted into that failed receipt.

The optional persistent desktop hook now validates the exact card/control
sysfs identities, requests only the controlC0 udev event, and binds only that
control node. Failure remains nonfatal to desktop startup. The previous
supervisor is backed up. The installed helper passed its live preparation
test in 0.426 seconds; the new hook has **not been reboot-tested**.

## First actual playback-endpoint diagnostic

The one-second digital-zero candidate opened native hw:0,2 (RDMA2), with
both amplifier-enable switches off and no mixer/routing changes. HW_PARAMS
and SW_PARAMS succeeded, but PREPARE returned EINVAL: the kernel explicitly
reported `no backend DAIs enabled for RDMA2`. No START/WRITEI_FRAMES occurred,
so **zero audio frames were submitted**. The trial exited in 0.443 seconds;
the PCM closed, both amp switches remained off, ABOX reset_count stayed zero,
the model stayed healthy and the boot was unchanged. This isolates the next
audio issue to the DPCM backend route; it does not prove acoustic playback.

The next reversible test selected SIFS0 only in `ABOX SPUS OUT2` and
`ABOX UAIF1 SPK`. PREPARE then passed, followed by successful drop, hw_free,
close and restoration of both selectors to RESERVED. No frames were sent in
that prepare-only test (7.842 seconds including setup and cleanup).

One zero-stream test through that same route subsequently **stalled**.
Eight WRITEI_FRAMES ioctls succeeded, followed by repeated EAGAIN until the
10-second child deadline. The controller terminated the child and restored
both selectors; total trial time was 15.363 seconds. Aplay returned zero even
while reporting `Aborted by signal Terminated...`, so exit status alone is
explicitly rejected as acceptance. The trace proves RDMA START/STOP and
codec/clock configuration, not sustained DMA progress or audible sound.
Both amplifier-enable switches remained off, the PCM closed, ABOX reset_count
remained zero, and the same boot/model remained healthy. No capture occurred.

The runner now records its deadline explicitly and rejects signal-aborted
zero exits. Eight hardware-free regression tests cover this classification.
The original stalled receipt is retained unchanged; its derived assessment
uses the recorded abort message and SIGTERM trace. The next useful test must
add pointer/period instrumentation, not repeat the same blind stream.

## Bluetooth binary identity

Private-HAL inspection distinguished ASCII GetAppVer (sub-op06) from binary
PatchVerReq (sub-op19). A separate guarded probe sent the latter only after
checking the former. It captured the 21-byte successful reply in 3.016 seconds.
The event layout yields product0013, ROM0201, SoC400c0210 and packed map key
400c021000130201, matching the recovered hpbtfw21.tlv/hpnv21.bab entry.
All UART/shared-WLAN vote checks passed afterward; no firmware/NVM bytes,
baud transition or HCI attachment were attempted. The premature first-segment
prototype remains rejected/unexecuted, not an accepted activation tool.

## NPU/build work

The exact historical Android Clang21 r563880c toolchain completed a full
Image/modules build (329 modules), including patched fs/file.o and the NPU
aggregate with captured ThinLTO, CFI,
MODVERSIONS and shadow-call-stack settings intact. The only olddefconfig
differences are generated CC_CAN_LINK probes and the disabled UAPI header
test. The earlier LTO_NONE result was caused by missing LLVM=1/LLVM_IAS=1
make arguments, not missing compiler support. All 324 modules common with
the original captured module tree have matching `__versions` hashes. The
`-dirty` release suffix alone is not a load rejection: this pinned kernel's
MODVERSIONS comparison skips that prefix. These checks do not prove boot
compatibility. No new kernel/module is loaded, and KEXEC is unavailable.

Built Image SHA-256:
`634f14fefb34e30579e3f8891709581315c05d17b719e47f3ec93f0bb9b42ac2`.

A real Pi-to-rig-Qwen source review completed in 158.19 seconds with one
write-only tool call. It corroborates the unsafe bare error/timeout proposal:
the caller and queued callback lifetime require coordinated ownership repair.
Its suggestions remain unaccepted until independently implemented/tested;
the driver objects compiling does not prove firmware boot or inference.

Curated measurements: [acceptance.json](../evidence/main-driver-loop-20260922/acceptance.json).
Raw logs, traces, vendor assets and model files remain private.

## Subsequent Bluetooth and audio measurements

The full 195,848-byte Bluetooth RAM patch transferred in 806 packets, with
final status0/subop1e acknowledgement (5.943 seconds). Its mode3 header and
pinned QCA driver explain why the preceding 243-byte prefix test received no
intermediate event. That timeout did not establish a rejected segment.

A subsequent transfer verified the ACK before sending the HAL's GetBoardIdReq,
without a reset or extra baud change. That query succeeded; overall4.299s.
The 3 Mbaud transport was separately verified by exact controller identity
before and after switching. Its baud reply ends in01, so conventional HCI
zero-status success is not claimed. Each test released its Bluetooth power
vote and preserved WLAN votes, USB, boot and the resident model. NVM board
configuration, HCI registration and pairing remain unfinished.

The instrumented audio zero-stream collected eight narrow status snapshots,
seven while ALSA claimed RUNNING. Actual RDMA2 status registers0x1230/0x1238
and ALSA hw_ptr stayed zero (appl_ptr8192), with PMactive/cacheN/service1.
Single-record reads avoided a full register dump. The10s child deadline
terminated the stalled stream; both selectors were restored, PCM closed,
amps stayed off and ABOX reset_count remained zero. This distinguishes
hardware nonprogress from merely missing ALSA pointer messages.

A suspended-only offset1 read captured the existing firmware DRAM log without
requesting a flush or SFR/SRAM dump. Only its hash/metadata are exported. The
log shows RDMA2 open, prepare, start and source assignment but no demonstrated
DMA progress. CP magic warnings are not established as the media failure's
cause. Actual Pi/rig-Qwen source review completed in149.85s; its findings were
independently checked, and an incorrect AMP-enable interpretation rejected.

See [curated continuation receipts](../evidence/main-driver-loop-20260922/continuation.json)
and [manifest](../evidence/main-driver-loop-20260922/continuation-manifest.json).
Neither raw logs nor vendor payloads are public. No further reboot or partition
write occurred during these tests.
