# Audio package debug-strip correction — 2026-10-03

## Result and boundary

The packager now derives a separately pinned `--strip-debug` copy of each of
the three audio modules before the existing CPIO, module-metadata, and static
ABI checks. The unstripped build outputs remain the source inputs and are
never overwritten. The real `llvm-objcopy-18` outputs match the exact hashes
recorded below, and a read-only in-memory CPIO substitution passed the full
324-module plus selected-WLAN static MODVERSIONS scan.

This work **did not run the recovery packager** and created no candidate
recovery image, candidate ramdisk, output manifest, or persistent candidate
CPIO. The static check used a temporary unpack of the preserved base ramdisk
and kept its substituted candidate CPIO in memory. The earlier single
package attempt remains failed before publication: AVB rejected its
101,259,264-byte pre-footer image against a 100,593,664-byte maximum for the
100,663,296-byte partition. This transform has not yet been shown to resolve
that size failure; compression and the final image-size gate remain untested.
An independent review and a separate coordinator GO are required before any
new packaging invocation.

No phone, SSH, ADB, firmware, module loading, deployment, flash, boot, reboot,
or NPU operation occurred. Host static checks are not runtime or hardware
acceptance.

## Inputs and transformation

The three original ELF64 AArch64 module sources are pinned independently of
their packaged derivatives. They came from the separately reviewed native
ABOX5/two-consumer artifacts built from kernel source commit
`7363ab97d917a20f2c96632c94efd9ab7df7729e` (tree
`84325be79eab0244dd9a130e51d0974f6eadd4f5`). The only transform is the
standard `llvm-objcopy-18 --strip-debug` operation used by the pinned kernel
module-install flow (`INSTALL_MOD_STRIP=1` maps to `--strip-debug`; the
install rule copies the built module before applying it).

The invocation alias `/usr/bin/llvm-objcopy-18` must resolve exactly to
`/usr/lib/llvm-18/bin/llvm-objcopy`; the resolved regular executable SHA-256
is `f52b9997b3c5019b4b3043e12b1ae2e821df67996ca344921c234c89c4d23e34`.
Each derivative is created exclusively in a temporary directory, checked
against its own size and SHA-256 pin, and never substituted for the original
source identity in provenance.

| CPIO module | Original bytes / SHA-256 | Packaged bytes / SHA-256 | GNU build ID (unchanged) |
|---|---:|---:|---|
| `snd-soc-samsung-abox.ko` | 9,596,112 / `55bae9f12135a2134337d7d520ddfadc85cdd049cedefd2a3c41f871fdd329bf` | 1,735,880 / `61d846d2bb13d5ff48261f21efadcd8b378bdc586c28b3e288ddccf145488265` | `26347c3373e155fa6badf7883ff162f1d9f6723f` |
| `rainbow_prince.ko` | 522,472 / `6461073beee9e1fdc4f7c92b250bbb773a18cbd766e0c9331e77ec01e5e45170` | 70,792 / `b896200b333be6d518b9eb4b218abefe8c115a3162e5fb3b1a7016a6b7175c7a` | `8a7227b58cb7f4faf73ea92974781d34870bbfac` |
| `exynos-usb-audio-offloading.ko` | 401,656 / `92116d85c21c9c3969e00746fb0299a5cb7725edfe9c344d5645417686416ec2` | 42,704 / `3b732ada44c0a6812b6aaeb38d7de8757ad54e7f843c6761b97ff4e5d63e8392` | `8c9b0d4787ea32eae7de0086a4d662b0f351675d` |

Only these debug and debug-relocation sections disappeared:

- ABOX5 and Rainbow Prince: `.debug_abbrev`, `.debug_frame`, `.debug_info`,
  `.debug_line`, `.debug_loc`, `.debug_ranges`, `.debug_str`,
  `.rela.debug_frame`, `.rela.debug_info`, `.rela.debug_line`,
  `.rela.debug_loc`, `.rela.debug_ranges`.
- USB offloader: the same set except `.rela.debug_loc` and
  `.rela.debug_ranges`.

No section was added; all other section names and ordering were preserved.
The validator compares section identity/flags/address/alignment/entry size
and link/target relationships, while allowing offsets and symbol indices to
move as expected when debug data is removed. For `SHF_ALLOC` sections it
requires exact bytes and metadata; `SHT_NOBITS` sections such as `.bss` have
no file contents to compare, so their type, size, flags, address, and
alignment are checked instead. Non-debug, non-relocation section contents
remain exact. Runtime symbol tables are compared by canonical symbol identity
and multiplicity rather than raw string or symbol-table offsets. Runtime
relocations are compared in order by target section, offset, relocation type,
resolved symbol identity, and addend, not raw symbol indices.

The kernel module metadata sections (`__versions`, `__ksymtab*`,
`__kcrctab*`, `.modinfo`, and related relocations) are included in the
preservation checks. Allocated export/version bytes are exact; relocation
semantics are canonicalized so legitimate index renumbering is accepted.
`modinfo -0` fields excluding only the path-dependent `filename` field and
`modprobe --dump-modversions` records were identical as ordered normalized
key/value records before and after transformation. The original GNU build ID is reported
as preserved metadata, not as the derivative's whole-file identity; the
derivative SHA-256 is recorded separately. Inputs with an appended Linux
module-signature trailer are rejected rather than silently invalidated.

The per-module section counts, allocation and runtime relocation/symbol
digests, export-CRC symbol counts, modinfo digests, and imported-CRC digests
are in the machine-readable evidence file.

## Static integration check

The preserved 963-record camera CPIO was decoded in a temporary directory.
Only the three exact transformed payloads were substituted in memory; raw
non-target CPIO records were left unchanged. Against the exact updated
17,283-row provider map, the pinned preflight checked all 324 ramdisk modules
(16,569 imports) and the selected external WLAN (495 imports): zero missing,
CRC-mismatched, unknown, or ambiguous imports, with `module_layout` CRC
`0x0e3c515c`. This proves only static host ELF/MODVERSIONS consistency for
that inventory. It is not current-phone module membership, module-loader,
runtime PM/IPC/DMA/PCM, physical audio, or deployment evidence.

## Tests and remaining gate

The 19-test hardware-free suite passed in each of these modes:

- `python3 tools/hardware/test-build-audio-coherent-recovery.py`
- `python3 -O tools/hardware/test-build-audio-coherent-recovery.py`
- `PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-build-audio-coherent-recovery.py`

The environment-optimized test verified `sys.flags.optimize == 1`; it did
not combine `PYTHONOPTIMIZE` with `-I`, which would ignore that environment
variable. Tests exercised the pinned objcopy on a small compiler-generated
ELF fixture, exact real-module derivatives when the local pinned inputs were
available, and refusal of modified allocated bytes, changed runtime
relocations, malformed symbol-table local boundaries, and appended signature
trailers. All 19 tests ran (no private-module fixture skip) on this host.

The initial failed package receipt at commit `1edf236f266433e42a3d2e56500dcbafa25c9d78`
is unchanged. No attempt was made to weaken LZ4 settings, AVB limits, module
version checks, or metadata protection. Next step is independent review of
this implementation, then a separate coordinator decision on whether to
authorize exactly one packaging attempt.
