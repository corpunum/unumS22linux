//! Status, battery, thermal, process list and kernel log, read straight from sysfs and /proc.
//! Everything is a plain file read: no subprocess, no helper library.

use crate::backends::{Telemetry, TopSort};
use crate::config::Config;
use crate::error::{ApiError, ApiResult};
use serde_json::{json, Value};
use std::fs;
use std::io::Read;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, Instant};

const O_NONBLOCK: i32 = 0o4000;

pub struct SysTelemetry {
    cfg: Arc<Config>,
    /// How long `top` waits between its two CPU samples.
    pub sample: Duration,
}

fn read(p: &Path) -> Option<String> {
    fs::read_to_string(p).ok().map(|s| s.trim().to_string())
}

fn num<T: std::str::FromStr>(p: &Path) -> Option<T> {
    read(p)?.parse().ok()
}

fn sorted_dirs(dir: &Path, prefix: &str) -> Vec<PathBuf> {
    let mut v: Vec<PathBuf> = fs::read_dir(dir)
        .map(|rd| {
            rd.filter_map(|e| e.ok())
                .filter(|e| e.file_name().to_string_lossy().starts_with(prefix))
                .map(|e| e.path())
                .collect()
        })
        .unwrap_or_default();
    v.sort();
    v
}

fn meminfo_kb(text: &str, key: &str) -> Option<u64> {
    text.lines()
        .find(|l| l.starts_with(key))?
        .split_whitespace()
        .nth(1)?
        .parse()
        .ok()
}

fn disk_mb(path: &Path) -> Option<Value> {
    let c = std::ffi::CString::new(path.to_str()?).ok()?;
    let mut st: libc::statvfs = unsafe { std::mem::zeroed() };
    if unsafe { libc::statvfs(c.as_ptr(), &mut st) } != 0 {
        return None;
    }
    let unit = st.f_frsize as u64;
    let mb = |blocks: u64| blocks * unit / (1 << 20);
    Some(json!({"total_mb": mb(st.f_blocks as u64), "avail_mb": mb(st.f_bavail as u64)}))
}

impl SysTelemetry {
    pub fn new(cfg: Arc<Config>) -> Self {
        Self {
            cfg,
            sample: Duration::from_millis(250),
        }
    }

    fn sys(&self, rel: &str) -> PathBuf {
        self.cfg.root.join(rel)
    }

    fn battery_value(&self) -> Option<Value> {
        let dir = sorted_dirs(&self.sys("sys/class/power_supply"), "")
            .into_iter()
            .find(|d| read(&d.join("type")).as_deref() == Some("Battery"))?;
        let status = read(&dir.join("status"));
        Some(json!({
            "name": dir.file_name()?.to_string_lossy(),
            "percent": num::<u32>(&dir.join("capacity")),
            "status": status,
            "charging": status.as_deref().map(|s| s == "Charging"),
            "health": read(&dir.join("health")),
            "temp_c": num::<f64>(&dir.join("temp")).map(|t| t / 10.0),
            "voltage_mv": num::<i64>(&dir.join("voltage_now")).map(|v| v / 1000),
            "current_ua": num::<i64>(&dir.join("current_now")),
        }))
    }

    fn zones(&self) -> Vec<Value> {
        sorted_dirs(&self.sys("sys/class/thermal"), "thermal_zone")
            .into_iter()
            .filter_map(|d| {
                let temp = num::<f64>(&d.join("temp"))? / 1000.0;
                Some(json!({
                    "zone": d.file_name()?.to_string_lossy(),
                    "type": read(&d.join("type")).unwrap_or_default(),
                    "temp_c": (temp * 10.0).round() / 10.0,
                }))
            })
            .collect()
    }

    fn proc_ticks(&self) -> Vec<(u32, String, u64, u64)> {
        let mut out = vec![];
        let page_kb = (unsafe { libc::sysconf(libc::_SC_PAGESIZE) }.max(4096) as u64) / 1024;
        let Ok(rd) = fs::read_dir(self.sys("proc")) else {
            return out;
        };
        for e in rd.filter_map(|e| e.ok()) {
            let Ok(pid) = e.file_name().to_string_lossy().parse::<u32>() else {
                continue;
            };
            let Some(stat) = read(&e.path().join("stat")) else {
                continue;
            };
            let (Some(open), Some(close)) = (stat.find('('), stat.rfind(')')) else {
                continue;
            };
            let f: Vec<&str> = stat[close + 1..].split_whitespace().collect();
            let ticks = f.get(11).and_then(|v| v.parse::<u64>().ok()).unwrap_or(0)
                + f.get(12).and_then(|v| v.parse::<u64>().ok()).unwrap_or(0);
            let rss_kb = read(&e.path().join("statm"))
                .and_then(|s| s.split_whitespace().nth(1)?.parse::<u64>().ok())
                .unwrap_or(0)
                * page_kb;
            out.push((pid, stat[open + 1..close].to_string(), ticks, rss_kb));
        }
        out
    }
}

/// Records from `/dev/kmsg` (or a file in the same format): `pri,seq,usec,flags;message`.
pub fn parse_kmsg(text: &str) -> Vec<Value> {
    text.lines()
        .filter(|l| !l.starts_with(' ') && !l.is_empty())
        .filter_map(|l| {
            let (head, msg) = l.split_once(';')?;
            let mut it = head.split(',');
            let pri: u32 = it.next()?.parse().ok()?;
            let seq: u64 = it.next()?.parse().ok()?;
            let usec: u64 = it.next()?.parse().ok()?;
            Some(json!({
                "seq": seq,
                "ts_s": usec as f64 / 1e6,
                "level": pri & 7,
                "msg": msg,
            }))
        })
        .collect()
}

impl Telemetry for SysTelemetry {
    fn status(&self) -> ApiResult {
        let uptime = read(&self.sys("proc/uptime"))
            .and_then(|s| s.split_whitespace().next()?.parse::<f64>().ok());
        let load: Vec<f64> = read(&self.sys("proc/loadavg"))
            .map(|s| {
                s.split_whitespace()
                    .take(3)
                    .filter_map(|v| v.parse().ok())
                    .collect()
            })
            .unwrap_or_default();
        let cpus = sorted_dirs(&self.sys("sys/devices/system/cpu"), "cpu")
            .iter()
            .filter(|d| {
                let n = d
                    .file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .to_string();
                n.len() > 3 && n[3..].chars().all(|c| c.is_ascii_digit())
            })
            .count();
        let mem = read(&self.sys("proc/meminfo")).map(|t| {
            json!({
                "total_mb": meminfo_kb(&t, "MemTotal:").map(|k| k / 1024),
                "available_mb": meminfo_kb(&t, "MemAvailable:").map(|k| k / 1024),
            })
        });
        let disk: Vec<Value> = ["", "srv/s22"]
            .iter()
            .filter_map(|m| {
                let mut v = disk_mb(&self.sys(m))?;
                v["mount"] = json!(format!("/{m}"));
                Some(v)
            })
            .collect();
        let mut net = serde_json::Map::new();
        for d in sorted_dirs(&self.sys("sys/class/net"), "") {
            let name = d.file_name().unwrap().to_string_lossy().to_string();
            if name != "lo" {
                net.insert(name, json!(read(&d.join("operstate"))));
            }
        }
        let zones = self.zones();
        let hottest = zones
            .iter()
            .filter_map(|z| z["temp_c"].as_f64())
            .fold(None, |m: Option<f64>, t| Some(m.map_or(t, |m| m.max(t))));
        let level = num::<u32>(&self.cfg.volume_file);
        let brightness = num::<u32>(&self.cfg.backlight_dir.join("brightness"));
        Ok(json!({
            "ok": true,
            "hostname": read(&self.sys("proc/sys/kernel/hostname")),
            "kernel": read(&self.sys("proc/sys/kernel/osrelease")),
            "uptime_s": uptime,
            "load": load,
            "cpus": cpus,
            "cpu_load": if cpus > 0 { load.first().map(|l| (l / cpus as f64 * 100.0).round() / 100.0) } else { None },
            "mem": mem,
            "disk": disk,
            "battery": self.battery_value(),
            "thermal_max_c": hottest,
            "network": net,
            "modem": read(&self.sys("sys/devices/platform/cpif/modem_state")),
            "display": {"state": read(&self.cfg.display_state), "brightness": brightness},
            "audio": {"level": level, "muted": level.map(|l| l == 0)},
            "keepalive": self.cfg.keepalive_enabled.exists(),
        }))
    }

    fn battery(&self) -> ApiResult {
        match self.battery_value() {
            Some(mut b) => {
                b["ok"] = json!(true);
                Ok(b)
            }
            None => Err(ApiError::unavailable("no power_supply of type Battery")),
        }
    }

    fn thermal(&self) -> ApiResult {
        let zones = self.zones();
        if zones.is_empty() {
            return Err(ApiError::unavailable("no readable thermal zones"));
        }
        let hottest = zones
            .iter()
            .max_by(|a, b| {
                a["temp_c"]
                    .as_f64()
                    .partial_cmp(&b["temp_c"].as_f64())
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .cloned();
        Ok(json!({"ok": true, "zones": zones, "hottest": hottest}))
    }

    fn top(&self, sort: TopSort, limit: usize) -> ApiResult {
        let tck = unsafe { libc::sysconf(libc::_SC_CLK_TCK) }.max(1) as f64;
        let before = self.proc_ticks();
        let t0 = Instant::now();
        std::thread::sleep(self.sample);
        let after = self.proc_ticks();
        let dt = t0.elapsed().as_secs_f64();
        if after.is_empty() {
            return Err(ApiError::unavailable("/proc is not readable"));
        }
        let mut rows: Vec<(f64, u64, Value)> = after
            .iter()
            .map(|(pid, comm, ticks, rss)| {
                let prev = before.iter().find(|b| b.0 == *pid).map(|b| b.2).unwrap_or(*ticks);
                let cpu = if self.sample.is_zero() || dt <= 0.0 {
                    0.0
                } else {
                    (ticks.saturating_sub(prev)) as f64 / tck / dt * 100.0
                };
                let cmdline = fs::read(self.sys(&format!("proc/{pid}/cmdline")))
                    .map(|b| {
                        String::from_utf8_lossy(&b).replace('\0', " ").trim().chars().take(120).collect::<String>()
                    })
                    .unwrap_or_default();
                (
                    cpu,
                    *rss,
                    json!({"pid": pid, "name": comm, "cpu_pct": (cpu * 10.0).round() / 10.0, "rss_mb": *rss as f64 / 1024.0, "cmd": cmdline}),
                )
            })
            .collect();
        match sort {
            TopSort::Cpu => rows.sort_by(|a, b| {
                b.0.partial_cmp(&a.0)
                    .unwrap_or(std::cmp::Ordering::Equal)
                    .then(b.1.cmp(&a.1))
            }),
            TopSort::Mem => rows.sort_by_key(|r| std::cmp::Reverse(r.1)),
        }
        let list: Vec<Value> = rows.into_iter().take(limit).map(|r| r.2).collect();
        Ok(
            json!({"ok": true, "sort": if sort == TopSort::Cpu { "cpu" } else { "mem" }, "processes": list, "total": after.len()}),
        )
    }

    fn dmesg(&self, since: Option<u64>, limit: usize) -> ApiResult {
        let path = self.sys("dev/kmsg");
        let mut f = fs::OpenOptions::new()
            .read(true)
            .custom_flags(O_NONBLOCK)
            .open(&path)
            .map_err(|e| ApiError::unavailable(format!("{}: {e}", path.display())))?;
        let mut text = String::new();
        let mut buf = vec![0u8; 8192];
        loop {
            match f.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => text.push_str(&String::from_utf8_lossy(&buf[..n])),
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                Err(e) if e.raw_os_error() == Some(libc::EPIPE) => continue,
                Err(e) => return Err(ApiError::unavailable(format!("kmsg read: {e}"))),
            }
            if text.len() > 8 << 20 {
                break;
            }
        }
        let mut lines = parse_kmsg(&text);
        if let Some(s) = since {
            lines.retain(|l| l["seq"].as_u64().unwrap_or(0) > s);
        }
        let truncated = lines.len() > limit;
        if truncated {
            lines.drain(..lines.len() - limit);
        }
        let last = lines.last().and_then(|l| l["seq"].as_u64());
        Ok(json!({"ok": true, "lines": lines, "last_seq": last, "truncated": truncated}))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::Tmp;

    fn fixture() -> (Tmp, SysTelemetry) {
        let t = Tmp::new();
        t.write("sys/class/power_supply/battery/type", "Battery\n");
        t.write("sys/class/power_supply/battery/capacity", "83\n");
        t.write("sys/class/power_supply/battery/status", "Charging\n");
        t.write("sys/class/power_supply/battery/temp", "312\n");
        t.write("sys/class/power_supply/battery/voltage_now", "4012000\n");
        t.write("sys/class/power_supply/usb/type", "USB\n");
        t.write("sys/class/thermal/thermal_zone0/type", "cpu-big\n");
        t.write("sys/class/thermal/thermal_zone0/temp", "45123\n");
        t.write("sys/class/thermal/thermal_zone1/type", "gpu\n");
        t.write("sys/class/thermal/thermal_zone1/temp", "51000\n");
        t.write("sys/class/net/wlan0/operstate", "up\n");
        t.write("sys/class/net/lo/operstate", "unknown\n");
        t.write("sys/devices/system/cpu/cpu0/x", "");
        t.write("sys/devices/system/cpu/cpu1/x", "");
        t.write("sys/devices/system/cpu/cpufreq/x", "");
        t.write("proc/uptime", "1234.5 999.0\n");
        t.write("proc/loadavg", "1.00 0.50 0.25 1/100 99\n");
        t.write(
            "proc/meminfo",
            "MemTotal: 8192000 kB\nMemAvailable: 4096000 kB\n",
        );
        t.write("proc/sys/kernel/hostname", "s22\n");
        t.write(
            "proc/10/stat",
            "10 (llama-server) S 1 1 1 0 -1 0 0 0 0 0 500 100 0 0\n",
        );
        t.write("proc/10/statm", "100 25000 0 0 0 0 0\n");
        t.write("proc/10/cmdline", "llama-server\0-m\0/models/x.gguf\0");
        t.write(
            "proc/11/stat",
            "11 (a b) c) S 1 1 1 0 -1 0 0 0 0 0 5 5 0 0\n",
        );
        t.write("proc/11/statm", "10 100 0 0 0 0 0\n");
        t.write("srv/s22/buttons/volume", "0\n");
        t.write("run/s22-display-state", "on\n");
        t.write("sys/class/backlight/panel/brightness", "128\n");
        t.write("srv/s22/state/keepalive/enabled", "");
        let mut s = SysTelemetry::new(Arc::new(Config::rooted(t.path())));
        s.sample = Duration::ZERO;
        (t, s)
    }

    #[test]
    fn status_summarises_files() {
        let (_t, s) = fixture();
        let v = s.status().unwrap();
        assert_eq!(v["uptime_s"], 1234.5);
        assert_eq!(v["cpus"], 2);
        assert_eq!(v["cpu_load"], 0.5);
        assert_eq!(v["mem"]["total_mb"], 8000);
        assert_eq!(v["battery"]["percent"], 83);
        assert_eq!(v["battery"]["charging"], true);
        assert_eq!(v["battery"]["temp_c"], 31.2);
        assert_eq!(v["thermal_max_c"], 51.0);
        assert_eq!(v["network"]["wlan0"], "up");
        assert!(v["network"].get("lo").is_none());
        assert_eq!(v["audio"]["muted"], true);
        assert_eq!(v["display"]["state"], "on");
        assert_eq!(v["keepalive"], true);
        assert_eq!(v["hostname"], "s22");
    }

    #[test]
    fn missing_sources_degrade_to_null_not_error() {
        let t = Tmp::new();
        let s = SysTelemetry::new(Arc::new(Config::rooted(t.path())));
        let v = s.status().unwrap();
        assert!(v["battery"].is_null());
        assert!(v["uptime_s"].is_null());
        assert_eq!(v["keepalive"], false);
        assert_eq!(s.battery().unwrap_err().code, "unavailable");
        assert_eq!(s.thermal().unwrap_err().code, "unavailable");
        assert_eq!(s.dmesg(None, 10).unwrap_err().code, "unavailable");
        assert_eq!(s.top(TopSort::Cpu, 5).unwrap_err().code, "unavailable");
    }

    #[test]
    fn battery_and_thermal() {
        let (_t, s) = fixture();
        let b = s.battery().unwrap();
        assert_eq!(b["voltage_mv"], 4012);
        assert_eq!(b["name"], "battery");
        let t = s.thermal().unwrap();
        assert_eq!(t["zones"].as_array().unwrap().len(), 2);
        assert_eq!(t["hottest"]["type"], "gpu");
    }

    #[test]
    fn top_sorts_and_handles_parens_in_comm() {
        let (_t, s) = fixture();
        let by_mem = s.top(TopSort::Mem, 5).unwrap();
        let p = by_mem["processes"].as_array().unwrap();
        assert_eq!(p[0]["name"], "llama-server");
        assert_eq!(p[0]["cmd"], "llama-server -m /models/x.gguf");
        assert_eq!(p[1]["name"], "a b) c");
        assert_eq!(
            s.top(TopSort::Cpu, 1).unwrap()["processes"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
    }

    #[test]
    fn dmesg_filters_by_seq_and_limits() {
        let (t, s) = fixture();
        t.write(
            "dev/kmsg",
            "6,100,1000000,-;first\n SUBSYSTEM=x\n4,101,2500000,-;second\n3,102,3000000,-;third\n",
        );
        let all = s.dmesg(None, 10).unwrap();
        assert_eq!(all["lines"].as_array().unwrap().len(), 3);
        assert_eq!(all["lines"][1]["level"], 4);
        assert_eq!(all["lines"][1]["ts_s"], 2.5);
        let since = s.dmesg(Some(100), 10).unwrap();
        assert_eq!(since["lines"][0]["msg"], "second");
        let last = s.dmesg(None, 1).unwrap();
        assert_eq!(last["lines"][0]["msg"], "third");
        assert_eq!(last["truncated"], true);
        assert_eq!(last["last_seq"], 102);
    }
}
