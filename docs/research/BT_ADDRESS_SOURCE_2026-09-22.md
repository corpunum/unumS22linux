# QCA6490 Bluetooth address source audit — 2026-09-22

Host/source-only audit. No EFS, IMEI, phone filesystem, address generation,
asset mutation, or device operation was performed.

## What is proven

The private-HAL binary is present at `/tmp/android.hardware.bluetooth@1.0-impl-qti.so`,
SHA-256 `153f3ca839d52dc1be6fe48724a97f97b6bd24020cb5fb4b2be33cc195e0584f`.
It is stripped but retains dynamic C++ symbols, including
`BluetoothAddress::GetLocalAddress(unsigned char*)` at `0x3a548`,
`PatchDLManager::GetBdaddrFromProperty(char const*, unsigned char*, bool)` at
`0x4ab04`, and `PatchDLManager::PerformChipInit()` at `0x4b03c`.

The exact `GetLocalAddress` chain is:

- `PerformChipInit+0xe4` (`0x4b120`) calls `GetLocalAddress` with `this+0x6a`;
  post-call timestamps are recorded at `0x4b154–0x4b15c`.
- It first reads `ro.bt.bdaddr_path` (literal `0x2d2e6`) at
  `0x3a620–0x3a630`. If nonempty, it opens that path and parses six
  hexadecimal fields with `fscanf` at `0x3a648–0x3a710`, reverses them into
  controller order, and returns success.
- If that path is unavailable/invalid, it checks cached address bytes in
  `bt_address_ins` (`x21+92`/`x21+96`, `0x3a978–0x3a9ac`).
- It then reads `ro.vendor.bt.boot.macaddr` (literal `0x2d24b`) at
  `0x3a9b0–0x3aa60`, requiring 17 characters and six parsed fields; next it
  reads `persist.vendor.service.bdroid.bdaddr` (literal `0x2d265`) at
  `0x3aa64–0x3ab04` with the same validation.
- If all sources fail, it seeds `rand()` from `clock_gettime(CLOCK_MONOTONIC)`
  (`0x3ab10–0x3ab4c`), constructs an address beginning with `0x2222`, formats
  it, and writes it through
  `Logger::PropertySet("persist.vendor.service.bdroid.bdaddr", value)` at
  `0x3abf4–0x3ac04`. This is generated fallback state, not a factory source.

The NVM-side consumer is:

- `ReadTlvInfo`'s type-2 tag-2 branch is at `0x4d5dc–0x4d5ec`.
- It copies four bytes plus a 16-bit halfword from the HAL's local-address
  buffer at `manager+0x6a` into the six-byte tag-2 payload.
- This proves that tag 2 is supplied by a HAL local-address buffer at runtime;
  it does not identify how that buffer is populated.

The accepted post-patch standard `Read_BD_ADDR` receipt returned status 0 with
six zero address bytes. The host candidate's command is visibly
`01 09 10 00` in `tools/hardware/bt-qca6490-patch-capture.c:15`, and its
parser deliberately reports all-zero/all-FF state without manufacturing an
identity. This is the only runtime address value retained here, and it is not
evidence of a factory address.

## Sources checked for a non-EFS route

The HAL therefore has two non-EFS configuration routes: `ro.bt.bdaddr_path`
points to a platform-selected file, and `ro.vendor.bt.boot.macaddr` is a
direct boot property. The binary does not identify the file's backing storage
or prove either value is factory/OTP-derived. The local recovered firmware assets
contain tag 2 as `ad 5a 00 00 00 00`; this is a conventional record shape with
zero tail bytes, not a device-specific factory address.

The pinned kernel tree contains QCA HCI transport code and Exynos UART code,
but no source path that supplies a Bluetooth MAC to the private QTI HAL. The
standard HCI response is controller state, not a proof of OTP/EFS provenance.

## Safe conclusion

There is a source-supported HAL route to consume a platform-provided address,
but no proof that either platform source is factory/OTP-backed in this runtime.
The HAL's last resort is explicitly random generation, so it must never be
accepted as a factory identity. The known runtime path supplied six zeros; the
diagnostic NVM image correctly keeps tag 2 zero and must not synthesize or
substitute an address.

This does not justify reading EFS/IMEI or claiming a factory address. A
separate normal Linux profile may use the HAL's explicit generated-address
fallback, provided its provenance remains `linux-generated`, the address is
persisted as ordinary private Linux configuration, and the zero-address
diagnostic is never used for normal radio operation.

## Exact NVM/reset ordering

The same HAL binary gives a precise post-NVM boundary. `NvmTagsManager::SocInit`
at `0x72bb8` calls `GetLocalAddress` (`0x72bcc`), then
`DownloadNvmTags` (`0x72bdc`); only if that succeeds does it call
`NvmTagsManager::HciReset` (`0x72be8`). The reset is therefore not part of the
normal `PatchDLManager::DownloadTlvFile` non-XMEM routine itself.

In `DownloadTlvFile`'s non-XMEM path, `GetTlvFile` is called at `0x5013c`,
followed by `TlvDnldReq` at `0x5038c`, then the function records post-NVM
timing and returns through `0x5046c`; there is no reset call on that path.
The separate `NvmTagsManager::HciReset` constructs the three-byte command
payload at `0x72fdc–0x72fe0`: little-endian `0x0c03`, followed by zero
(`03 0c 00`), sends it through the transport vtable at `0x72fe4–0x72ffc`,
and reads the event at `0x73010`. Thus a future full initialization must
reserve reset for the outer `SocInit` state after successful NVM transfer;
the host runtime-address builder remains offline-only and does not encode or
send this command.

Root independently checked the relevant modern `PatchDLManager::SocInit`
branch too: `DownloadTlvFile` is called at `0x4b674`, followed on success by
`PatchDLManager::HciReset` at `0x4b828` (implementation `0x51f70`). The legacy
`NvmTagsManager` branch above is not the sole evidence for this ordering.

## Live normal-profile reset/readback

`runtime-reset-first` completed in 4.864 seconds on recovery boot 765. It
downloaded the exact patch and all 29 NVM segments, then received standard
Reset Command Complete `04 0e 04 01 03 0c 00`. Read BD_ADDR matched the private
Linux-generated address exactly. Read Local Version reported HCI version
`0x0c`, manufacturer `0x001d`, subversion `0x687c`.

The identity is persisted on the host under ignored private Linux
configuration, not read from or written to Samsung identity partitions. No
protected partition, discovery, advertising, pairing or HCI registration was
involved. The controller was powered off afterward; the independent USB link,
WLAN vote and model health were preserved on the same boot.

Pi/Qwen's actual 68.61-second source review (`btreset01`) proposed checking
Reset before NVM. That proposal was rejected: it contradicts the reviewed
modern HAL ordering and the accepted reply trace. Model execution is evidence
of a review, not evidence that its recommendation is correct.
