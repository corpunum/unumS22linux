# Persistence preparation and private backups

The backup helpers **do not erase, format, flash, decrypt or mount storage**.
Separate deployment/startup helpers now populate and mount already-formatted
userdata; none formats or flashes a partition. All helpers are specific to
the already-verified SM-S901B native-recovery environment
and existing pinned SSH identity. Do not generalize device paths to another phone.

## Raw backup

Run from the original host checkout:

```sh
python3 tools/persistence/backup-phone.py
```

The source helper is supplied to Python over the existing SSH connection, not
installed on the phone. It validates native PID1, exact partition names,
capacity, major/minor device numbers, absence of holders and absence of mounts
in the shell and PID1 mount views. It opens block devices with `O_RDONLY`.

The host creates a fresh mode-0700 directory below
`/home/corpunum/s22-private-backups/`. Partition files contain Android data,
key metadata and device identity. **Never publish this directory or its raw
contents to GitHub.** A failed/interrupted run leaves `.partial` files; it
does not silently resume, delete, overwrite or count them as completed backups.

Lossless gzip compresses transport. Entire zero chunks become sparse-file
holes on the host, preserving exact logical bytes and size. Each completed
image is fsynced and checked three ways:

1. SHA256 computed while the phone reads the original bytes;
2. independent host SHA256 of the decoded incoming stream;
3. another host SHA256 pass over the saved image.

Successful images become mode 0400 and receive individual `.verified.json`
records. The final `raw-backup-complete.json` is written only after all planned
partitions pass. This is a sequential, per-partition capture, **not an atomic
cross-partition snapshot**. Android services must stay stopped throughout.
No MISC, PIT, bootloader or TrustZone write is involved. CACHE is deliberately
excluded because the running native overlay writes there; its Linux backup is
a separate private artifact documented in `docs/OMARCHY_TRIAL.md`.

Capture device identity and boot generation during and after the same session:

```sh
python3 tools/persistence/capture-backup-identity.py /home/corpunum/s22-private-backups/SESSION during
python3 tools/persistence/capture-backup-identity.py /home/corpunum/s22-private-backups/SESSION after
```

Replace `SESSION` with the directory the backup created. Identity receipts may
contain a serial number; they are private, not public evidence. The `after`
receipt refuses a changed boot generation. It cannot retroactively certify
atomicity or quiescence of a different environment.

## Critical limitation

Raw userdata is ciphertext. Matching hashes prove transfer/storage integrity,
not that Android's hardware-wrapped keys still decrypt personal files or that
apps can be restored. The receipts deliberately say:

```json
{"decrypted_file_backup": false, "file_restore_verified": false, "permits_erasure": false}
```

Do not turn this result into permission to erase. A readable export through
an Android-capable environment may require the owner's local unlock. Do not
ask the owner to disclose their PIN. Alternatively, the owner must explicitly
accept the risk of having only an unproven encrypted recovery snapshot.

The persistent Arch candidate is a separate host-only preparation described
in `persistent-arch-candidate-20260920.md`. Its existence does not satisfy
the backup, deployment, boot or hardware-acceptance gates.

## Installed persistent desktop

The owner accepted raw-only data-loss risk; userdata was separately converted
to ext4. See `docs/PERSISTENCE_MIGRATION_2026-09-20.md` for the historical
operation and tests. Never rerun filesystem creation on the installed phone.

`deploy-persistent-files.py` is a fresh-empty-target deployment helper. It
requires the validated filesystem already mounted at `/srv/s22`, transfers
hash-pinned local artifacts, and refuses an existing installation. It includes
the signed host-installed libbsd/libmd/inotify-tools fixup; the base archive
alone was insufficient for the keyboard. `verify-deployment.py` checks the
349-package closure and core hashes before exclusively creating the deployment
marker. These are not update-in-place commands.

The cache-overlay `/usr/local/bin/start-weston-native` dispatches to
`start-persistent-desktop` when `/etc/s22-persistent-enabled` exists. A separate
`/etc/s22-persistent-disabled` flag or `S22_RESCUE=1` selects the Alpine rescue
desktop. SSH/guardian remain independent. The supervisor requires the exact
userdata UUID, never formats or repairs, mounts volatile runtime directories,
starts the CPU model and actual Omarchy UI, and cleans its own processes and
mounts on stop. Ordinary startup failure falls back to Weston; a second
concurrent launch refuses without taking over the display.

After physical confirmation of a clean screen, `/etc/s22-linear-stride-enabled`
opts the desktop into the narrow S22 linear-stride workaround. The native
guardian, SSH and model process are not preloaded. Set
`S22_LINEAR_STRIDE_TRIAL=0` for a controlled no-preload launch, or move the
marker aside before restarting. See `../omarchy-trial/s22-linear-stride.md`.
The old installed supervisor is preserved as `.pre-stride`; do not remove
the installed workaround library while the marker is enabled.

Native pacman's signature-helper hang was reproduced before a transaction.
Package DB checks work, but native signed installation is NOT accepted yet.
The fixup was verified/installed on the host and transferred with its package
records; signature checking was not disabled. Do not invoke arbitrary native
package transactions until this separate kernel/runtime issue is resolved.
