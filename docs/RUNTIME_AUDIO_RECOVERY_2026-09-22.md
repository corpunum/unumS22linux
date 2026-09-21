# Runtime repair and real audio card — 2026-09-22

The supervised Luna/Pi/Qwen loop produced two measured advances: a working
process-spawn workaround for Pi and a real Rainbow-Prince audio card after
an audio-only recovery update. This is not completion of all phone drivers.

## Arch process creation: exact cause and scoped workaround

The pinned Samsung/Lineage kernel's `fs/file.c::__range_close()` omits the
fdtable-limit clamp present in [upstream Linux v5.10](https://github.com/torvalds/linux/blob/v5.10/fs/file.c).
With an unsigned fd and a `UINT_MAX` endpoint, increment wraps and the loop
cannot finish. Absent descriptors also skip its `cond_resched()` call.
The hung captured-output Python child was observed in `__arm64_sys_close_range`.

The initial clone3-only ENOSYS experiment did **not** fix captured-output
subprocess creation; it left one additional stuck task. No further unfiltered
repeat was attempted. A targeted close_range-only ENOSYS process filter did
fix the same test. Trace evidence showed clone3 succeed, close_range return
ENOSYS, and ordinary descriptor closes complete. Ten repetitions and an
isolated Arch tmux create/query/cleanup also passed without new stuck tasks.

The static wrapper now prefixes the existing canonical `pi` and
`pi-research` launchers. It inherits to their tools, retains UID1000,
zero capabilities and NoNewPrivs, and changes no model/provider selection.
It is not a sandbox or a global kernel repair. Original launchers and agent
instructions are backed up on userdata. The local4B actually invoked one
bash tool, which verified the filter and captured `PI_SPAWN_OK` from a child:
72.43s before reboot and 64.39s after reboot. These are whole agent-turn
timings, not subprocess latency or tokens/s.

A separate kernel clamp patch compiles as `fs/file.o`; it is **not installed**.
Build/compiler/config compatibility remains a separate gate. Qwen reviews
are proposals only: parent inspection rejected incorrect suggestions and
used source plus live traces as the deciding evidence.

## Audio startup repair and actual recovery reboot

The recovery candidate adds only four exact recovered ABOX firmware/topology
files under the early ramdisk's `vendor/firmware`. All939 original ramdisk
records, kernel, DTB and recovery-DTBO match native V3. The header and AVB
descriptor were independently verified. Exact RECOVERY identity, full old
hash, on-device rollback backup, staged candidate and full write readback
were checked before one software `s22-reboot recovery` request.

Only RECOVERY was written. No BOOT/MISC/EFS/identity/PIT/bootloader/TrustZone
write was performed. The image SHA256 is
`1c1b77a5e532e50b8274cfc68921aa9b1bfe6d4ae9a3459281be0cc033c5c3d5`.
Native V3 and known-good Lineage recovery remain available for rollback.

BORE762 records the actual RECOVERY selection. USB SSH returned, automatic
guardian ACK worked, and the acceptance sample reached95.02s uninterrupted
uptime with persistent4B/desktop ready and no pending-SIGKILL tasks. Both
old stuck tasks were cleared by this reboot. No physical intervention.

Post-boot observation found:

- `Rainbow-Prince` card0 with23 playback and53 capture PCM entries;
- `abox`, `abox-core`, `abox-tplg` and `rainbow-sound` drivers bound;
- exact early firmware hashes and Calliope version6XH0;
- no devices left in the deferred-probe list;
- all13 Wi-Fi checks passed, including forced-WLAN DNS and verified HTTPS;
- Pi web backend started automatically and tailnet HTTP returned200.

The first tailnet HTTP connection timed out; following a successful Tailscale
peer ping, IPv4 and ordinary-hostname HTTP checks passed. This transient is
recorded, not counted as uninterrupted network acceptance.

Startup's early device scan missed the later ALSA registration. After
verifying sysfs/uevent major116/minor114, parent created only `controlC0`
as0660 root:audio. The existing root-owned `/dev/snd` directory was narrowly
hardened from0777 to0755; existing timer permissions were unchanged. Native
amixer then enumerated1736 controls with no output truncation. This node
repair is session-only pending reviewed startup integration; the early
firmware itself is persistent in RECOVERY. No PCM device was opened.

Bluetooth's bounded version-then-board query also passed in0.905s and
returned an11-byte successful FC00/subop23 response (payload02 00 00).
WLAN power votes and UART state were restored; the model remained healthy.
No firmware/NVM was transmitted and no HCI or pairing success is claimed.

No mixer route was enabled and no PCM was played or recorded in this
checkpoint. Physical speaker/microphone operation remains unverified.
Bluetooth firmware/HCI, NPU inference, cellular, camera, GPS, suspend and
physical touch acceptance remain separate work. The GPU's earlier Samsung
Vulkan/OpenCL and0.8B offload results were not re-benchmarked here.

Selected metadata: [acceptance.json](../evidence/main-driver-loop-20260922/acceptance.json).
Raw logs, device identities, models and vendor assets remain private.
