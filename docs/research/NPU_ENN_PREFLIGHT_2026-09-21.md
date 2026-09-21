# ENN loader preflight for S5E9925 (host-only)

Research date: 2026-09-21. This is a non-executing audit of recovered vendor
ELFs. The helper runs host `readelf`/`c++filt` only; it never executes or
relocates an ARM/AArch64 ELF, calls `dlopen`, opens `/dev/vertex10`, accesses a
phone, loads firmware, or calls an ENN/NPU function.

## Reproducible result

This initial asset-only inventory is not the final staged closure. Matching
system libraries were subsequently recovered and the supervised phone loader
resolved all six symbols successfully; see the
[loader trial](NPU_ENN_STAGE_2026-09-21.md). A subsequent no-device initializer
returned zero despite failed NPU/allocator opens; this is not hardware readiness
or inference. The following remains the initial static inventory.

Run from the repository root:

```text
python3 tools/hardware/enn-loader-preflight.py
```

The result is
`rootfs/hardware-reuse-20260921/enn-loader-preflight.json`, SHA-256
`b9c968599f48e803cecdbe05a58cf11a2761c24cd3bb472eb77068aa0daadf47`.
The helper itself is SHA-256
`6b7e3af84b523b8d3c2c077ceceeaa739a803fd34ad8ecc8b60f5bb382610a07`.

The selected recovered API object is AArch64 ELF64:

```text
rootfs/npu-vendor-assets-decompressed/libenn_public_api_cpp_lib__7c1
SHA-256 2d5d7bb1a3df54d224adcd7520a466ce74b578d33e27878bdc1244b003da74a9
```

The preflight found 24 same-ABI ELF assets, 8 staged closure nodes, and 13
unresolved names. The unresolved names include Android/HIDL and allocator
edges (`vendor.samsung_slsi.hardware.enn@1.0.so`, `libhidlbase.so`,
`libhidlmemory.so`, `android.hidl.memory@1.0.so`, `libdmabufheap.so`), Bionic
system names, and `libOpenCL.so`. These are static loader names, not proof
that every library or service is called on every path.

The API record now retains the complete defined dynamic-export set (52
symbols for the selected object) separately from the six fixed ENN API
symbols. Version/device-query candidates are searched in that complete set,
not in the fixed allowlist; none were found. The closure root is explicitly
the selected API pathname, and its SHA-256 is recorded alongside any SONAME
aliases. Different-byte duplicate SONAMEs are reported as ambiguous and are
not traversed silently; this run found none.

The other AArch64 public-wrapper variant is also auditable:

```text
python3 tools/hardware/enn-loader-preflight.py \
  --api rootfs/npu-vendor-assets-decompressed/libenn_public_api_cpp.so__7c0 \
  --output /tmp/enn-preflight-7c0.json
```

It is SHA-256 `1386b7a836e717b9d111861919807c1a07da41a6063d6b3817d14fd630a76e96`,
with SONAME `libenn_public_api_cpp.so`, 3 staged same-ABI closure nodes, and
12 unresolved names. The 32-bit ARM variant (`__537`) must not be mixed into
the AArch64 process.

## Exact API boundary

The selected AArch64 wrapper exports these defined C++ entry points:

- `enn::api::EnnInitialize()` and `EnnDeinitialize()`;
- `EnnGetMetaInfo(_enn_meta_type_id_e, unsigned long, char*)`;
- `EnnOpenModel(char const*, unsigned long*)` and
  `EnnOpenModelFromMemory(char const*, unsigned int, unsigned long*)`;
- `EnnCloseModel(unsigned long)`.

No version/device-query candidate was found in the complete defined export
set. `EnnGetMetaInfo` is the closest apparent metadata entry, but its enum,
output-buffer contract, and handle semantics are not available in the recovered public headers. The only
smallest known runtime entry is `EnnInitialize()`, and the smallest apparent
offline-model entry is `EnnOpenModelFromMemory(...)`; neither is safe to call
in a host preflight because initialization/model open may reach vendor
services, firmware, buffers, or `/dev/vertex10`.

The Samsung ENNDelegate archive audit found only
`include/enn_wrapper_log.h` and `include/enn_wrapper_sq.h`, not the ENN API
structs, metadata enum definitions, VS4L UAPI, or S5E9925 model ABI. The
demangled signatures are therefore observations from ELF symbols, not a usable
replacement for matching headers.

## Smallest future isolated path

If the owner later authorizes a phone experiment, the smallest defensible
runtime path is an Android/Bionic AArch64 process using one exact wrapper
variant and its matching SONAME closure, under a process timeout and private
mount/device namespace. It must first record which unresolved names are
actually resolved/called, then call only `EnnInitialize()` with no model or
buffer operation. A successful initialize would still prove only runtime
initialization; it would not prove NPU firmware, model load, or inference.

Only after exact headers and a model-container contract are recovered could a
separate supervised step consider `EnnOpenModelFromMemory` on a known NCP-v25
payload. The current ENNC/NCP scanner finds structural candidates but does not
establish ENNC payload length, handle ABI, firmware compatibility, or execution.
No fake stubs or guessed metadata buffers should be used.

## Source evidence

- [`runtime-feasibility.md`](../../evidence/npu-audit-20260920/runtime-feasibility.md)
- [`decompressed-elf-dependencies.txt`](../../evidence/npu-audit-20260920/decompressed-elf-dependencies.txt)
- [`enndelegate-archive-audit.txt`](../../evidence/npu-audit-20260920/enndelegate-archive-audit.txt)
- [`enn-loader-preflight.py`](../../tools/hardware/enn-loader-preflight.py)
- [`NPU_REUSE_NEXT_2026-09-21.md`](NPU_REUSE_NEXT_2026-09-21.md)
