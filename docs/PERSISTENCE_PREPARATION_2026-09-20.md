# Persistence preparation — 2026-09-20

The owner asked to back up Android, remove it and make Linux permanent, then
said to continue. This work advances backups and host preparation; it does
not reinterpret that request as consent to silently lose unrecoverable data.

## Backup state

Private raw backup session:
`/home/corpunum/s22-private-backups/20260920T062514Z/`.
This directory is outside the public repository, mode 0700. Completed images
are mode 0400; logs/receipts are mode 0600. Do not upload its contents.

The source reads exact allowlisted, size-checked, inactive partitions using
`O_RDONLY`. There are no phone file writes, mounts, reboot commands, key
operations, formatting or flashing in the backup helpers. Individual captures
are sequential, not an atomic whole-phone snapshot. Native guardian remains
running; private identity receipts bind the capture to the pinned SSH identity
and kernel boot generation.

An initial uncompressed attempt was stopped deliberately after roughly 14 GiB
of userdata transfer. Its partial image and completed small images are retained
in the earlier `20260920T061900Z` directory and are not treated as a complete
snapshot. Compression was selected after read-only samples showed large zero
regions, not because userdata was assumed empty. The replacement uses lossless
gzip in transit and exact sparse zero regions in the host file.

The replacement backup finished successfully. At 06:41 UTC the parent verified
`raw-backup-complete.json`: **15 partitions, 126859476992 logical bytes**
(118.15 GiB), with no partial images in the completed session. Every partition
passed the source-stream, host-stream and independent saved-file SHA256
comparisons, including userdata's full **113283956736 bytes**. Sparse storage
uses approximately 14 GiB on the host without discarding logical zero bytes.

The after-capture identity receipt confirmed the same kernel boot generation
as the during-capture receipt. The parent checked every manifest record,
image size and 0400 image mode, then fsynced all session files and the session
and parent directories. This is a verified raw capture, not a tested restore.

Matching hashes establish raw-byte integrity, **not** decrypted personal-file
restorability. The filesystem is protected by Android metadata/FBE encryption
and hardware-wrapped keys. There is no decrypted mapper in the native system.
Read-only inspection of the metadata image found its normal `vold` and
`password_slots` directories; no key contents were printed or modified.
Its ext4 journal-recovery flag was preserved, not repaired in place.

No deletion should proceed until a readable file export is verified, or the
owner explicitly accepts the possibility that the encrypted raw snapshot
cannot restore their personal files. A local Android unlock may be required;
the PIN must not be sent to the assistant.

## Clean Arch candidate

Host-only candidate: `rootfs/persistent-arch-candidate-20260920/`.
It uses the existing pinned Arch/Omarchy sources, the successfully tested
GCC16 Aquamarine override and canonical HTTP chat client. Unlike the earlier
RAM file-payload trial, the added Squeekboard/Python/GNOME dependencies were
installed into this candidate through its signed-package workflow.

- 346 registered packages; worker `pacman -Dk` reports no DB errors.
- Worker `pacman -Qk` reports no missing files.
- Parent independently checked **80813 registered paths**: none missing.
- Parent verified the chat hash matches canonical `s22-chat-http.py`, the
  Aquamarine artifact hash, and the real Omarchy shell symlink.
- Local `/opt` overrides/configs are identified separately, not misrepresented
  as package-owned files.

See the candidate's [provenance](../tools/persistence/persistent-arch-candidate-20260920.md)
and [verification output](../tools/persistence/persistent-arch-candidate-20260920-verification.txt).

This is not an installed system. Persistent mounting, model/desktop supervision,
fallback handling, recovery reboot tests and a separate cold-boot design remain
to be implemented and accepted after the data-preservation gate. No Android
partition has been erased and no new phone image has been flashed.

The final 06:42 UTC read-only phone check still showed native guardian and BORE
518 (RECOVERY), with Hyprland, foot, Squeekboard and Quickshell running. The
resident model's loopback health endpoint returned `ok`; battery temperature
was 38.4 C after the backup load. No reboot occurred during this work.
