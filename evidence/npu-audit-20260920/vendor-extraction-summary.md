# Read-only stock vendor extraction

Source image was supplied as a completed reconstruction: 1,640,783,872 bytes,
SHA256 `6dfe677119792e95a37b016092c327ad62bc0dc7759851424ad05e6bcb31b206`.
The F2FS superblock reports UUID `94ea9115-dc53-43d6-8805-3ac7a34b44d1`.

The host had no F2FS kernel support, so signed Ubuntu `f2fs-tools 1.16.0-1`
was downloaded with `apt-get download` and unpacked with `dpkg-deb -x` into a
temporary directory. `fsck.f2fs -t --dry-run --no-kernel-check` was used only
to enumerate the tree. `dump.f2fs -i INODE` (the package's fsck.f2fs symlink)
copied selected inodes to temporary output directories; no mount, repair, or
write operation touched the source image.

The staged set contains 77 files / approximately 363 MiB:

* ENN/NPU-named libraries and compiler blobs under `rootfs/npu-vendor-assets/enn-libs/`;
* NNC model files under `rootfs/npu-vendor-assets/nnc/`;
* eight named firmware ELF entries (`libann.elf`, `libnn*.elf`, etc.) under
  `rootfs/npu-vendor-assets/firmware-elf/`; their extracted payloads are
  zero-filled/non-ELF data, so they are not usable ELF artifacts;
* `enn_mcd_kernel_64.bin` under `other-firmware/`.

The directory tree contains no exact `NPU.bin` or `vectors.bin`. The initial
dump-only copies were zero-filled because compression was not decoded. The
corrected output is under `rootfs/npu-vendor-assets-decompressed/`: 67 assets,
including NNC files, with 49 valid ELF files. The critical
`libenn_public_api_cpp_lib__7c1` is a valid AArch64 ELF; its dependencies are
recorded in `decompressed-elf-dependencies.txt`, with corrected hashes in
`decompressed-assets.sha256`.
