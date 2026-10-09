# Independent review: audio coherent package follow-up

## Verdict

The two deterministic blockers recorded in
`AUDIO_COHERENT_PACKAGE_REVIEW_2026-10-03.md` are fixed in this exact follow-up.
I found no new implementation blocker to one bounded HOST-only packaging run,
subject to a separate coordinator GO. This review does not authorize that run,
does not establish a package artifact, and grants no phone, boot, deployment,
module-load, or hardware acceptance.

The earlier BLOCKED review remains unchanged as the accurate verdict for its
older source. No package function was called here; no candidate CPIO, ramdisk,
recovery image, or output directory was created.

## Frozen source and local pins

- Reviewed source commit: `8f87edd7f01a5d1c04d8f1a0b3b9eb4e4acb1792`
  (tree `7c11c213fe71664dd22f1c66925f29b355164c63`). The follow-up author
  commit is `c8606ff265d2d7eae7afcb3ffd994f168c32f5e3`.
- Builder SHA-256:
  `2824a0adc2198262e90d549036d1d72a5aef42b6f833672401d3e79b66d95ee2`.
- Focused test SHA-256:
  `b412daf7cb2da6dba36338346861210b156d7ba47e008a2488af379e5253b62b`.
- Follow-up research note SHA-256:
  `f31427e34ce6d3f4db5c8930b63ba66447a22b9d8bbfe2038d9265a475b72a59`.
- Preserved authored package note SHA-256:
  `d2c0749a2d81486ca9813308c021cdafb5c00fd48f18dc7f48ee2c11d3dfa1ed`.

Independent rehashes matched the B104 recovery image
`b10412715756da3cc8ee221368b49f179cc0c64ab7bd2802976480905e6d8d2f`,
native-eight `Module.symvers`
`15fc69e815cb4da6cb3372f4b5141005ab2e770b0414b67f230f1b185bf03df7`,
candidate ABOX5 `modules-only.symvers`
`e73d89c9637e3cea289c4bc61d999967d9cd20ae33a73bf71bfd13c7439f67ac`,
candidate offloader `modules-only.symvers`
`715a469f62a95ab8813c866cb0656f756730475fb80322b1afb4dfcbcfd527ad`,
the three candidate modules (ABOX5
`55bae9f12135a2134337d7d520ddfadc85cdd049cedefd2a3c41f871fdd329bf`, Rainbow
`6461073beee9e1fdc4f7c92b250bbb773a18cbd766e0c9331e77ec01e5e45170`,
offloader
`92116d85c21c9c3969e00746fb0299a5cb7725edfe9c344d5645417686416ec2`), and
selected external WLAN
`cbf8932d079e97006a5b7aae0e1b5acfe65b3e8b5113ab8fa095daa36796738d`.
The canonical 17,283-row provider map was rebuilt from those exact inputs;
its SHA-256 is
`0c8e481225fe3ab071ba9be1d14faae06ce8a556fa7e89b07f3dcefb761bd470`.

## Follow-up findings

1. **WLAN identity is now checked from the ELF.** The descriptive preflight
   label remains `selected external Lineage WLAN`, while a separate pinned
   `modinfo -F name` invocation must return the expected internal name `wlan`.
   The check no longer compares a display label to module identity. Both
   negative name cases and the positive `wlan` case pass; the real static scan
   below also exercised this check against the actual selected file.

2. **The isolated mkbootimg import now works for the pinned tool.** The
   builder verifies both `mkbootimg.py` (SHA-256
   `37d84b3d162e0bc62e36c1f4e1c63c85ea0caa9f29be023eb2f8efe006ad948c`) and
   `gki/generate_gki_certificate.py` (SHA-256
   `1bb1feec68a13da18d581aa2c631798f86f6bc10b55d587b2dd31446a0f8a203`)
   before child launch. It retains `-I -S -B` and inserts the one explicit
   pinned `tools/mkbootimg` directory into the child's `sys.path`. The
   `gki` directory has no `__init__.py`; both a controlled namespace-package
   fixture and the actual pinned `mkbootimg.py --help` path passed. A real
   pinned-tool smoke also generated a tiny temporary header-v2 fixture; it
   was not a recovery image and was discarded.

   This is a narrowly pinned import arrangement, not a sandbox or complete
   runtime/toolchain closure. The child can import files under the explicitly
   exposed tool directory, and the surrounding Python/OS runtime is not
   hermetically captured. The implemented checks hash the two imported
   project files before launch; they do not make the directory immutable or
   eliminate a concurrent-change race. No claim of startup-hook prevention,
   privilege isolation, or authorization follows from `-I -S`.

## Independent read-only static inspection

Using the pinned unpacker and temporary directory, I revalidated the B104
header-v2 image and exact 963-record base CPIO, then replaced only the three
pinned module payloads in memory. The comparison found exactly those three
changed CPIO records; all other raw records and order were preserved, and the
three pinned `modules.dep`, `modules.alias`, and `modules.softdep` records
matched. The provider map removed only the 28 old ABOX and five old offloader
rows, added the corresponding actual module-only rows, and had no
distinct-CRC duplicate provider symbols.

The actual static ABI scan inspected all **324** resulting ramdisk modules
and **16,569** imports: zero missing symbols, CRC mismatches, unknown or
ambiguous CRCs, missing `__versions`, vermagic mismatches, or missing/wrong
`module_layout` records. Every module matched vermagic
`5.10.260-g4e5c5ad7d950 SMP preempt mod_unload modversions aarch64` and
`module_layout` CRC `0x0e3c515c`. The external WLAN, not included in the
ramdisk, passed the same checks for all **495** imports and reported internal
name `wlan`.

This used the production `inspect_static_abi` path with pinned host
`modinfo`, `modprobe`, and `readelf`; it did not call `build_candidate`, AVB
footer generation, mkbootimg for a candidate, or any output publication.
The temporary input/output files were removed at process exit.

## Regression runs

All 15 focused tests passed in each mode on CPython 3.12.3. The environment
mode was separately verified as `sys.flags.optimize == 1` and did not use
`-I`:

```text
TMPDIR=/tmp python3 -I -S -B tools/hardware/test-build-audio-coherent-recovery.py
TMPDIR=/tmp python3 -O -I -S -B tools/hardware/test-build-audio-coherent-recovery.py
TMPDIR=/tmp PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-build-audio-coherent-recovery.py
```

The suite ran the actual pinned-tool help and tiny header-v2 cases locally;
neither was skipped. It also exercised the wrong-helper-hash and missing-helper
negative paths, plus wrong internal module names. The effective environment
mode is test execution only.

## Remaining limits

This review clears only the identified code/preflight blockers for a possible
coordinator-authorized host package operation. No package artifact was made
or verified here. Even a later successful host package and footer check would
not prove Samsung authentication, bootability, current-phone module
membership or loading, runtime audio/PM/IPC/DMA/PCM behavior, physical audio,
deployment, or hardware acceptance. The 324-module static CPIO inventory is
not the separately observed 325-module current-phone runtime inventory.
