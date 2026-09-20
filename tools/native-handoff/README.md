# Native first-stage handoff

This directory contains a narrowly scoped derivative of the verified Lineage
recovery ramdisk. It preserves the shipping /init symlink and AOSP first
stage. The shipping regular /system/bin/init is renamed to
/system/bin/init.android; init-wrapper delegates every invocation to it
except selinux_setup when the build-time /native-enable marker exists.

native-guardian becomes PID 1 only in that explicit native mode. It owns the
watchdog and starts native-start. Native startup failures before the grace
period can fall back to the original AOSP selinux_setup; a live native session
is never transitioned to Android merely because the host did not ACK within
120 seconds. The marker is intentionally host-created; merely reaching an SSH
listen state is not accepted as proof of remote control. After the grace
period, native-start failures restart the SSH rescue without returning to
Android.

The default builder mode keeps the Alpine archive on the writable cache
filesystem as /cache/native-rootfs.tar.gz. This is required for the current
32,493,352-byte archive because embedding it in the 96 MiB recovery image does
not fit with the existing 67 MiB payload. An embedded mode is available and
fails closed if the resulting recovery image exceeds the partition size.

No device operation is performed by the builder. It only writes artifacts
under builds/native_handoff_*.

## Optional cache-persistent variant

Current status (2026-09-20): this variant is now used by native V3 and has
passed real recovery reboots, persistent-file checks and whole-partition
readback. See `docs/NATIVE_LINUX.md`; the design notes below predate those tests.

`native-start-persistent` is an unembedded, opt-in script variant. It extracts
the same embedded Alpine xz archive into a RAM lower layer, then attempts an
OverlayFS root with ordinary files only under:

`/cache/s22-linux/upper`

`/cache/s22-linux/work`

The workspace parent and `work` are mode `0700`; `upper` is mode `0755`
because its mode becomes the visible Linux root directory mode.
The resulting root also mounts a separate 256 MiB tmpfs at `/tmp`, so runtime
temporary files cannot fill the persistent upper layer. If
`/usr/local/bin/start-weston-native` exists and is executable, it is launched
after networking and independently of SSH; output is written to
`/run/weston-native.log`.

It mounts only the kernel-derived `/dev/block/by-name/cache` alias as ext4;
there is no `sdaN` fallback, formatting, raw block write, userdata, EFS, or
other partition access. Existing rootfs contents remain the lower layer, while
SSH host keys, package installs, and other writes go into `upper`.

If cache mounting, workspace validation, or OverlayFS setup fails, the script
binds the already-extracted lower layer as a RAM-only root and continues to
ECM/SSH. It does not fall back to Android for a persistence failure. This
variant was subsequently flashed and verified as native V3 (BORE 517/518).

An already-mounted `/cache` is accepted only when `/proc/mounts` identifies the
same kernel-derived cache block node; the script never remounts an unrelated
mountpoint. Overlay setup is a single attempt: it never deletes or empties an
existing `work` directory to recover from stale state, and falls back to RAM
if OverlayFS rejects it.

The pinned kernel evidence has `CONFIG_OVERLAY_FS=y`, `CONFIG_EXT4_FS=y`,
`CONFIG_EXT4_FS_POSIX_ACL=y`, `CONFIG_EXT4_FS_SECURITY=y`, and
`CONFIG_FS_POSIX_ACL=y`; ext4's xattr implementation is unconditional in the
pinned `fs/ext4/Makefile`. `CONFIG_TMPFS_XATTR` is not required because the
OverlayFS upper/work directories are on cache ext4, not tmpfs.
