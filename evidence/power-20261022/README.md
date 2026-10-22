# Power saving and suspend-to-RAM — 2026-10-22

Phone locked (fastshell Power-key lock, real DPMS off) the whole time, lying on its usual spot,
USB-C attached to a host that supplies VBUS and the ECM link (that host is the ROG Ally, not the rig;
the rig cannot see the phone on USB). Times are about 02:20–04:05 EEST.

## How drain was measured

- **USB was taken out of the picture with Samsung's slate mode**:
  `echo 1 > /sys/class/power_supply/battery/batt_slate_mode`. It votes the charger to BUCK_OFF
  (source: `drivers/battery/common/sec_battery_sysfs.c`), so the battery feeds the whole phone and
  `current_now` is the real drain. The status reads `Discharging`. USB data, ECM and SSH stayed up.
  It was set back to 0 at the end, and the phone charges again (`Charging`, +364 mA).
- **Awake windows:** `current_now` (mA; the Samsung sign is negative for discharging) every 5 s,
  sampled by `tools/hardware/power/s22-power-sample.py`.
- **Suspend windows:** the sampler cannot run while the phone sleeps. These windows use the fuel
  gauge's `batt_read_raw_soc`, which has 0.01 % steps. 1 step is about 0.33 mAh on
  `charge_full` = 3327 mAh.
  - **Cross-check:** on the same awake window (`C-awake-control.csv`), the raw-SOC slope gave 160 mA and `current_now` gave 172 mA.
  - **Error:** the suspend-window numbers are good to about ±10 %.
- `charge_counter` is SOC-derived. Its units don't match mAh (7.1 M at 85 %), so it is not used.

## Results

| Condition (locked, screen off, battery only) | Window | Drain | %/h |
|---|---|---|---|
| Baseline: schedutil, all services as found | 15.0 min | **192 mA** mean (182 median) | **5.8 %/h** |
| + `powersave` governor on all 3 clusters | 6.9 min | 171 mA | 5.1 %/h |
| Control repeat (powersave, no autosleep) | 10.0 min | 172 mA (raw SOC: 160 mA) | 5.2 %/h (4.8 by SOC) |
| **s22-autosleep** (deep suspend cycles, Wi-Fi + Tailscale stay reachable) | 20.4 min | **36 mA** avg | **1.1 %/h** |
| s22-autosleep, earlier windows | 21.2 / 7.7 min | 32 / 41 mA | 1.0 / 1.2 %/h |

So the gain is about **5.3× less drain**. At 1.1 %/h a full battery would last about 90 h locked. That figure is extrapolated, not measured.
For reference, screen on with USB charging the phone showed −156 mA net (`pre-on-usb.csv`), with the host supplying about 104 mA (`iin` in dmesg).

**Time asleep:** 89 % of wall time on the clean run (`cycle3`: 18 cycles, 1087 s of 1221 s, 2 aborts).
Most cycles end after **113 s**. The cause is a Tailscale disco/WireGuard UDP packet from the rig (192.168.50.253:41641) that matches the Wi-Fi WoW pattern (`PATTERN_MATCH_FOUND`). The wake packet was decoded from the qcacld dump; its payload starts `54 53 f0 9f 92 ac` ("TS💬").
That is the price of keeping SSH over Tailscale reachable while asleep.

**What was awake before:** `wakers-base-locked.txt` shows 0.9 % CPU busy and 280 context switches/s.
- **Top wakers:** schedutil's `fast_switch_pos` (82/s; gone under powersave), rcu, ACPM log, tailscaled, the BT HCI thread, node (OpenUnum), s22-phoned and unumsearch.
- **D-state threads:** the 8 `tz_worker_threa` plus `tz_iwlog_thread`/`tz_iwsock` (TrustZone), `ufs_perf_0` and `chub_log_kthrea` sit in D state and hold load at about 14. They use no CPU and do not block suspend.
- **Kernel wakeup sources:** with the screen off, the only active ones were `dwc3-otg` and `usb_notify` (the USB cable). The display's (`drmdsim`/`drmdecon`) went away on DPMS off.
- **cpuidle:** each CPU has only WFI and a CPU power-down state, about 90 % residency when idle.

## Suspend bring-up (stock Samsung kernel, no Android)

`/sys/power/state` = `freeze mem`, and `mem_sleep` = `s2idle [deep]`. The kernel has `PM_WAKELOCKS` but no autosleep.

| Log | Step | Result |
|---|---|---|
| `suspend-…235051.log` | `pm_test=freezer` | ok |
| `suspend-…235152.log` | `pm_test=devices` | ok: WLAN enters WoW, MHI M3, PCIe, UFS, USB, sensors suspend and resume in 137/130 ms |
| `suspend-…235251.log` | `pm_test=platform` | ok (`umts_ipc1 rx_demux -19` noise: nobody reads that channel) |
| `suspend-…235308.log` | `pm_test=processors` | ok ("Disabling non-boot cpus failed" is the test stop point, Samsung's abort-reason logger) |
| `suspend-…235325.log` | `pm_test=core` | first try: `cnss_pci` -EAGAIN (WLAN busy) |
| `suspend-…235406.log` | `pm_test=core` | retry ok: `exynos_pm_syscore_suspend` mode 8 |
| `suspend-…235429.log` | **real deep suspend** | AP SoC down (apsoc 0→1), MIF down ×5, woken by `wlan_pci_wake_irq`; health all ok |

Wake sources:

| Source | Status |
|---|---|
| Wi-Fi WoW (`wlan_pci_wake_irq`) | **Verified**, dozens of times. SSH over Tailscale to the sleeping phone connected in **5.6 s** |
| RTC alarm | **Verified**: 3 of 6 `once 6` runs woke via `SYSINT vGPIO2PMU_EINT1` → `s2mps25-irq` after 5.5 s (`rtc-once2.json`); the other 3 were woken by Wi-Fi first |
| Modem | **Not verified on its own.** `cp_mbox_irq_0` is pending on every resume. The modem stayed ONLINE, registered and SMS-ready across about 60 cycles. No incoming SMS or call was tried (none may be sent) |
| Power key | **Not verified.** It shares the PMIC interrupt path that the RTC wake used, and `s2mps25-power-keys` has `power/wakeup` = enabled. Nobody was there to press it |

**Health after every test** (`s22-power-health.sh`):
- Wi-Fi up, with the gateway answering ping;
- `tailscale ping` to the rig;
- modem ONLINE, SIM READY, SMS ready, registered on LTE;
- ABOX card and PCMs present;
- hci0 present;
- fastshell running;
- `stm_ts` evdev present;
- USB ECM `configured`.

After about 60 suspend cycles, `s22-touchd power` turned the panel on: `dpms on: Ok(())`, panel frame-done, governor back to schedutil. It then turned it off again.
No keepalive restart happened during the whole session. The last restart was before the work started.

**Not tested:**
- audio playback after resume (no sound at night);
- physical touch;
- BT traffic;
- the voice loop;
- a reboot with keepalive starting `autosleep` itself. (Done later on 2026-10-22: it started about 30 s after `s22-reboot recovery`, see `evidence/npu-custom-20261022/reboot-check.txt`.)

## Mistake made and fixed

`cycle1` ran under busybox `timeout 420`, and that `timeout` did not stop the daemon when its time ran out. `cycle2` therefore ran with two daemons racing each other: the cycle counts are double-counted, though the energy figure is still valid.
`s22-autosleep run` now takes a `flock` on `/run/s22-autosleep/lock` and exits if another daemon holds it.

## Files

| File | What it holds |
|---|---|
| `summary.txt` | every number above, recomputed from the raw files |
| `pre-on-usb.csv` | screen on, charging |
| `base-locked.csv` | baseline |
| `A-powersave.csv` | powersave governor |
| `C-awake-control.csv` | control run, sampled every 5 s |
| `C-awake-contaminated.csv` | discarded: autosleep daemons were still running |
| `wakers-*.txt` | per-thread context switches/s and CPU over 45–60 s |
| `suspend-*.log` | pm_test ladder and first real suspend |
| `autosleep.jsonl` | every daemon cycle: slept_s, wake IRQ, raw SOC |
| `cycle*.meta` | start time and raw SOC for each autosleep window |
| `rtc-once*.json` | RTC wake tests |
