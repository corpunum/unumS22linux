# Cellular closure follow-up — 2026-09-21

This is a host-only, static recovery attempt. No vendor ELF was launched and
no phone, modem, EFS, cpefs, radio, or partition was opened or changed.

## Result

The isolated follow-up root is `rootfs/cellular-closure-followup-20260921/`.
It contains the previously recovered 39-ELF vendor closure and a regenerated
manifest. The six formerly missing system SONAMEs were recovered exactly:

```
android.hidl.safe_union@1.0.so
libexpat.so
libhardware_legacy.so
libnetutils.so
libsqlite.so
libxml2.so
```

Exact hashes are recorded in `manifest.json`; the six are
`dba83db0d80e533a062869ccc10bcf94082e7dcea3a04378e18175d1c4e8c855`,
`1f041db633441ac172cfa363e243090222b61fa3b15e6b93f73cbd56c483d0ec`,
`c68e5495d4214cada0e5bdf08718a0577f254732bbce7952d0eec994e58f873b`,
`73dcb9c2526074a39b6f02133da9cc949b782632f3a99a536102aced5546003a`,
`dd7350c71ffe54c11bda386261c59128af13252e12c921da1b1d790c17b08670`, and
`33181cb058542764f437fe5f3afbfccbb6b2b3c5610d3ccdbb90ceea6bd46f4a` in
the order listed above. The manifest reports 18 available isolated-system
names and zero direct missing dependencies; execution remained false.

## Recovery evidence and blocker

The source was the verified private backup
`/home/corpunum/s22-private-backups/20260920T062514Z/super.img`, SHA-256
`083f6ff378504e4558657517506eb16a186c116230a275339f21098034b36d37`.
Checksum-validated LP metadata produced a 6,654,492,672-byte read-only
dm-linear system view. Regenerated map/tree metadata is in the follow-up root.
The `/system/lib64` entries used the second (lib64) tree occurrence, not the
same-named `/system/lib` entries:

| object | map inode metadata |
| --- | --- |
| android.hidl.safe_union@1.0.so | slots 588479–588481; tree inode 0x1333 |
| libexpat.so | tree inode 0x14bc |
| libhardware_legacy.so | tree inode 0x14e9 |
| libnetutils.so | tree inode 0x1586 |
| libsqlite.so | tree inode 0x1633 |
| libxml2.so | tree inode 0x16ae |

Recovery used `tools/hardware/recover-f2fs-file.py` against that view. Recursive
static resolution adds exact `android.system.suspend-V1-ndk.so` (inode 0x1345,
SHA-256 `5002b7175913f81df0747dd2d73461bef73abcf529192fea87d72139331eddb8`).
The remaining ICU chain was recovered from the exact `com.android.i18n.apex`
(inode 0x51, APEX SHA-256
`118b8c74014226c80c660c05f2d63819622443855af04c7cc0d04325ed4b844f`):
`libandroidicu.so` SHA-256
`127823968609d2d80e8d095e450acf8b3527bbca54fb2c1ea256a8b587861c7a`,
`libicui18n.so` SHA-256
`687109097d7c362986b4c19f6292710c25bfb00e936cfd2d6f19aef77844fb2e`, and
`libicuuc.so` SHA-256
`051e5297d177b38eb1a85cfdebd251f5e096069805eb0e01692cce8c905d2da5`.
A host-only recursive `readelf -d` traversal scanned 64 ELF objects, 61
dependency names, and found zero missing names. The dm/loop view was torn down
after recovery; no filesystem mount or executable launch occurred.

## CP boot and protected NV

A minimal CP boot cannot currently be shown to avoid protected NV/EFS writes.
Static strings in recovered `cbd` explicitly reference `/mnt/vendor/efs/nv_data.bin`,
`/mnt/vendor/efs/nv_5g_data.bin`, `/efs/factory.prop`, NV validation, create,
write, fsync, and removal paths. The RIL init fragment also expects the
matching Samsung service contract and restart targets. Therefore no EFS/cpefs
bypass or empty-NV substitution is proposed. Completing the ELF closure does
not authorize CP boot: any such trial needs a protected-NV write policy and
separate authorization; this host-only work does not establish that gate.

## No-call loader runtime result

After the static gate, the parent-supervised isolated loader trial completed at
20:41:11 UTC with exit status 0 in 1.109 seconds. It loaded
`/vendor/lib64/libsec-ril.so` with `RTLD_NOW` and resolved `RIL_Init`; the
function was not called and `_exit()` was used, with no `dlclose`. The runner
used private mount and network namespaces, UID 1000, zero capabilities,
`NoNewPrivs`, and no proc mount. No modem, binder, EFS, or cpefs device was
exposed. This is a loader/constructor observation only, not CP boot, RIL
service, modem online, or telephony acceptance. The private raw receipt is
under `rootfs/hardware-reuse-20260921/npu-trials/cellular-loader-first/`.

## Reproducibility

The probe source is `tools/hardware/cellular-dlopen-probe.c`; it is fixed to
the recovered `/vendor/lib64/libsec-ril.so` target and performs only `dlopen`,
`dlsym`, flushes, and `_exit`. The accepted binary was built host-side with:

```
rootfs/gpu-compat-20260921/toolchain/android-ndk-r27c/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android31-clang \
  -std=c11 -Wall -Wextra -Werror tools/hardware/cellular-dlopen-probe.c \
  -o rootfs/cellular-loader-20260921/bin/cellular-dlopen-probe -ldl
```

The vendor source closure is `rootfs/cellular-closure-followup-20260921/vendor`;
the exact system objects are under its `system/lib64`, recovered from the
verified FYI3 private `super.img` and the `com.android.i18n.apex` payload.
`tools/hardware/build-cellular-loader-manifests.py` deterministically builds
`manifests/files.json` and `manifests/closure.json`, and now rejects symlinks
and all non-regular files. No new stage or phone interaction is implied by
this documentation update.
