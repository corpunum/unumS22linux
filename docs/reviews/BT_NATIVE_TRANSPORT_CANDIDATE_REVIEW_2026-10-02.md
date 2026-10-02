# Independent BT transport candidate build review — 2026-10-02

## Scope and disposition

Reviewed the four files added by author commit
`a85a878329bc1c631a2026c9dc029547215ba93`: the candidate builder, its test,
the sanitized receipt, and the build research note. The review worktree is at
`b223820e0fb19991a03ec847332b02f2f8350a5f`; the only change after the author
commit is the documentation hash correction from
`a839b321e8e5b26bfbb93ecd79f660bea7bb81d2`. The builder, test, and receipt
remain byte-identical to the author commit. The requested reviewer selection
was `gpt-6-luna`, reasoning `max`; that records the requested configuration,
not model self-identification.

Disposition: the retained artifact's identity and static ELF properties
independently match the captured receipt. I clear this exact SHA-256 only as
an identified, unexecuted host candidate. Builder-to-artifact provenance is
not closed: the exact helper bytes and Python startup environment used for
the one recorded compile were not contemporaneously attested. This review
does not clear candidate execution, staging, deployment, controller behavior,
or device acceptance.

## Artifact and input evidence

The captured artifact is
`/home/corpunum/s22-linux/builds/bt-native-transport-candidate-20261002/bt-qca6490-hci-bridge-probe-native-20261002`.
Independent hashing and ELF inspection found:

| Property | Observed value |
| --- | --- |
| SHA-256 | `74b39343eaa0cd4e7176fb0fef0d6a30ee3e6abcea8954f68331381e719d8c61` |
| GNU build ID | `c5c9be207d9a957fc52734f056c510218ac01be5` |
| Size and mode | 1,042,008 bytes; `0700` |
| ELF | ELF64, little-endian AArch64, `ET_EXEC`; no interpreter or dynamic section |

The ELF header and build ID agree with `readelf`; the file is statically
linked. I inspected it as data and did not execute it.

The saved compiler depfile, link map, build log, and dependency manifest hash
to `8c139471cf0ee9ea6d0c44e895dbe3b5588b2465603a63931abbac29639eb80a`,
`4fb01d83d0d092b9eeb4b376f11a5f016c1e018a2d088869d7a4ec926e34f41e`,
`01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b`, and
`25dfe4c980f6bfe85bf25dee9a542c2ce88c19f3355cbac440180b7ea2ae9a95`,
respectively. The build log is one byte (`0a`, newline), so it contains no
compiler output or invocation details. The candidate directory is mode `0700`;
the manifest is `0600`.

The manifest is a compact identity-to-SHA-256 map. Its canonical sorted JSON
hash independently matches the receipt. It has 208 entries: 8 public source
files, 2 private input labels, and 198 system-path hash labels. Every value is
a 64-character lowercase SHA-256; the published keys contain no absolute
paths or file contents. All eight source hashes match their pins at source
commit `79c539ea55a1663e946ed881606232519873fd93`. The local exact-fixture
tests also verified the pinned GCC 13 driver, selected compiler components,
and private headers. The compiler driver SHA-256 is
`cd90adc7801f4595267f61a5d25bd3a0c6beb2f9f1f107ab919a97a12972dc9a`; the
selected `cc1`, `collect2`, assembler, and linker hashes match the receipt.
I hashed the eight runtime files named by link-map
`LOAD` entries (`crt1.o`, `crti.o`, `crtn.o`, `crtbeginT.o`, `crtend.o`,
`libc.a`, `libgcc.a`, and `libgcc_eh.a`); all match the receipt's pins.

The receipt describes one successful `-static -std=c11 -Wall -Wextra -Werror
-O2` AArch64 crosscompile and no execution or device/deployment action. This
review did not rebuild. The existing runner and consumed trial remain on the
old source and artifact pins; this candidate has a distinct name, output
directory, and digest.

## Host tests

I ran the committed suite in the three requested interpreter modes. Each run
passed 13/13 tests: 10 portable cases and all 3 optional exact-local-fixture
cases. The modes reported effective optimization levels 0, 1, and 1. The
portable cases exercised temporary Git source gates, inherited compiler
environment refusal through the real CLI, output collision refusal, ELF/hash
refusal, and the fixed one-target static command. The local preflight test
used `--check-only`; it did not compile or alter the existing output.

I also ran a controlled absent-fixture simulation. It masked only the exact
local compiler and two private-header paths from the fixture-presence check:
all 10 portable tests ran and passed, and exactly the 3 local-fixture tests
skipped with the documented reason. This verifies the skip branch, not a
separate machine installation without those files.

## Blocking provenance limits

The receipt explicitly says that the builder SHA-256
`7d17e06534356bfbd60c12f29a70bbfd859a8305db9231838122dcaf6c1ff9cf` was
measured after the build, and that no contemporaneous executed-helper hash
was captured. The checked-in helper's current bytes match that digest, but
that does not prove those exact bytes produced this artifact. The one-byte
build log supplies no independent command transcript.

The helper also checks compiler and build redirection variables only after
Python has started. It does not reject Python startup controls such as
`PYTHONPATH` or require an isolated interpreter; a startup hook such as
`sitecustomize` can run before `check_environment()`. The tests cover `CPATH`
refusal, not Python startup isolation, and the receipt does not identify the
Python interpreter or its startup environment. This is a blocker to treating
the helper's own receipt as self-authenticating build provenance. I found no
evidence that a startup hook or altered helper was used for this compile.

Finally, pre-build and post-build hash equality does not rule out a
concurrent change-and-restore while the compiler reads mutable source or
header paths. No such event was observed; the retained depfile and manifest
support the stated before/after closure, but are not a compiler-read
snapshot.

For a future build to carry stronger provenance, run the reviewed helper
under a pinned isolated Python startup, record its digest before execution and
capture the actual invocation/environment. Keep the source and private input
snapshot stable for the duration of the compile.

## Evidence boundary

The result establishes a matching host artifact identity, ELF format, and
captured input/toolchain records. It does not establish execution behavior,
Bluetooth HCI transactions, controller compatibility, radio or audio
operation, kernel/module integration, or deployment readiness. No phone,
SSH, ADB, HCI device, firmware operation, staging, upload, or powered test was
used for this review.
