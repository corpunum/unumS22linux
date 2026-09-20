# Alpine ARM64 bring-up rootfs

`build-alpine-arm64.sh` downloads the pinned Alpine 3.24.2 `aarch64`
minirootfs, verifies Alpine's published SHA256, installs the initial host-only
userspace, and writes a reproducible tarball under `rootfs/`. The package set is
OpenRC, OpenSSH, iproute2, Python 3, Git, curl, and CA certificates; no desktop
packages are included.

The script copies only `/home/corpunum/.ssh/id_ed25519.pub` into root's
`authorized_keys`. It never reads or includes a private key. Password login is
disabled. OpenRC is configured for DHCP on `eth0` and to start `sshd`.

Run the bounded host build from the project root:

```sh
tools/linux-rootfs/build-alpine-arm64.sh
tools/linux-rootfs/test-alpine-arm64-ssh.sh
```

The second command starts the ARM64 `sshd` under QEMU in a chroot, bound only
to loopback port 22222, and checks a temporary key login as root. Its temporary
private key, host key, QEMU interpreter, and authorized-key addition are removed
on exit. This is a staged userspace/chroot check, not proof of native phone boot.

The build records source and validation details in
`rootfs/alpine-arm64/rootfs-build-manifest.txt` and `qemu-check.txt`; the
artifact size and hash are in `rootfs/alpine-arm64/artifact-manifest.txt`.
