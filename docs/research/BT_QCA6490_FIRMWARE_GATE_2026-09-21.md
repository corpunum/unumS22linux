# QCA6490 firmware gate — 2026-09-21

Host-only audit after the parent-authorized live-WLAN version exchange. No
phone action was performed for this note and no firmware was sent.

## What the version exchange established

The bounded H4 exchange returned Command Complete for opcode `0xfc00`, status
`0`, sub-operation `06`, with:

```text
Release 19.0201 PF=QCA6490 BUILD=BTFW.HSP.2.1-00124-ROM-1
LMP=0x38E6 SoCID=0x400C0210
```

This is strong controller identity and transport evidence. It is not proof of
an initialized HCI stack, patch/NVM acceptance, Bluetooth class registration,
or audio operation.

## Matching recovered files

The recovered matching vendor closure contains both generations:

| role | candidate files | host size |
|---|---|---:|
| Hastings rampatch | `hpbtfw20.tlv`, `hpbtfw21.tlv` | 163760, 195848 |
| Hastings NVM | `hpnv20.bin`, `hpnv21.bab`, `hpnv21g.bab` | 6203, 7023, 6863 |
| Hastings XML override | `bt_nvm_loading.xml`, `bt_nvm_loading_2nd.xml` | 3649 each |
| older/alternate transport family | `htbtfw20.tlv`, `htnv20.bin` | 212048, 5941 |

The QTI HAL's literal strings include `qca6490`, `hastings`,
`HASTINGS_VER_2_0`, `hpbtfw20.tlv`, `hpbtfw21.tlv`, `hpnv20.bin`,
`hpnv21.bab`, `hpnv21g.bab`, and both XML paths. Its QTI path therefore
supports the Hastings `hp*` family; the `ht*` pair is not the default for this
identified controller.

The observed `Release 19.0201` is not itself a file-selector value. The
`0x19` Get-App-Version sub-operation belongs to a different upstream QCA
command variant; this HAL's matching QTI command is
`01 00 fc 01 06`, which already succeeded. Do not choose `hpbtfw20` merely
because the response contains “19”, and do not treat the response as proof of
the `hpnv21g.bab` board variant.

## What the QTI disassembly proves about selection

In `PatchDLManager::SocInit` (captured `/tmp/qti-socinit.txt` from the
hash-recorded QTI HAL), the HAL first calls `FormDefaultPaths`, performs the
five-byte Get-App-Version exchange, then calls `SetBaudRateReq` and
`DownloadTlvFile` (roughly `4b550-4b678`). It subsequently calls
`GetBuildInfoReq`, `DisableInternalLdo`, optional add-on/OTP operations, and
other controller-init operations (`4b69c-4b70c`). Thus the app-version reply
is only the first gate of a larger state machine.

The HAL contains separate `FormRegularPaths`, `GetTlvFile`,
`SetRampatchRegularPaths`, `SetNVMRegularPaths`, `UpdateHastingNvm`, and XML
parsing symbols. Static strings prove the available names, but the available
host evidence does not establish the exact branch selecting `hpnv21.bab`
versus `hpnv21g.bab` for this board ID. The returned SoCID is not a sufficient
board-ID proof for that choice. The safe claim is therefore:

* `hpbtfw21.tlv` is the matching generation candidate for the returned
  Hastings/QCA6490 build (`BTFW.HSP.2.1-00124`), but patch acceptance remains
  unproven.
* `hpnv21.bab` is the normal generation candidate; `hpnv21g.bab` is an
  alternate candidate whose board-selection predicate is not recovered.
* The XML files are HAL override inputs, not interchangeable firmware blobs;
  their target is `WCN6856_2.1`/`R0` and they must only be consumed by the
  matching QTI XML parser after the HAL has selected the NVM format.

No source evidence supports blindly sending both NVM candidates, trying the
`g` variant after an error, or using `htnv20.bin` as fallback. Such retries can
change controller state and are not a safe native initialization policy.

## Minimal safe initialization sequence (not an execution command)

There is no safe one-line native `hciattach` command for this controller. The
minimum source-matched sequence is the QTI controller state machine:

1. Acquire exclusive `/dev/ttySAC1` and `/dev/btpower` ownership only after
   the reviewed live-WLAN/regulator/GPIO gates. Power BT through `0xbfad` and
   configure 115200 8N1 with hardware flow control. The UART must remain at
   this initial baud for the version exchange.
2. Send the five-byte H4 command `01 00 fc 01 06`; validate the returned
   Command Complete's declared length, opcode `fc00`, status `00`, sub-op
   `06`, and expected QCA6490/Hastings identity. The observed reply was a
   95-byte H4 event, not a five-byte reply.
3. Run QTI `SetBaudRateReq`, using the controller-reported maximum-baud
   selection and restoring hardware flow control after the baud transition.
   A guessed enum or a direct 3-Mbit switch is not equivalent.
4. Run QTI `DownloadTlvFile` with the selected `hpbtfw21.tlv`, then the HAL's
   required build-info, internal-LDO, add-on/OTP, and NVM/XML operations in
   their compiled order. NVM selection must come from the recovered board-ID
   branch; it is not safe to substitute `hpnv21g.bab` by filename similarity.
5. Only after all QTI init gates succeed may a controller reset and normal HCI
   protocol registration be attempted. Preserve QTI RX/event parsing,
   recovery timers, and teardown; do not expose the raw line discipline to
   BlueZ as “working” after the version response alone.

The existing raw probe intentionally stops after step 2 and treats returned
events as opaque. That was the correct bounded diagnostic. Extending it to
patch/NVM would require a separately reviewed QTI-compatible implementation
including the Android property/logging dependencies and the unresolved board
selection branch.

## Decision

The controller identity is now proven and the best matching patch candidate
is `hpbtfw21.tlv`. A working native H4 stack is **not** established: the QTI
HAL must perform patch, baud, NVM/XML, build-info, and teardown/recovery work
that the raw probe does not implement. Do not run `btattach`, send a guessed
NVM, or select `hpnv21g.bab` without recovering the HAL's board-ID predicate.
The next safe implementation is a source-matched QTI closure or an explicitly
reviewed native implementation of that state machine, followed by bounded HCI
registration and independent Bluetooth data-path validation.
