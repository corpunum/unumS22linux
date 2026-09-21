# Isolated ENN loader: successful no-call phone trial

Date: 2026-09-21. Host preparation was followed by one supervised phone
loader trial at 17:24:55 UTC. The real ENN library loaded and all six API
symbols resolved: exit 0 in 2.709 seconds. No ENN function was called and no
NPU/DRM/binder/dma-heap node was exposed. This is a loader milestone, not
NPU initialization, firmware boot, model loading or inference. No vendor ELF
was executed on the host.

A separate 17:33:53 UTC init-only trial returned status 0 **despite failing to
open the intentionally absent NPU and allocator nodes**. It establishes the
runtime's direct-device path, not NPU readiness; see the final section.

## Staged root

The new isolated root is
`rootfs/npu-compat-20260921/`; accepted GPU runtime directories were not
modified. It contains the exact AArch64 ENN wrapper family selected by the
static preflight, copies of the already accepted Bionic/GPU baseline system
libraries, and the recovered vendor HIDL library:

```text
bin/enn-dlopen-probe
vendor/lib64/libenn_public_api_cpp_lib.so
vendor/lib64/libenn_user_lib.so
vendor/lib64/libenn_common_utils.so
vendor/lib64/libenn_engine_lib.so
vendor/lib64/libenn_model.so
vendor/lib64/libenn_user_driver_cpu.so
vendor/lib64/libenn_user_driver_gpu_lib.so
vendor/lib64/libenn_user_driver_unified.so
vendor/lib64/vendor.samsung_slsi.hardware.enn@1.0.so
```

The exact vendor HIDL object was recovered with the existing read-only
`recover-vendor-closure.py` path from the read-only vendor image. Its SHA-256
is `dcddddb8fdaffe68efe7613bdec036c27304f382e4d9e5b7517b769ae2069d1c`.
All staged-file hashes and sizes are in
`rootfs/npu-compat-20260921/manifests/files.json`, regenerated after the
system-library recovery; SHA-256
`c50b658fcf83a4fa8691e269b6b39f3bc2b71e39179b4241e4c1ec3f2fbc1129`.

The host-only recursive DT_NEEDED audit is also persisted in
`manifests/closure.json` (34 ELF objects scanned, 23 reachable dependency
names, zero missing and zero ambiguous SONAMEs). It confirms that the actual
staged `vendor/lib64/libOpenCL.so` is reachable through the ENN closure; it is
not merely listed as a previously unresolved name. The probe interpreter is
`/system/bin/linker64`, and the stage contains no fail-fast abort shims.

The closure now passes the bounded loader trial, and the previous three missing
AArch64 system objects are now recovered from the verified FYI3 private
`super.img` system logical partition and staged without substitution:

```text
system/lib64/android.hidl.memory@1.0.so
  inode 0x1332, 124192 bytes
  sha256 de8eed0f41f6b17c9eaf6bd40e6a4c9379bbccc5c6da70ac7191039fa14628b8
system/lib64/libdmabufheap.so
  inode 0x1498, 86000 bytes
  sha256 65651e4463e10d89a05751e003994a2c75a56742a227fa91f438426f1bcdfe8b
system/lib64/libhidlmemory.so
  inode 0x1501, 27672 bytes
  sha256 53370a96e8ef9d05816909b8b61c6a2862892c120f94060a1a019b1fa1109869
system/lib64/android.hidl.memory.token@1.0.so
  inode 0x1331, 69920 bytes
  sha256 fe66cd5ea90495d0c90f613167af0486900bd23fa9752d8b170a55e4174cfa7b
```

The source snapshot is `/dev/sda30` from the verified backup, SHA-256
`083f6ff378504e4558657517506eb16a186c116230a275339f21098034b36d37`, with
build identity `S901BXXSIFYI3`. Its logical-partition metadata passed all
geometry/header/table checks; the `system` filesystem is F2FS and was exposed
through a temporary read-only dm-linear extent map. Only the four named files
were recovered. `readelf` confirms ELF64 AArch64 and the expected SONAMEs.
The static DT_NEEDED names still do not prove which objects are called on the
initialize path; the parent-supervised phone trace must establish that.

### Additional extracted-source boundary

Before the backup route, the same three exact filenames were checked by
individual-file search in
`stock/FYI3/extracted/unpacked_recovery/ramdisk_extracted` and the matching
Lineage unpacked recovery tree. No candidate files were found there, and no
`readelf` candidate existed to inspect. The stock tar/zip images were not
opened, streamed, or unpacked. The separate verified FYI3 `super.img` route was
then used through the bounded logical-partition metadata parser and temporary
read-only dm-linear extent map described above; no whole userdata image or
archive was copied. Thus the earlier absence was limited to the inspected
extracted trees and recovered vendor image; the verified FYI3 system partition
is now the separate source for the four recovered objects above.

## Probe binary

`tools/hardware/enn-dlopen-probe.c` was built with the verified Android NDK
r27c AArch64 Bionic compiler:

```text
aarch64-linux-android31-clang -std=c11 -Wall -Wextra -Werror \
  tools/hardware/enn-dlopen-probe.c \
  -o rootfs/npu-compat-20260921/bin/enn-dlopen-probe -ldl
```

The resulting ELF is AArch64 PIE and depends only on `libdl.so` and `libc.so`.
SHA-256: `70383afb4f3aaa110624a8e6b2a26f8a82b6297e4355175d7ab9cd8e904bf25d`.

It prints and flushes a stage before `dlopen(RTLD_NOW|RTLD_LOCAL)` and before
each `dlsym` lookup for the six exact ENN C++ symbols. It never calls a found
symbol and intentionally does not call `dlclose`; vendor constructors run as
part of `dlopen`, so the parent must supervise this attempt and provide no
NPU/DRM/dma-heap/binder device exposure. A successful result means only that
the loader resolved those symbols; it is not ENN initialization, model load,
firmware boot, or NPU inference.

The phone-side attempt was parent-supervised. It used the matching
Bionic linker and exact wrapper SONAMEs, recorded the unresolved-library and
constructor trace, and stopped at this no-call probe. The later independent
initialization trial below used a separately reviewed no-argument ABI and root.
Metadata/model/buffer calls still require their exact headers and contracts;
they were not added to either probe.

## Parent-reviewed phone runner

The separate host-only staging and supervision helpers are:

```text
tools/hardware/stage-enn-phone.py
tools/hardware/exec-enn-isolated.py
tools/hardware/run-enn-trial.py
tools/hardware/verify-enn-stage.py
```

`stage-enn-phone.py --plan` performs no phone access. When explicitly invoked,
it refuses to overwrite `/srv/s22/npu-compat-20260921`, transfers only this
reviewed stage, and verifies every manifest hash remotely. The isolated runner
requires that exact root, mounts only a read-only `/proc` when requested, and
creates a private `/dev` containing `null`, `zero`, `random`, and `urandom`
only. The private `/dev` intentionally does not use `MS_NODEV`, so those four
explicit character nodes remain usable; no other node is created. It exposes
no DRM, NPU, DMA-heap, binder, display, or input nodes, drops
UID/GID to 1000, clears capabilities, and sets `NoNewPrivs=1` before executing
the probe.

The supervisor records the full private before/after `dmesg`, phone health,
stage hashes, bounded `strace` file/ioctl/connect/clone activity, boot identity,
and kernel delta. It has no retry or reboot path. Original loader-trial helper
hashes (later expanded for the separate init root):

```text
exec-enn-isolated.py  7d6a8f29a3176d0e29e9783fb5c625284b9872b1bff84cb51fb12e3f361696d7
stage-enn-phone.py    a329ed8ea818317ea3c19eacdabccd9ba6d142f9dc0c0037147bca9e20fbd7a7
run-enn-trial.py      42acf3142f0da1be8aec98a21101d004efe62a836443e471eaaa5ac3e749724b
verify-enn-stage.py   8065fa873fd7d930a0a2dc1de076210e43e0c8c15a6b179ef43fa65f39926fa7
```

## Phone result

The stage transferred 36 hash-verified files to a new userdata directory.
The runner printed UID/GID 1000, zero capabilities and `NoNewPrivs=1`, then
successfully loaded `libenn_public_api_cpp_lib.so` with `RTLD_NOW` and resolved
all six symbols. Static closure covered 23 reachable SONAMEs with no missing
or ambiguous dependency; no abort stubs were staged.

The trace records the real ENN, HIDL and OpenCL libraries loading. Android
property files and `/dev/socket/logdw` were absent; no service was started.
The missing generated `/linkerconfig/ld.config.txt` produced a warning, not
a failure. No new GPU/NPU kernel messages were captured. Boot identity was
unchanged and the resident 4B service remained healthy at 30.2 C.

Private logs: `rootfs/hardware-reuse-20260921/npu-trials/loader-first/`.
Public, identity-free receipt:
[`npu-loader.json`](../../evidence/hardware-reuse-20260921/npu-loader.json).
The next distinct gate is the exact no-argument initialization ABI and its
service/device requests, still without guessing model or buffer structures.

## Initialization-only ABI evidence

The separate source-only probe is
`tools/hardware/enn-init-probe.c`. It hard-codes the staged wrapper path
`/vendor/lib64/libenn_public_api_cpp_lib.so`, resolves only
`_ZN3enn3api13EnnInitializeEv`, calls it once, prints the signed status, and
calls `_exit()` without `EnnDeinitialize`, model, buffer, device, or ioctl
operations directly. The parent reviewed and executed it in a separate
`/srv/s22/npu-init-20260921` root, preserving the original loader stage.

The exact wrapper object is SHA-256
`2d5d7bb1a3df54d224adcd7520a466ce74b578d33e27878bdc1244b003da74a9`.
At wrapper `.text` offset/address `0x3060`, the four-byte instruction is an
unconditional branch to `EnnInitialize@plt`; at `0x3064`, the corresponding
four-byte instruction branches to `EnnDeinitialize@plt`. In the matching
`libenn_user_lib.so` (SHA-256
`8c60e7af00e9f98ac8e7079feccf5cabe8f7ce4c3fb6925e43fcaf9edd21de90`),
`EnnInitialize` at `0x15bc4` saves no incoming arguments, calls
`EnnContextManager::init`, and returns its status through `w0`; the normal
success path also returns `w19` through `w0`. `EnnDeinitialize` at `0x1ada4`
likewise takes no arguments and returns a status through `w0`. These are
disassembly-backed observations of the matching binaries, not a substitute for
the missing vendor header.

The initialization path allocates/registers ENN context, engine, memory
manager, and medium-interface state. Static inspection found no direct
`open`, `ioctl`, `connect`, or `socket` imports in the matching user/engine
objects, but indirect service behavior remains unproven. `_exit()` is used
after the flushed result to avoid vendor atexit/fini cleanup; the supervisor's
deadline remains responsible for termination if initialization blocks.

## Initialization trace: zero return is not hardware readiness

At 17:33:53 UTC, `init-first` exited 0 in 2.385 seconds and printed
`result=enn_initialize status=0 deinitialize=none`. The 37-file stage had
manifest SHA256
`2caadc6d0bba4fc866d132a83d5753cffec58dd3f2628863de56f8fc67e1da60`;
probe SHA256
`f19984676d7801bc324a37ea4d1976f819de1d9c1e1e00e2dabe7606cd9e51ce`.
The initializer source hash is
`a6ff5a06c19fa4f89a0424711544020cb49a4876f44233ea137483da0ae30547`.
This exact isolated-helper hash is
`1bc99740573140a2860837d7ea4b8b6c8d1af613625ef49bb21bd80224252189`.

The trace exposes the important contract:

- `/dev/ion`, `/dev/dma_heap/system-uncached` and `/dev/dma_heap/system`
  were requested read-only and returned `ENOENT`.
- `/vendor/etc/enn/custom_mode_config.json` was absent.
- `/dev/vertex10` was requested read-only and returned `ENOENT`.
- Logging/property resources were absent; no Android service was started.

No accelerator node was exposed or opened successfully, and no NPU ioctl or
firmware boot occurred. The API's zero return therefore cannot be a readiness
gate. This provides direct-device evidence for this ENN variant; DT_NEEDED
HIDL libraries do not prove that initialization requires binder services.
Model-load/execute paths may have different dependencies and are untested.

Same boot, resident CPU4B healthy, 30.2 C, no new GPU/NPU kernel messages.
Public receipt: [`npu-initialize.json`](../../evidence/hardware-reuse-20260921/npu-initialize.json).
Next is a source-matched allocator/NPU open-and-close lifecycle and firmware
audit, before any device exposure; model/buffer ABI and numerical inference
remain separate gates. Do not infer readiness from the zero status.

### Kernel open versus firmware-boot boundary

The pinned defconfig enables `CONFIG_NPU_USE_BOOT_IOCTL` and
`CONFIG_NPU_USE_HW_DEVICE`. `npu_vertex_open()` (`npu-vertex.c:202-277`)
obtains the device reference, opens a session and initializes queues. First
open reaches `npu_device_open()` (`npu-device.c:554-646`): this configuration
excludes the power/protocol/late-open block, but still changes software
scheduler/QoS state and allocates a session. Final close uses the corresponding
system/QoS/scheduler/memory cleanup (`npu-device.c:833-862`). It is not a
side-effect-free metadata read, and no real open/close was tested here.

Explicit VS4L BOOTUP crosses the firmware/hardware boundary:
`npu-vertex.c:1614-1634`, `npu-device.c:726-780`, and
`npu-system.c:1660-1758` lead to resume, firmware/interface startup, DSP state
and protocol initialization. Therefore neither a successful library call nor
an eventual simple device open would prove firmware execution. Do not expose
the node to the ENN initializer until subsequent ioctls, firmware, memory and
teardown are individually reviewed. Source SHA:
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`.
