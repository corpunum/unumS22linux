# Userdata Linux migration — 2026-09-20

## Authority and preservation

After the completed private raw backup and an explicit warning about encrypted
personal-file recovery, the owner replied: "i accept possible loss of android
data". This resolves the backup-risk decision for the requested userdata
conversion. It does not authorize bootloader, PIT, EFS, IMEI or TrustZone
changes. No raw backup or private identity receipt belongs in GitHub.

Before formatting, all 15 saved images/records were present with their expected
sizes and 0400 permissions. A fresh read-only phone inventory matched the
backup inventory exactly, including partition starts/sizes/device identities,
no holders and no mounts. Kernel boot generation matched the final backup
receipt. Original Lineage recovery and native V3 image hashes were rechecked.

## Destructive operation completed

Only PARTNAME `userdata`, resolved as `/dev/sda36` (259:20), was formatted.
Start: 28594176 sectors; length: 221257728 sectors / 113283956736 bytes.
The partition table was not changed. Android's previous userdata filesystem
has been replaced; file-level recovery from the encrypted raw image remains
unproven, as accepted by the owner.

Signed Alpine e2fsprogs 1.47.4-r0 was installed in the existing rescue overlay.
After a non-writing `mke2fs -n` test, the actual creation used:

```text
mke2fs -t ext4 -b 4096 -i 65536 -m 0 -L S22_LINUX
  -U 1dd55c26-bd57-489a-9d9b-4c60e6f430eb
  -O none,has_journal,ext_attr,resize_inode,dir_index,filetype,extent,64bit,flex_bg,sparse_super,large_file,huge_file,dir_nlink,extra_isize,metadata_csum
  -E nodiscard,lazy_itable_init=0,lazy_journal_init=0 /dev/sda36
```

This is a historical command, not a reinstallation instruction. **Do not run
it again: it would destroy the new Linux installation.**

Creation and `e2fsck -fn` both exited 0. Explicit feature selection excludes
the newer `orphan_file` feature; the phone's raw superblock confirmed 4096-byte
blocks, UUID above and no orphan_file bit. The kernel mounted it at `/srv/s22`
with `rw,noatime,errors=remount-ro`, reporting about 104.5 GiB available.

One pre-mount check failed harmlessly because BusyBox `blkid` does not implement
util-linux's `-s UUID -o value` output contract. The full output was parsed
instead; no formatting retry was needed or performed.

## Persistent recovery startup accepted

The base archive (963065234 bytes, SHA256
`363f836b6d45673a26e117493b034038d7b845c092c2883039bd8128cda67646`),
2B model and resident server were transferred with matching phone hashes.
Native checks confirmed all 346 initial packages and 80813 registered paths,
clean `pacman -Dk`, canonical chat, server, model and Aquamarine hashes.

The first real frame exposed a missing keyboard ELF dependency, `libbsd.so.0`.
The package DB check had not established runtime closure. Attempting a native
signed install reproduced the previously recorded signature-helper hang,
before an ALPM transaction: child 20831 stayed runnable with SIGKILL pending,
open-file limit 1024. No signature policy was weakened. Host-side signed
installation of libbsd, libmd and inotify-tools supplied an exact file/package-DB
delta. Its SHA256 is
`5d3eeb6d054b3a4ed320a208f44bb51b7be0145133ce86e9b0d27db91dd36841`.
After deployment and native `ldconfig`, all four relevant package file checks
passed and the keyboard's ELF dependency listing had no missing libraries.
There are now 349 registered packages. The native install hang remains open.

The cache overlay now contains a separate Python desktop/model supervisor,
with the original Weston launcher saved as `start-weston-native.pre-persistence`.
The dispatch is enabled by `/etc/s22-persistent-enabled`; guardian/SSH are
unchanged. Strict UUID/partition/deployment validation precedes mounting.
The Arch root and models are ext4-backed; `/dev`, `/run`, `/tmp` and `/dev/shm`
are deliberately volatile. Only needed device nodes and read-only proc/sys,
udev, DNS and model paths are bound into the Arch session. The model listener
is loopback-only. Startup failures retain SSH and fall back to Weston.

Initial actual-device session worked; a deliberately stopped model was
automatically restarted with the desktop PID unchanged. Five focused mocked
supervisor tests and Python/shell syntax checks passed. These are focused
checks, not exhaustive service or hardware coverage.

After enabling the hook and syncing, a software-only `s22-reboot recovery`
advanced BORE to **519**. Without any host rootfs/model restoration, native
guardian, ext4 mounts, Hyprland, foot, Squeekboard, Quickshell and the model
returned automatically. Readiness was observed at 15.81 seconds kernel uptime;
the stable sample at 77.59 seconds showed the same boot and healthy model.
Synthetic taps on the actual event7 keyboard typed and submitted `hi`; the
visible response took 1.4 seconds, first text 0.6 seconds. This proves the
downstream touch-to-chat path, not physical finger sensing.

The reboot cleared the stuck signature helper. Its exact empty stale pacman
lock was removed only after confirming no live pacman process; DB check passed
again. Available RAM with the persistent desktop/model was about 4.7 GiB,
versus roughly 1.6 GiB in the old RAM-staged installation. The saved source
archives are retained on userdata; about 100 GiB remains free.

Evidence: `evidence/persistence-20260920/`, especially
`recovery-autostart-stable.json` and `persistent-after-reboot-chat.png`.

## Normal BOOT: authorized, not yet accepted

The owner separately approved modifying BOOT while retaining RECOVERY as
rescue. At approximately 07:48 UTC, BOOT was written and read back with SHA256
`4aeb801486e35e2c71dab0e988c6ac14802b05a29d062ddd93834e74802b5b2e`.
RECOVERY was unchanged at
`1a827b43d29141efb47f530902dd4e4ee2b6d780515893c9ecd676ad27efd7d1` and
vendor_boot was unchanged at
`383b6f6789e655b070929914db5639e6d9bacc78823ada7bf553962b5b4888be`.

The normal reboot began at 07:48:18 UTC. By 07:56 UTC, SSH, ADB, and
Download-USB had not returned. Boot mode and runtime are therefore
unconfirmed; this is not a normal-BOOT acceptance. The last accepted working
state is BORE519 recovery with automatic persistent Omarchy/model startup,
15.81-second readiness, and a stable 77-second sample. The host reproduced a
mixed LZ4/gzip compression-boundary decoder failure, but no phone-side boot
evidence is available yet. Android services
were absent in the last accepted recovery state, not proven absent during this
normal-BOOT attempt.
