# Headless recovery derivative

`build_headless_recovery.py` builds `builds/headless_recovery_key.img` from the
verified `lineage/build-20260915/recovery.img`. It does not rebuild or replace
the kernel, DTB, recovery DTBO, or Android userspace. The CPIO stream is copied
record-for-record and only these two records change:

1. `adb_keys`: the dangling symlink to `/product/etc/security/adb_keys` becomes
   a mode-0644 regular file containing the existing host
   `/home/corpunum/.android/adbkey.pub` (SHA-256
   `aa20649485647d31fcb588946e58d8e5580ae145297ae87d70e2aad7ddef6ccb`).
2. `system/etc/init/hw/init.rc`: one `restorecon /adb_keys` action is added to
   recovery `early-init`. Recovery's own rc did not restorecon this path; the
   pinned recovery `plat_file_contexts` maps `/adb_keys` to `system_file`, so
   this requests the expected label before `adbd`; the pinned policy grants
   `adbd` read access to both `system_file` and `adb_keys_file`.

The matching recovery rc already sets `ro.debuggable=1`,
`service.adb.root=1`, `sys.usb.configfs=1`, and `sys.usb.config=adb`, and starts
`/system/bin/adbd --root_seclabel=u:r:su:s0`. The pinned ADB source computes
authentication as `ro.adb.secure && ro.adb.secure.recovery`; this build leaves
both properties unchanged (`ro.adb.secure=1`, recovery default true), so only
the host possessing the matching private key is authorized. No private key is
embedded. No no-auth property is needed; if a future explicitly USB-only
no-auth variant is requested, the narrow recovery property is
`ro.adb.secure.recovery=0` (not `ro.adb.secure=0`, which is broader).

Pinned source revisions used for the rationale are recorded in
`lineage/build-20260915/build-manifest.xml`: `system/core`
`0e96ff13d90df568ddd788bb02b3ddb6e3641528`, `packages/modules/adb`
`e7ed41a85f5a0a8c9db0ed7ba21c6ebd277b5db6`, `bootable/recovery`
`d4ef5569dda1c5066f90efd25455b589ced6073e`, and kernel
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`; matching `system/sepolicy`
is `885cc500f6078a766d1f6def5ce4c06c55841773`.

The build preserves reference header fields: page size 2048, header v2,
kernel load `0x10008000`, ramdisk load `0x11000000`, tags `0x10000100`, DTB
`0x11f00000`, OS `16.0.0`, patch `2026-09`, and cmdline ` bootconfig`.
AVB is the same unsigned (`algorithm NONE`) recovery hash-footer structure,
rollback index 0, partition size 100663296, and the Lineage recovery
fingerprint property. The deterministic footer reuses the reference salt.

`restart2.c` and `build_restart2.sh` produce
`builds/headless_recovery_restart2_aarch64`, a static ARM64 helper accepting
only `recovery` or `download`. It calls Linux `RESTART2` directly with the
literal target consumed by the pinned Samsung `sec_reboot` notifier; it never
writes Android BCB/MISC. Help and invalid-argument behavior were emulated with
`qemu-aarch64-static`; no reboot target was invoked.

## Verified artifacts

```
builds/headless_recovery_key.img             cb8bd4cf1c47027c28278a30ae526667d6ba0fe55bdec6e026e6f2df6de8b654
builds/headless_recovery_key_ramdisk.cpio    dfbedf50dad75a4bda8b8627d4a6e9ac4e8e1bb1c7c3bc83902931aceef13d0b
builds/headless_recovery_key_ramdisk.lz4     2f0e4b06667d62f42e14278e529374d94e78f5c49e5a0d0f0bb8d8a0ffa25318
builds/headless_recovery_restart2_aarch64   082a91f33664f1b57250f92581a32d59d91e4690d4e73756011bac31e76e6441
```

Static checks confirmed a 100663296-byte image; byte-identical kernel, DTB,
and recovery DTBO; regular `/adb_keys` with matching host-key hash; one
`restorecon /adb_keys`; 928 CPIO records with exactly the two intended changed
records; and successful `avbtool verify_image` hash verification.

The unchanged control selector was then run on the phone: Linux RESTART2
selected RECOVERY (`BORE512`, `SOFT INFORM3(12345674)`), and the host received
authenticated root ADB automatically at about 30 seconds. Observed runtime
properties were `ro.adb.secure=1` and an empty `ro.adb.secure.recovery` (the
default is therefore still enforced). The live `ls -lZ /adb_keys` result was
`u:object_r:rootfs:s0`, not the expected `system_file`; do not treat the
restorecon action as live label proof. Key authentication and root ADB worked,
so no image rebuild was needed for this control result.
