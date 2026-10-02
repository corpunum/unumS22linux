# Independent audio debug-strip review — 2026-10-03

## Verdict

I found no implementation or focused-test blocker to one separately
coordinator-authorized, host-only packaging attempt. This review does not run
or authorize packaging itself. The preceding attempt's AVB size rejection is
still unresolved: the debug-strip transform has not been passed through LZ4,
mkbootimg, or the final image-size gate, so another attempt may fail for size.
No retry is implied by this review.

The reviewed author snapshot is commit
`7a8ffd3d78179e80006247520080c3f981e1e27e`, tree
`be16c55dca8148ac41ddd89697ca406009151d08`. I imported that frozen commit into
this isolated review worktree without changing its implementation. The
builder SHA-256 is
`3b7e031974fb60930629d14dff2df62c01aea27c89383f3240cfb4921fa4e462`; the
test SHA-256 is
`21d00edce1ae0fa96e256b5d1cbbc4d78f9af8881047ab2a119d8b32de597616`.

## Independent checks

The 19 focused hardware-free tests passed in three independent invocations:
normal Python, `-O`, and effective `PYTHONOPTIMIZE=1` (19/19 each, no skips).
The suite covers exact pinned module derivatives, CPIO target-only
replacement, rejection of altered allocated bytes/runtime relocations,
malformed symbol-table boundaries and signature trailers, pinned GKI import
failure/success paths, actual pinned mkbootimg help and a tiny synthetic v2
image, external WLAN internal-name validation, startup isolation, and
exclusive output refusal.

I separately ran only the safe in-memory transformation and validation path;
I did not invoke the package builder. The three original module sources
remained distinct from their verified stripped derivatives:

| Module | Source SHA-256 | Stripped SHA-256 | Bytes after strip |
|---|---|---|---:|
| ABOX5 | `55bae9f12135a2134337d7d520ddfadc85cdd049cedefd2a3c41f871fdd329bf` | `61d846d2bb13d5ff48261f21efadcd8b378bdc586c28b3e288ddccf145488265` | 1,735,880 |
| Rainbow Prince | `6461073beee9e1fdc4f7c92b250bbb773a18cbd766e0c9331e77ec01e5e45170` | `b896200b333be6d518b9eb4b218abefe8c115a3162e5fb3b1a7016a6b7175c7a` | 70,792 |
| USB audio offloader | `92116d85c21c9c3969e00746fb0299a5cb7725edfe9c344d5645417686416ec2` | `3b732ada44c0a6812b6aaeb38d7de8757ad54e7f843c6761b97ff4e5d63e8392` | 42,704 |

For the actual ABOX5 derivative, additional reviewer-created in-memory
mutations were rejected: changing a `.rela__ksymtab` addend, increasing the
`.bss` NOBITS size, changing `.modinfo` allocation flags, and decrementing the
in-range `.symtab.sh_info` local-symbol boundary. These checks add negative
coverage for protected relocation semantics and symbol-table boundaries;
they are not observed module failures.

The preserved base recovery CPIO was unpacked only in a temporary directory.
Only the three pinned module payloads were substituted in memory; the other
960 raw records remained byte-identical, including `modules.dep`,
`modules.alias`, and `modules.softdep`. The exact updated 17,283-row provider
map then passed the static check for all 324 ramdisk modules (16,569 imports)
and the selected external WLAN (495 imports): zero missing, CRC-mismatched,
unknown, or ambiguous imports, with `module_layout` CRC `0x0e3c515c`. This is
host ELF/MODVERSIONS evidence only, not loader, live-phone, or audio-runtime
acceptance.

I independently checked the pinned objcopy alias and executable, helper
source hashes, the mkbootimg and GKI helper hashes, and the base/module/map
pins. The GKI bootstrap uses a child interpreter with `-I -S -B` and adds only
the explicit pinned Android-tools directory; wrong/missing helper tests fail
before launching the child. The full candidate image path, AVB footer, and
atomic output publication remain unexercised in this review.

## Limits and preserved failure

The original package receipt remains unchanged and records a pre-footer image
of 101,259,264 bytes against a maximum of 100,593,664 bytes for the
100,663,296-byte partition: a 665,600-byte overage. Debug stripping greatly
reduces the three module payloads, but does not establish compressed ramdisk
size or final fit. A separately authorized packaging attempt must retain the
existing LZ4, AVB, ABI, exact-three-module, and exclusive-output gates; this
review does not approve weakening them or authorize a second attempt.

No candidate image, candidate CPIO, output manifest, module load, deployment,
phone access, boot, or reboot occurred. Kernel loading, current-phone module
membership, PM/IPC/DMA/PCM progress, physical audio, Samsung authentication,
and bootability remain unproven.
