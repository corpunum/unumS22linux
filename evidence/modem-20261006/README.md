# Modem boot attempt — 2026-10-06

## Run (owner-approved; the coordinator ran it once in the main session)
- `cbd` (CBD-V3-20220120R1) ran from the minimal Android runtime at
  `/srv/s22/android-rt`, using the bionic linker from the recovery ramdisk,
  the stock args `-d -t ss310 -P by-name/radio -bm -mm -B umts_boot0
  -D umts_ramdump0 -n /mnt/vendor/efs -o s -o v`, and a **private EFS copy**
  (the real EFS is never written).
- It retried every ~6 s with `NV validation TIMEOUT`, then
  `boot_wake_lock: user wake_lock open fail (Permission denied)` and
  `DEV(/dev/umts_boot0) open fail (Permission denied)`. modem_state stayed
  `INIT`. The coordinator stopped it.

## Root cause (read-only analysis)
- The cpif open handler (`bootdump_open`) has no caller check, and SELinux
  is permissive. The chroot's /dev and /sys are binds without `nodev`, and
  the process had full capabilities (CapEff/CapBnd 0x1ffffffffff).
- `cbd` drops root by itself. The disassembly at 0x142f0–0x14330 shows
  `prctl(PR_SET_KEEPCAPS,1)`, then `setuid(1001 /* AID_RADIO */)`, then
  `capset`. On Android, ueventd and init have already made its resources
  accessible to radio:
  - vendor ueventd: `/dev/umts* 0660 system radio`,
    `/dev/umts_ramdump0 0660 radio radio`;
  - AOSP init: `/sys/power/wake_lock` and `/sys/power/wake_unlock` are radio-writable.
- On the phone all `/dev/umts*` nodes are `root:root 0600` and wake_lock is
  `root 0644`, so uid 1001 gets EACCES. The process gid is still 0 (it was not
  started with init's group list), so group ownership alone is not enough.
- The EFS copy already has Android ownership (`nv_data.bin` 1001:1001). The
  runtime root `/srv/s22/android-rt` is 1000:1000 0775 (tar kept host uids)
  and should be root:root 0755.

## Proposed next single command (needs the owner; modem action)
See the lead report. It sets session-only ownership (`/dev/umts_*`
→ 1001:1001 0660; wake_lock/wake_unlock → 1001; radio partition `/dev/sda17`
→ group 1001 0640, read-only; cbd log dir → 1001) and relaunches cbd once.
Rollback: `pkill -f vendor/bin/cbd`, or `s22-reboot recovery` (it restores
the device-node modes).

## Second command (ownership fix + cbd): no crash; it never ran
- There was **no reboot or panic.** The current boot started at 19:24:17 UTC
  with my own `s22-reboot recovery` (deploying c72b3d6). That is the
  "uptime 10 min at 19:34". The saved last_kmsg of the previous boot ends with
  `s22-restart2 … sec_reboot (0, recovery)` (my deliberate reboot), with no
  panic and no cbd lines.
- In the current boot, `cbd.out` was written 266 s after boot (19:28); that is the
  first run. After the second command, `/dev/umts_*`, `/dev/sda17`,
  `/sys/power/wake_lock` and `/srv/s22/android-rt` all still have their old
  ownership. The second command died at its first step: `pkill -f
  vendor/bin/cbd` matches the remote `sh -c` command line that contains the same
  string, so it killed its own shell. That explains why there was no output at all.
  (I made the same mistake earlier with `s22-buttons`.)
- Load ~14 is the steady state seen since day 1: D-state kernel threads
  (`tz_worker_threa` ×8, `tz_iwsock`, `ufs_perf`, …); CPU is 98% idle. It is not
  a runaway and not the close_range bug.
- Health: Hyprland, model, s22-buttons, sound card, display on, and wlan0 up.
