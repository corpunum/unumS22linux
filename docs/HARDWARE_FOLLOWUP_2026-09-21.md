# Hardware follow-up — 2026-09-21

This checkpoint advances native hardware access; it does **not** finish all
drivers. No reboot, partition write, modem activation, NPU firmware boot, or
Bluetooth firmware download occurred. The existing guardian/Omarchy/CPU4B
session and independent USB recovery connection were preserved.

## New device evidence

| Area | Measured result | Remaining acceptance |
| --- | --- | --- |
| Bluetooth | Native H4 UART exchange identified QCA6490, ROM HSP2.1, SoCID `0x400C0210`; power/regulator state restored | Correct QTI patch/NVM/baud initialization, HCI, pairing, peripherals and audio |
| Magnetometer | Two runs, 24 changing vectors each, increasing timestamps, no partial frames or injection | Accuracy is zero; calibrated compass, orientation and desktop integration unaccepted |
| Light sensor | Two runs, 16 packed frames each, increasing timestamps, no injection | Lux stayed1; physical light response and calibration untested |
| Cellular libraries | Recovered missing system dependencies; real `libsec-ril.so` loads and resolves `RIL_Init` on the phone | CP boot, protected NV handling, Samsung service contract, SIM/data/calls/SMS |
| Wi-Fi | All13 post-experiment checks passed, including DNS and TLS-verified HTTPS forced through wlan0 | Cable-free, roaming and suspend/resume checks remain |

Public receipts are in
[`evidence/hardware-followup-20260921`](../evidence/hardware-followup-20260921/).
Raw traces, firmware, vendor libraries, device identities and timestamped
sensor frames remain private. The exporter checks the actual Bluetooth H4
event length/opcode/status; the raw C transport intentionally treats payloads
as opaque rather than pretending it initializes HCI.

The isolated cellular trial ran in1.109s, exit0, with UID1000, capabilities0,
NoNewPrivs, private mount/network namespaces, and no modem/binder/EFS devices
or proc mount. Library constructors executed. No RIL function was called;
the process used `_exit` without destructors. The post-exec trace contains no
ioctl or clone and one unsuccessful Android logging-socket connection.
This is real library loading, **not** an operational modem.

## Unresolved hardware and concrete gates

- **NPU:** source audit found an unbounded power-notify wait and unsafe error
  cleanup/refcount ownership. No speculative kernel patch is retained. The
  host ABI verifier refuses execution. A source-tested kernel fix and a
  recoverable boot test are needed before NPU boot/inference. Ordinary
  NPU-only BOOTUP uses recovered `AIE.bin`; missing firmware-test
  `vectors.bin` is not the blocker.
- **Audio:** ABOX firmware already runs, but only debug PCMs exist. Rainbow
  card registration exhausted its early retries. Firmware must be available
  before that startup attempt; live unbind/reprobe lacks safe teardown and
  was not attempted. No speaker/microphone acceptance.
- **Bluetooth:** `hpbtfw21.tlv` matches the observed generation, but exact
  `hpnv21.bab` versus `hpnv21g.bab` selection and the QTI initialization
  sequence still require implementation. No guessed firmware was sent.
- **Cellular:** Samsung `cbd` has NV create/write/fsync/remove paths. The
  project's protected-EFS boundary has not changed; no CP startup was run.
- **Camera, GPS, suspend and physical touch:** no new acceptance this round.
  Remote software checks do not substitute for a physical stimulus or
  end-to-end capture test.
- **GPU:** prior Samsung OpenCL/Vulkan compute and0.8B model offload remain
  the accepted baseline, not new measurements here. Resident4B is CPU;
  accelerated Hyprland, sustained GPU stability and4B offload remain open.

The previous stuck Arch process-creation task is still unresolved. No
reboot was attempted to conceal or clear it. Sensor sampling and BT power
were temporary; no new sensor/BT/modem service was enabled at startup.

Detailed audits:
[Bluetooth/audio](research/BT_AUDIO_NEXT_GATES_2026-09-21.md),
[QCA firmware sequence](research/BT_QCA6490_FIRMWARE_GATE_2026-09-21.md),
[cellular closure](research/CELLULAR_CLOSURE_FOLLOWUP_2026-09-21.md),
[NPU boot gate](research/NPU_BOOTUP_GATE_2026-09-21.md).

## Validation

Nine sensor decoder tests, Bluetooth parser/constants fixtures, and NPU ABI
and execution-refusal checks pass on the host. Source/scripts pass syntax
checks and `git diff --check`. These checks do not prove missing physical
data paths. Live receipts preserve exact source/binary hashes and same-boot
health checks; post-trial CPU4B API remained healthy.
