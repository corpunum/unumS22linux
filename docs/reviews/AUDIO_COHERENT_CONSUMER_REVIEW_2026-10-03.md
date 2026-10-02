# Independent coherent ABOX consumer artifact review — 2026-10-03

## Scope and disposition

Reviewed author commit `7d0c31145b421690f1b0030d3102d19c84f717e3`, integrated
at `497d680867d8abbfb31f35b1138d732b27a3b086`, using the retained host outputs
and records. I did not rebuild, execute a candidate module, access the phone,
or package or deploy anything.

Disposition: the retained ABOX5/Rainbow/offloader set independently passes the
specific pinned native-eight static import/export inventory described below.
This is not current-phone compatibility or driver/audio acceptance. I found
one stale host-test-coverage statement in the author report and JSON: the
generic physical-slot regression gap is marked unresolved there, but was
closed by the later host-harness test and independent review.

## Retained build and artifact identity

The recorded kernel source is commit
`7363ab97d917a20f2c96632c94efd9ab7df7729e`, tree
`84325be79eab0244dd9a130e51d0974f6eadd4f5`, parent
`3c11bdda6ba6ba27fb4eb7e2cb096d98c514d6d3`, clean in the retained source
worktree. Relative to native-eight base `872bffb8ea2ea657f94d10b866dc655b5718d6db`,
the four changed paths are `include/trace/events/samsung_abox.h` and
`sound/soc/samsung/abox/{abox.c,abox.h,abox_rdma.c}`. I rehashed these four
source files against the phase receipt. The candidate config is byte-identical
before and after `olddefconfig` (SHA-256
`d762d5fc71e369013ee36657d007063ce1f0faca9707d5f9ccfba2597b7fcd16`).

The retained raw-file hashes match the receipt: `olddefconfig.log`
`a447adc3f3a0807a2dd132a6ae14d7eb2f1a602cc15bc09ff897f4acfa10244b`,
`olddefconfig-phase.json`
`f104b4a3ce04b15f87c91ba6ad9aa295c57c826e1c280e86e55317f600cbdaf8`,
`build.log` `ce9b6a824754145b9e08f367c95c851e93b9db271803b96e777023164cc6845b`,
`build-phase.json`
`3653c6cb963bd3a7772ffdc22af0832c7ecf52eff43c3cce5254fc89e4f165dc`, and
`resource-monitor.jsonl`
`afa354484b7a3c3ce0572c1a6589a4574f9bacc54867ce0db114fa3ba44da197`. The
phase records olddefconfig exit 0 with unchanged config and a single `-j1`,
`V=1`, nice-10 build of only
`sound/soc/samsung/rainbow_prince.ko` and
`sound/usb/exynos-usb-audio-offloading.ko`; the build exited 0, reported no
resource abort, and recorded the owned process group absent. The output lists
only those two new `.ko` files, with no `Image` or `vmlinux`.

The exact retained module bytes independently match the report:

| Artifact | Size | SHA-256 | GNU build ID | Origin |
|---|---:|---|---|---|
| ABOX5 `snd-soc-samsung-abox.ko` | 9,596,112 | `55bae9f12135a2134337d7d520ddfadc85cdd049cedefd2a3c41f871fdd329bf` | `26347c3373e155fa6badf7883ff162f1d9f6723f` | separately retained ABOX5 build |
| `rainbow_prince.ko` | 522,472 | `6461073beee9e1fdc4f7c92b250bbb773a18cbd766e0c9331e77ec01e5e45170` | `8a7227b58cb7f4faf73ea92974781d34870bbfac` | two-consumer build |
| `exynos-usb-audio-offloading.ko` | 401,656 | `92116d85c21c9c3969e00746fb0299a5cb7725edfe9c344d5645417686416ec2` | `8c9b0d4787ea32eae7de0086a4d662b0f351675d` | two-consumer build |

All three are ELF64 AArch64 relocatable modules with the expected
`5.10.260-g4e5c5ad7d950 SMP preempt mod_unload modversions aarch64` vermagic.
The retained one-shot wrapper SHA-256 is
`2624029f6fc3406a2256acb0df72c904fb92a30cb5e96ee1f3b6d65916f9157f`; the
monitored helper is
`56f39759e4a098562cbafd634a659b007be2a540a4ada6234962711be026e5d5`; the
Symvers helper is
`2404fab4a2ed469eab1930a7275c28fd3f5a0bbb1b23c88369338ca23cd26c67`; and
isolated CPython 3.12 is
`e50d468e8b0adfb05733f5b87b3cff34829c4a8c1aea50c865aa8bdfe4bb150f`. All 13
configured LLVM/AArch64 tool binaries rehashed to their receipt values. These
are locally retained phase/log and hash records, not a signed external
execution attestation.

## Independent static inventory

I parsed the retained native-eight `modules.order` and artifacts and
independently substituted only ABOX5 plus the two rebuilt consumers. The
sorted 329-path inventory digests are `d79baeab5ac13f13227b9f9ab71894f22ccab6f8157ceddb81f78624f83ec6ad` (paths) and `06e59a60df846aff3a2877367793b4558d1da610714df73f246ca72f2d2d1e77` (ordered path plus artifact hashes); all remaining 326 module
files are the original native-eight artifacts. Using `modprobe --dump-modversions`
as a read-only ELF parser (no module load), I rescanned all 17,255 import
records. All 329 modules have a `__versions` section, exactly one
`module_layout` import at CRC `0x0e3c515c`, and the exact expected vermagic.
The native-eight base `Module.symvers` has 17,283 rows and SHA-256
`15fc69e815cb4da6cb3372f4b5141005ab2e770b0414b67f230f1b185bf03df7`; the
17,278-row build input map is
`89aadfec054550479f5b2f05fcd70c51a51bddbc7306209fa2e39f7fc2e46513`. The
candidate ABOX5 28-row map is
`e73d89c9637e3cea289c4bc61d999967d9cd20ae33a73bf71bfd13c7439f67ac`; the
rebuilt offloader's five-row map is
`715a469f62a95ab8813c866cb0656f756730475fb80322b1afb4dfcbcfd527ad`. The
reconstructed final provider map has 17,283 rows and sorted-row SHA-256
`0c8e481225fe3ab071ba9be1d14faae06ce8a556fa7e89b07f3dcefb761bd470`:
17,250 retained rows, 28 ABOX5 rows, and five rebuilt offloader rows. The
scan found no missing imports, CRC mismatches, unknown or ambiguous providers,
or symbols with multiple distinct provider CRCs.

The ABOX5 ELF's 28 `__crc_*` values and 28 `__ksymtab_*` names match its
`modules-only.symvers`; Rainbow exports none. The rebuilt offloader's actual
ELF CRC and ksymtab maps match its five post-build rows. I also compared those
exports with the original native-eight offloader: all five names and CRCs are
unchanged.

| Offloader export | CRC in old and rebuilt module |
|---|---:|
| `exynos_usb_audio_exit` | `0x95d3b9ac` |
| `exynos_usb_audio_init` | `0xdbd16bd8` |
| `g_hwinfo` | `0x5ec25df8` |
| `otg_connection` | `0x6fab687a` |
| `usb_audio_connection` | `0x1dcec843` |

This closes the reported stale consumer-import mismatch only for this
three-replacement artifact set and the pinned 329-module native-eight
inventory. It does not explain the ten prior ABOX export CRC changes, establish
that a phone runs this source/artifact set, or prove module loading, kernel
queue ordering, runtime PM, IPC/DMA/PCM progress, or physical audio.

## Stale test-gap record and remaining limits

The report and JSON at `497d680` still state that generic physical-slot reuse
coverage is unresolved (`generic_physical_slot_reuse_test_gap` and its matching
limitation). That text is stale as of test commit
`3da3d065e80d5af7cee9987331f7762373183d36` and independent review
`6b9c1f63c1251f4fc45ee0cb5452b345deb823d4`: the narrow host fixture now reaches
the extracted queue-get path and physical slot-zero reuse, and its overwrite
and clear mutations fail their dedicated checks. The earlier gap is closed
for that host test. This does not establish Linux-kernel queue ordering,
workqueue/RCU behavior, or hardware behavior.

A separate coordinator-provided read-only receipt
(`runtime-host-module-note-correlation-20261003.json`, SHA-256
`1f6c9367702ba99e4699d14e30c958e2db4008bb3c175acb0678315930c6cec4`)
reportedly correlates build IDs for the separate 325-module runtime set to
preserved host module identities. I did not independently review that capture
or receipt here; the coordinator notes it does not attest full in-memory bytes
or loaded file paths. It does not make this 329-module static result a
candidate-phone match or acceptance. Current camera state, candidate
deployment, and hardware/audio acceptance remain unknown.
