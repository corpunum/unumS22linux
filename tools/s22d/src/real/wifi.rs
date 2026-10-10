//! Wi-Fi through `wpa_cli`, the way the phone's own `wifi-autostart.py` runs it:
//! `wpa_cli -p /run/wpa_supplicant-s22 -i wlan0 <command>`.
//!
//! The saved profile lives in `wifi-private/wpa_supplicant.conf` with `update_config=0`, so
//! everything done here is runtime-only. Restarting the supplicant returns to the saved profile.

use super::runner::{Output, Runner};
use crate::backends::{Wifi, WifiConnect};
use crate::config::Config;
use crate::error::{ApiError, ApiResult};
use axum::http::StatusCode;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};

pub struct WpaCli {
    cfg: Arc<Config>,
    runner: Arc<dyn Runner>,
    pub scan_wait: Duration,
    pub poll: Duration,
    pub connect_timeout: Duration,
}

pub fn parse_kv(text: &str) -> HashMap<String, String> {
    text.lines()
        .filter_map(|l| l.split_once('='))
        .map(|(k, v)| (k.trim().to_string(), v.trim().to_string()))
        .collect()
}

pub fn parse_scan_results(text: &str) -> Vec<Value> {
    let mut nets: Vec<Value> = text
        .lines()
        .skip(1)
        .filter_map(|l| {
            let f: Vec<&str> = l.split('\t').collect();
            if f.len() < 5 || f[4].is_empty() {
                return None;
            }
            let flags = f[3];
            Some(json!({
                "ssid": f[4],
                "bssid": f[0],
                "freq_mhz": f[1].parse::<u32>().ok(),
                "signal_dbm": f[2].parse::<i32>().ok(),
                "secured": flags.contains("WPA") || flags.contains("WEP") || flags.contains("SAE"),
                "flags": flags,
            }))
        })
        .collect();
    nets.sort_by_key(|n| std::cmp::Reverse(n["signal_dbm"].as_i64().unwrap_or(-200)));
    nets
}

fn hex(s: &str) -> String {
    s.bytes().map(|b| format!("{b:02x}")).collect()
}

pub fn validate(req: &WifiConnect) -> Result<(), ApiError> {
    if req.ssid.is_empty() || req.ssid.len() > 32 || req.ssid.chars().any(|c| c.is_control()) {
        return Err(ApiError::bad_request(
            "ssid must be 1-32 bytes without control characters",
        ));
    }
    if let Some(p) = &req.password {
        if !(8..=63).contains(&p.len()) || !p.chars().all(|c| (' '..='~').contains(&c)) {
            return Err(ApiError::bad_request(
                "password must be 8-63 printable ASCII characters (omit it for an open network)",
            ));
        }
    }
    Ok(())
}

impl WpaCli {
    pub fn new(cfg: Arc<Config>, runner: Arc<dyn Runner>) -> Self {
        Self {
            cfg,
            runner,
            scan_wait: Duration::from_secs(3),
            poll: Duration::from_secs(1),
            connect_timeout: Duration::from_secs(25),
        }
    }

    fn cli(&self, args: &[&str]) -> Result<Output, ApiError> {
        let mut a: Vec<String> = vec![
            "-p".into(),
            self.cfg.wpa_ctrl_dir.display().to_string(),
            "-i".into(),
            self.cfg.wifi_iface.clone(),
        ];
        a.extend(args.iter().map(|s| s.to_string()));
        let o = self
            .runner
            .run(&self.cfg.wpa_cli, &a, Duration::from_secs(10))
            .map_err(|e| ApiError::unavailable(format!("wpa_cli: {e}")))?;
        if o.timed_out {
            return Err(ApiError::timeout("wpa_cli did not answer"));
        }
        Ok(o)
    }

    /// A command that must answer `OK`.
    fn cli_ok(&self, args: &[&str]) -> Result<(), ApiError> {
        let o = self.cli(args)?;
        if o.success() && o.stdout.trim() == "OK" {
            Ok(())
        } else {
            Err(ApiError::upstream(format!(
                "wpa_cli {} answered {:?}",
                args[0],
                o.stdout.trim()
            )))
        }
    }

    fn status_map(&self) -> Result<HashMap<String, String>, ApiError> {
        let o = self.cli(&["status"])?;
        if !o.success() {
            return Err(ApiError::unavailable(format!(
                "wpa_supplicant not reachable: {}",
                o.stderr.trim()
            )));
        }
        Ok(parse_kv(&o.stdout))
    }
}

impl Wifi for WpaCli {
    fn availability(&self) -> Option<String> {
        let sock = self.cfg.wpa_ctrl_dir.join(&self.cfg.wifi_iface);
        if sock.exists() {
            None
        } else {
            Some(format!(
                "wpa_supplicant control socket missing: {}",
                sock.display()
            ))
        }
    }

    fn status(&self) -> ApiResult {
        let st = self.status_map()?;
        let sig = self
            .cli(&["signal_poll"])
            .ok()
            .filter(|o| o.success())
            .map(|o| parse_kv(&o.stdout))
            .unwrap_or_default();
        let int =
            |m: &HashMap<String, String>, k: &str| m.get(k).and_then(|v| v.parse::<i64>().ok());
        Ok(json!({
            "ok": true,
            "state": st.get("wpa_state"),
            "connected": st.get("wpa_state").map(|s| s == "COMPLETED").unwrap_or(false),
            "ssid": st.get("ssid"),
            "bssid": st.get("bssid"),
            "ip": st.get("ip_address"),
            "freq_mhz": int(&st, "freq"),
            "rssi_dbm": int(&sig, "RSSI"),
            "link_mbps": int(&sig, "LINKSPEED"),
            "network_id": int(&st, "id"),
        }))
    }

    fn scan(&self) -> ApiResult {
        // A scan may be refused while one is running; the results below are still valid.
        let _ = self.cli(&["scan"])?;
        std::thread::sleep(self.scan_wait);
        let o = self.cli(&["scan_results"])?;
        if !o.success() {
            return Err(ApiError::unavailable(format!(
                "scan_results failed: {}",
                o.stderr.trim()
            )));
        }
        Ok(json!({"ok": true, "networks": parse_scan_results(&o.stdout)}))
    }

    fn connect(&self, req: &WifiConnect) -> ApiResult {
        validate(req)?;
        let previous = self.status_map()?.get("id").cloned();
        let o = self.cli(&["add_network"])?;
        let id = o.stdout.trim().to_string();
        if !o.success() || id.parse::<u32>().is_err() {
            return Err(ApiError::upstream(format!("add_network answered {id:?}")));
        }
        let setup = (|| {
            self.cli_ok(&["set_network", &id, "ssid", &hex(&req.ssid)])?;
            match &req.password {
                Some(p) => self.cli_ok(&["set_network", &id, "psk", &format!("\"{p}\"")])?,
                None => self.cli_ok(&["set_network", &id, "key_mgmt", "NONE"])?,
            }
            self.cli_ok(&["select_network", &id])
        })();
        let result = setup.and_then(|_| {
            let deadline = Instant::now() + self.connect_timeout;
            loop {
                let st = self.status_map()?;
                if st.get("wpa_state").map(String::as_str) == Some("COMPLETED")
                    && st.get("id").map(String::as_str) == Some(id.as_str())
                {
                    return Ok(st);
                }
                if Instant::now() >= deadline {
                    return Err(ApiError::new(
                        StatusCode::BAD_GATEWAY,
                        "connect_failed",
                        format!(
                            "did not associate within {}s",
                            self.connect_timeout.as_secs()
                        ),
                    ));
                }
                std::thread::sleep(self.poll);
            }
        });
        match result {
            Ok(st) => Ok(
                json!({"ok": true, "ssid": req.ssid, "ip": st.get("ip_address"), "network_id": id}),
            ),
            Err(e) => {
                // Put the phone back where it was: select_network disabled every other profile.
                let _ = self.cli(&["remove_network", &id]);
                let _ = self.cli(&["enable_network", "all"]);
                if let Some(prev) = previous {
                    let _ = self.cli(&["select_network", &prev]);
                }
                Err(e)
            }
        }
    }

    fn disconnect(&self) -> ApiResult {
        self.cli_ok(&["disconnect"])?;
        Ok(json!({"ok": true, "note": "use /v1/wifi/reconnect to rejoin"}))
    }

    fn reconnect(&self) -> ApiResult {
        let _ = self.cli(&["enable_network", "all"])?;
        self.cli_ok(&["reconnect"])?;
        Ok(json!({"ok": true}))
    }
}

#[cfg(test)]
mod tests {
    use super::super::runner::fake::{fail, out, FakeRunner};
    use super::*;
    use crate::testutil::Tmp;

    fn wifi(rules: Vec<(&str, Vec<Output>)>) -> (Tmp, Arc<FakeRunner>, WpaCli) {
        let t = Tmp::new();
        let r = Arc::new(FakeRunner::new(rules));
        let mut w = WpaCli::new(Arc::new(Config::rooted(t.path())), r.clone());
        w.scan_wait = Duration::ZERO;
        w.poll = Duration::from_millis(5);
        w.connect_timeout = Duration::from_millis(80);
        (t, r, w)
    }

    const SCAN: &str = "bssid / frequency / signal level / flags / ssid\n\
        aa:bb:cc:dd:ee:01\t2412\t-70\t[WPA2-PSK-CCMP][ESS]\tFar\n\
        aa:bb:cc:dd:ee:02\t5180\t-40\t[SAE][ESS]\tNear\n\
        aa:bb:cc:dd:ee:03\t2437\t-55\t[ESS]\tCafe\n\
        aa:bb:cc:dd:ee:04\t2437\t-50\t[WPA2-PSK-CCMP]\t\n";

    #[test]
    fn status_parses_state_and_signal() {
        let (_t, r, w) = wifi(vec![
            (
                "wlan0 status",
                vec![out(
                    "wpa_state=COMPLETED\nssid=Home\nip_address=192.168.1.9\nfreq=5180\nid=0\n",
                )],
            ),
            ("signal_poll", vec![out("RSSI=-52\nLINKSPEED=433\n")]),
        ]);
        let v = w.status().unwrap();
        assert_eq!(v["connected"], true);
        assert_eq!(v["ssid"], "Home");
        assert_eq!(v["ip"], "192.168.1.9");
        assert_eq!(v["rssi_dbm"], -52);
        assert_eq!(v["link_mbps"], 433);
        assert!(r.called("-p"));
        assert!(r.called("-i wlan0"));
    }

    #[test]
    fn status_when_supplicant_is_down_is_unavailable() {
        let (_t, _r, w) = wifi(vec![(
            "status",
            vec![fail("Failed to connect to wpa_supplicant")],
        )]);
        assert_eq!(w.status().unwrap_err().code, "unavailable");
    }

    #[test]
    fn scan_sorts_by_signal_and_hides_hidden_ssids() {
        let (_t, r, w) = wifi(vec![
            ("scan_results", vec![out(SCAN)]),
            (" scan", vec![out("OK")]),
        ]);
        let v = w.scan().unwrap();
        let n = v["networks"].as_array().unwrap();
        assert_eq!(n.len(), 3);
        assert_eq!(n[0]["ssid"], "Near");
        assert_eq!(n[0]["secured"], true);
        assert_eq!(n[2]["ssid"], "Far");
        assert_eq!(n[1]["secured"], false);
        assert!(r.called("wlan0 scan"));
    }

    #[test]
    fn connect_validates_before_touching_the_supplicant() {
        let (_t, r, w) = wifi(vec![]);
        for (ssid, pw) in [
            ("", None),
            ("x", Some("short")),
            ("ok", Some("bad\u{7f}password")),
        ] {
            let e = w
                .connect(&WifiConnect {
                    ssid: ssid.into(),
                    password: pw.map(String::from),
                })
                .unwrap_err();
            assert_eq!(e.code, "bad_request");
        }
        assert!(r.calls().is_empty());
    }

    #[test]
    fn connect_success_uses_hex_ssid_and_quoted_psk() {
        let (_t, r, w) = wifi(vec![
            (
                "wlan0 status",
                vec![
                    out("wpa_state=COMPLETED\nid=0\n"),
                    out("wpa_state=COMPLETED\nid=3\nip_address=10.0.0.5\n"),
                ],
            ),
            ("add_network", vec![out("3\n")]),
            ("set_network", vec![out("OK")]),
            ("select_network", vec![out("OK")]),
        ]);
        let v = w
            .connect(&WifiConnect {
                ssid: "Lab".into(),
                password: Some("hunter2hunter2".into()),
            })
            .unwrap();
        assert_eq!(v["ip"], "10.0.0.5");
        assert!(r.called("set_network 3 ssid 4c6162"));
        assert!(r.called("set_network 3 psk \"hunter2hunter2\""));
        assert!(!r.called("remove_network"));
    }

    #[test]
    fn open_network_sets_key_mgmt_none() {
        let (_t, r, w) = wifi(vec![
            (
                "wlan0 status",
                vec![
                    out("wpa_state=DISCONNECTED\n"),
                    out("wpa_state=COMPLETED\nid=1\n"),
                ],
            ),
            ("add_network", vec![out("1")]),
            ("set_network", vec![out("OK")]),
            ("select_network", vec![out("OK")]),
        ]);
        w.connect(&WifiConnect {
            ssid: "Open".into(),
            password: None,
        })
        .unwrap();
        assert!(r.called("set_network 1 key_mgmt NONE"));
    }

    #[test]
    fn failed_connect_restores_previous_network() {
        let (_t, r, w) = wifi(vec![
            (
                "wlan0 status",
                vec![
                    out("wpa_state=COMPLETED\nid=0\n"),
                    out("wpa_state=SCANNING\n"),
                ],
            ),
            ("add_network", vec![out("4")]),
            ("set_network", vec![out("OK")]),
            ("select_network", vec![out("OK")]),
            ("remove_network", vec![out("OK")]),
            ("enable_network", vec![out("OK")]),
        ]);
        let e = w
            .connect(&WifiConnect {
                ssid: "Nope".into(),
                password: Some("wrongpassword".into()),
            })
            .unwrap_err();
        assert_eq!(e.code, "connect_failed");
        assert!(r.called("remove_network 4"));
        assert!(r.called("enable_network all"));
        let calls = r.calls();
        assert!(calls.last().unwrap().ends_with("select_network 0"));
    }

    #[test]
    fn disconnect_and_reconnect() {
        let (_t, r, w) = wifi(vec![
            ("disconnect", vec![out("OK")]),
            ("reconnect", vec![out("OK")]),
            ("enable_network", vec![out("OK")]),
        ]);
        assert_eq!(w.disconnect().unwrap()["ok"], true);
        assert_eq!(w.reconnect().unwrap()["ok"], true);
        assert!(r.called("enable_network all"));
        let (_t, _r, w) = wifi(vec![("disconnect", vec![out("FAIL")])]);
        assert_eq!(w.disconnect().unwrap_err().code, "upstream_error");
    }

    #[test]
    fn availability_follows_the_control_socket() {
        let (t, _r, w) = wifi(vec![]);
        assert!(w.availability().unwrap().contains("control socket missing"));
        t.write("run/wpa_supplicant-s22/wlan0", "");
        assert!(w.availability().is_none());
    }
}
