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

## Third attempt: radio partition EACCES; NV validation explained
- With the ownership fix, the wake lock works and `umts_boot0` opens. The next
  failure is `std_boot_parse_toc_img: BIN(/dev/block/by-name/radio) open fail
  (Permission denied)`. `/dev/sda17` is `root:1001 0440/0640`, but cbd runs as
  uid 1001 **with gid 0** (setuid only, no setgid/setgroups), so the group bit
  does not apply. Fix: `chown 1001 /dev/sda17; chmod 0400`, read-only.
- `NV validation TIMEOUT` is **not fatal**. The disassembly at 0xee48–0xef1c
  polls `property_get("vendor.cbd.rfs_check_done")` for "1" (normally set by
  the Samsung RIL's RFS service) 50 × 100 ms, then logs TIMEOUT and carries on
  with boot. With no Android property service, it always times out after 5 s.

## AP-side init done: IMEI, no-SIM status and signal over SIPC (same day, later)
- Root cause of the endless `PHONE_START`: cpif `rild_ready()` (link_device.c)
  sends `CMD_INIT_END` only once **both `umts_ipc0` (FMT) and `umts_rfs0` (RFS)
  are open**. Nothing had opened them.
- New `tools/hardware/modem/s22-modem.py` (installed as `s22-modem`) opens
  both and speaks Samsung SIPC FMT frames (`u16 len | mseq | aseq | group |
  index | type | data`). The kernel adds/strips the SIPC5 link header. INIT_END
  was sent at the next PHONE_START. The CP then sent `PWR_PHONE_PWR_UP`,
  `AST_POWERON`, SIM/NET notifications and started streaming 2040-byte RFS
  frames (a ~141 KB CP→AP file write; logged, **not served**; the real EFS is
  untouched).
- Device-verified replies (GET only; no call/SMS/attach request sent):
  - `MISC_ME_VERSION`: modem SW `S901BXXSIFYI3`, HW `REV0.7`, model `SM-S901BZKDEUX`.
  - `MISC_ME_SN`: IMEI `35033005*****13` (15 digits, Luhn-valid; SVN 29).
  - `SEC_SIM_STATUS`: `0x80 CARD_NOT_PRESENT` (correct; no SIM).
  - `NET_REGIST` (JSON payload in this firmware): `act lte, reg_status denied,
    tac 3062, pci 334`. That is limited service without a SIM. The CP powers up
    in normal (RF on) mode by itself.
  - `DISP_RSSI_INFO` notification: LTE RSRP −95 dBm, RSRQ −7 dB, RSSNR 30.0 dB.
    A `GET` returns `{"rssi_level": 66–68}`.
- After the sessions closed, `modem_state` stayed `ONLINE`. There was no CP
  crash and cbd kept running. The kernel no longer logs PHONE_START.
- Many newer messages carry JSON (`{"signal":6,"status":0}`, `scell_status`, …).
  The tool decodes those.
- `tools/hardware/modem/s22-modem-up.sh` (`s22-modem-up [up|stop|status]`) is
  the manual, on-demand bring-up: session-only node ownership, then the cbd
  chroot launch. It adds no boot hook. The `up` path has not yet been re-run
  after a reboot.
- Open: RFS service (CP NV writes go unanswered; a private-copy RFS server is
  the next step), rmnet data (needs SIM + PDP), voice/SMS (owner must name
  numbers first).

## RFS NV service into the PRIVATE EFS copy (2026-10-06/07, lead)
- The unanswered CP→AP frames are libsamsung-ipc RFS `NV_WRITE_ITEM` (cmd 2):
  `u32 len | cmd | id | u32 offset | u32 length | data`. The CP writes its NV image
  (141 590 bytes at offset 0) after INIT_END. The kernel delivers it in 2040-byte
  chunks, which are reassembled by total length.
- `s22-modem --serve-rfs` answers NV read/write. The only file it ever touches
  is `/srv/s22/android-rt/efs/nv_data.bin` (the private copy). The store
  refuses any path outside `/srv/s22/android-rt/`, and the real EFS partition
  is never opened. After each write it rewrites `nv_data.bin.md5` =
  md5(nv_data + "Samsung_Android_RIL"). This scheme was first checked against
  the copy's existing md5, which matched. Each request is bounds-checked
  against 1 MiB.
- Backup taken first: `/srv/s22/state/modem/efs-private-backup-<ts>/`.
- Device run: the first open after cbd was running sent no write. After a manual
  `s22-modem-up stop` + `up` (this also tested the bring-up script; ONLINE
  immediately), the CP sent one NV write. It was served with confirm=1; 2 bytes
  differed from the copy, and the md5 was updated. The CP stayed ONLINE with no
  crash, and the queries afterwards still worked (signal then: RSRP −119 dBm,
  SNR 21 dB, a different cell).
- Host tests: `tools/hardware/test_s22_modem.py` (fragmented write + md5,
  read, out-of-range refusal, real-EFS path refusal, IMEI masking). Added to the CI allowlist.
