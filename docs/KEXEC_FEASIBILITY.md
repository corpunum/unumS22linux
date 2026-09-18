# kexec Feasibility — r0s / s5e9925

Status: **NOT AVAILABLE out of the box. BUILDABLE, unverified.**

## Evidence
Source: `android_kernel_samsung_s5e9925` @ 4e5c5ad7 (lineage-23.2 branch),
`arch/arm64/configs/s5e9925_defconfig`:

```
# CONFIG_KEXEC is not set
# CONFIG_KEXEC_FILE is not set
# CONFIG_CRASH_DUMP is not set
```

`CONFIG_PSTORE*` options ARE enabled (`CONFIG_PSTORE=y`, `CONFIG_PSTORE_RAM=y`,
`CONFIG_PSTORE_CONSOLE=y`, `CONFIG_PSTORE_PMSG=y`), so ramoops-based crash logging is
present in the shipped kernel design even though we can't read it without root
(see evidence/previous_failed_boot/README.md).

## Implication
The RAM-boot development loop described in the master plan (Samsung bootloader →
known-good recovery kernel/userspace → kexec → experimental kernel/DTB/initramfs,
without reflashing on every iteration) is **not usable with the stock or current
Lineage recovery kernel as shipped**. kexec support does not exist in either.

## Path to enable kexec (unverified, not yet attempted)
1. Take the current s5e9925 kernel source + s5e9925_defconfig as a base
2. Enable `CONFIG_KEXEC=y` and `CONFIG_KEXEC_FILE=y` (leave CRASH_DUMP off unless needed)
3. Rebuild the recovery kernel image with these options, keeping every other config
   bit-identical to the current known build (to avoid destabilizing other subsystems)
4. This requires the full recovery boot chain to already work first (Phase 7/8 control
   test), so we have a known-good baseline to diff against
5. Risk: Samsung's S-LK bootloader may itself refuse to jump into a kexec'd kernel
   image the same way, or SELinux/dm-verity/AVB on the *target* kexec'd kernel may add
   friction — untested, no evidence either way

## Recommendation
Do not block early native-Linux bring-up on kexec. Treat the reflash-per-iteration
workflow (via samloader-rs `flash --partition RECOVERY` + immediate VolUp+Power boot
to recovery, avoiding an intervening Android boot) as the default development loop
for now. Revisit kexec once a working native recovery/kernel baseline exists and we
have kernel build infrastructure proven end-to-end.
