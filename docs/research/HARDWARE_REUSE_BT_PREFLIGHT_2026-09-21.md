# Bluetooth native HCI preflight — 2026-09-21

This is a read-only preflight; no HCI node was opened and no attach, module,
rfkill, service, firmware, reboot, or network action was performed.

## Finding

The kernel does contain the required generic transport pieces: live
`/proc/config.gz` reports `CONFIG_BT_QCA=y`, `CONFIG_BT_HCIUART=y`,
`CONFIG_BT_HCIUART_SERDEV=y`, `CONFIG_BT_HCIUART_H4=y`,
`CONFIG_BT_HCIUART_QCA=y`, and `CONFIG_SERIAL_DEV_BUS=y`. Thus “HCIUART is
missing” is rejected.

The live `bt_qca6490` platform device is bound to `bt_power`, and `rfkill0` is
Bluetooth, but `/sys/bus/serial/devices` and `/sys/class/bluetooth/hci*` are
empty. `ttySAC1` is the Exynos UART node (major 204/minor 65), not an HCI
device. The DT declares `compatible = "qcom,qca6490"` with Samsung GPIO
power/reset/wake wiring.

The pinned kernel source is decisive: `btpower.c` has a `qcom,qca6490` power
match, while `hci_qca.c`'s serdev table has no `qcom,qca6490-bt` entry (it has
`qcom,qca6390-bt`, WCN399x and older entries). That rules out automatic serdev
binding, but not the independent hci_uart line-discipline ioctl path. In that
non-serdev path `qca_soc_type()` defaults to `QCA_ROME`, `qca_setup()` reads a
controller version, and `btqca.c` requests `qca/rampatch_%08x.bin` and
`qca/nvm_%08x.bin`; it does not select the recovered `htbtfw20.tlv`/`htnv20.bin`
names. This explains power/rfkill without an HCI controller and leaves the
line-discipline firmware/SoC mapping unresolved.

## Userspace/firmware boundary

Alpine has `btattach` from `bluez-5.86-r2`; read-only `strings -n 2` shows the
`qca` protocol, consistent with the BlueZ protocol list
([btattach protocol documentation](https://github.com/bluez/bluez/wiki/btattach)).
`bluetoothd` exists only at `/usr/lib/bluetooth/bluetoothd` and was not
started. A candidate syntax is `btattach -B /dev/ttySAC1 -P qca`, but it is
not an activation instruction: the exact SoC response, baud/flow-control,
power sequencing, and firmware-name mapping are unproven. The exact Samsung
QCA firmware set (`hpbtfw*`, `hpnv*`, `htbtfw20.tlv`, `htnv20.bin`, NVM XML)
is recovered from the owner’s read-only vendor image and hash-recorded under
`rootfs/bt-audio-vendor-assets/`; it is not present in the active native phone
root and was not copied.

The Android source manifest supplies the matching QTI service/implementation
and init rc, which are absent from native Alpine:
[r0s proprietary files](https://raw.githubusercontent.com/LineageOS/android_device_samsung_r0s/lineage-23.2/proprietary-files.txt).

## Decision

No one-shot `btattach`/`hciattach` command is safe to run yet. Although the
BlueZ QCA protocol exists and the line-discipline path does not require a
serdev DT match, the exact QCA6490 SoC response, firmware path/name mapping,
baud/flow-control, and bt_power sequencing remain unproven; activation could
strand or disturb the controller. The safe next implementation is either the
matching Android QTI Bluetooth service/driver closure or a separately reviewed
native qca6490 HCI/firmware integration, followed by a parent-approved bounded
activation with USB rescue.

Exact commands and live results are preserved in
`rootfs/hardware-reuse-20260921/bluetooth-hci-preflight-commands.sh` and
`bluetooth-hci-preflight-results.txt`.

## Exact QTI power/UART audit (host-only, no execution)

The matching vendor HAL was recovered read-only to `/tmp` from the pinned
vendor image. `android.hardware.bluetooth@1.0-impl-qti.so` is AArch64,
575,144 bytes, SHA-256
`153f3ca839d52dc1be6fe48724a97f97b6bd24020cb5fb4b2be33cc195e0584f`.
The matching init rc is `/tmp/android.hardware.bluetooth@1.0-service-qti.rc`;
the service runs as `bluetooth`, with `NET_ADMIN` and `BLOCK_SUSPEND`.

Static disassembly establishes the following exact order for the normal QTI
UART controller path:

1. `UartController::Init` (`0x42058`) constructs `PowerManager`, then calls
   `SetPower(false, false)` (`0x42214`) followed by `SetPower(true, false)`
   (`0x42224`).
2. `setVendorPropertiesDefault` (`0x3c6d4`) queries `/dev/btpower` with
   `BT_CMD_GET_CHIPSET_ID` (`0xbfaf`); a `qca6490` response selects the
   internal `hastings` type. `GetSocTypeInt` maps `hastings` to type `4`
   (`0x3e7ec`, `0x3e888`).
3. For type 4, `PowerManager::SetPower` takes the direct power path
   (`0x60e48` mask test) and `PowerUpChip` opens `/dev/btpower` with
   `O_RDWR|O_CLOEXEC` (`0x61160`) and issues `ioctl(fd, 0xbfad, 0/1)`
   (`0x61220`). The kernel ABI is confirmed by
   `include/linux/btpower.h`: `BT_CMD_PWR_CTRL=0xbfad`; `btpower.c` applies
   the requested mode only when its internal `pwr_state` differs.
4. The recovered `.data` byte `soc_need_reload_patch` is initialized to `1`
   (symbol `0x8a6a8`; GOT relocation `0x87f08`). Consequently the normal
   type-4 path starts HCI transport with the default UART baud enum `0`
   (115200), while retaining format `0x8209` (hardware flow control). The
   Hastings-specific alternate enum `0x11` is only written when the
   `Init(..., bool)` argument is false (`HciUartTransport::Init`, `0x603c0`);
   the normal caller passes the recovered global byte at `0x42350`.
5. `PatchDLManager::SocInit` sends the QTI Get-App-Version command before
   changing baud: `GetAppVerCmd` at `0x4c89c` constructs the complete
   five-byte H4 command `[01 00 fc 01 06]` (opcode `0xfc00`, one-byte
   payload `0x06`) and sends it at `0x4c9e0`; only after that does `SocInit`
   call `SetBaudRateReq` at
   `0x4b648`. `SetBaudRateReq` obtains the max-baud enum, sends the vendor
   baud request, calls `SetBaudRate`, waits 20 ms, and restores flow control
   (`0x507d0`–`0x50a2c`). This proves that a version exchange is initially
   attempted at 115200, but it is not a standalone-safe exchange because
   the preceding power/transport setup and following patch/NVM flow are part
   of the QTI state machine.

The same HAL contains `PatchDLManager::HciSendVsCmd` at `0x4c4f8`, so its
response path is not an absent symbol. It requires `UartWrite` to return the
requested five bytes, then reads H4 events through `ReadNewHciEvent` and the
vendor-event parser. `GetAppVerCmd` additionally accepts only a returned
length of `5` and response-buffer byte `6 == 0x10` (`0x4c9f4`–`0x4ca04`).
The helper's accepted-event branch depends on mutable PatchDLManager flags
and includes recovery/vendor-event handling; the disassembly does not expose
a single standalone response fixture equivalent to a public HCI contract.

The matching teardown is also exact: `UartController::Disconnect` (`0x44bf8`)
stops the RX watcher and calls `HciTransportCleanup`; with
`soc_need_reload_patch=1`, it then calls `SetPower(false, false)` and
`PowerManager::Cleanup` (`0x44c28`–`0x44c40`). UART teardown first performs
`TIOCMGET`/`TIOCMSET`, flushes `TCIOFLUSH`, and runs `CleanUp`; transport
deinitialization issues `ioctl(fd, 0x54ee)` before closing (`0x5f7d8`–`0x5f890`,
`0x60864`–`0x60900`). The private `0x54ee` return is ignored immediately
before `close` (`0x5f83c`–`0x5f848`), so `ENOTTY` there is not itself a
version-only blocker. In contrast, private `0x54ec` is issued by transport
operation 3; `SetBaudRateReq` checks its negative return (`0x50954`–`0x50968`),
and `CheckForUartFailureCode` also interprets it (`0x60c68`–`0x60cfc`). Its
driver ABI remains unidentified in the pinned kernel source.

This resolves the initial baud and power ABI, but not a source-complete
minimal QTI state machine: the HAL also requires Android property/log/HIDL
dependencies, its controller-version response gates later patch/NVM
operations, and the transport has recovery timers and controller reset paths.
No phone UART, `/dev/btpower`, ioctl, rfkill, firmware, module, service, or
vendor ELF was opened, executed, or modified during this audit.

## Shared-rail audit and bounded raw transport artifact

The exact r0s DT is not hypothetical about power sharing. In
`arch/arm64/boot/dts/samsung/r0s/r0s_eur_openx_w01_r27.dts`, the
`vreg_wlan` fixed regulator is the rail used by the `qcom,cnss-qca6490`
WLAN node (`wlan,regulator_name = "vreg_wlan"`) and by the `bt_qca6490`
node. The same DT has separate WLAN and BT enable GPIOs:
`wlan-en-gpio = <... 0x12 ...>` and `qcom,bt-en-gpio = <... 0x11 ...>`;
the BT node also carries three positional `gpios` entries for BT enable,
BT wake, and host wake. The pinned `cnss2/main.c` independently calls
`regulator_get(..., "vreg_wlan")`, while the active Samsung `btpower.c`
path calls `regulator_get(..., "vreg_wlan")` and then
`regulator_enable/disable`. Therefore BT power is not proven independent of
WLAN: regulator reference counting may preserve a live WLAN consumer, but
the probe must treat the shared rail and GPIO timing as a parent-reviewed
risk. The kernel source also documents a QCA6490 minimum 100 ms separation
between BT_EN off and WLAN_EN on in `cnss2/power.c`; this is an additional
reason not to run an ad-hoc power cycle while WLAN is active.

The host-only artifact
`tools/hardware/bt-version-transport-probe.c` is intentionally narrower
than the QTI state machine. It has no default device path: `--self-test` is
the only default-safe operation, and device access requires
`--execute`, `--allow-shared-wlan-rail`, and an explicit `--btpower-rdev`
copied from a new read-only device preflight. Parent `lstat` on the current
boot confirmed `/dev/ttySAC1` character major 204/minor 65 and `/dev/btpower`
character major 503/minor 0. The latter must be rechecked per boot, not
hardcoded as a universal driver number. The probe also refuses to proceed
unless the read-only CNSS baseline is exactly
`State: 0x400000(PCI PROBE DONE)`, `/sys/module/wlan` is absent, and
`/sys/class/net/wlan0` is absent. These are the already source-backed
quiescent-WLAN checks from `tools/hardware/wifi-bringup-once.py`, not an
assumption that a shared regulator is independent. If a parent authorizes it,
the probe configures 115200 8N1 with hardware flow control, sends exactly
`01 00 fc 01 06`, reads only generic H4 event framing under a 1-second
deadline, caps the raw capture at 1024 bytes/eight events, prints event code
and payload as `UNKNOWN`, then performs QTI-compatible RTS/flush/`0x54ee`
cleanup and attempts `BT_CMD_PWR_CTRL(0)` even after a transport error. It
never sends firmware, changes rfkill, invokes hciattach, or interprets a
version response. SIGINT/SIGTERM only set a `sig_atomic_t` cancellation flag;
the normal path then restores any saved termios and attempts the bounded
power-off cleanup. `tools/hardware/test-bt-version-transport.sh` compiles it
with `-Wall -Wextra -Werror` and passes the parser/constants self-test plus
both device-identity refusal tests on the host.

The current phone has working WLAN, so it deliberately does not satisfy the
quiescent-radio requirement. No Bluetooth device was opened and no power
sequence was attempted; preserving the live remote network takes priority.
