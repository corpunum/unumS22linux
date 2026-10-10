//! Service restart the way `s22-keepalive` does it: stop the process, and keepalive's next pass
//! starts it again with its own definition (argv, log, health check, backoff). s22d never starts a
//! service itself, so it cannot start one twice or with the wrong arguments.
//!
//! `needle` strings are copied from keepalive's `SERVICE_DEFS` and `keepalive-config.s22.json`.

use super::runner::Runner;
use crate::backends::Services;
use crate::config::Config;
use crate::error::{ApiError, ApiResult};
use serde_json::json;
use std::sync::Arc;
use std::time::Duration;

/// The only services s22d will touch.
pub const ALLOWLIST: [&str; 5] = ["openunum", "unumsearch", "modem", "phoned", "llama"];

enum Stop {
    /// SIGTERM every process whose command line contains this text.
    Needle(&'static str),
    /// `s22-modem-up stop` (kills `cbd`, keepalive boots the modem again).
    ModemUp,
}

fn stop_for(name: &str) -> Option<Stop> {
    Some(match name {
        "openunum" => Stop::Needle("node src/server.mjs"),
        "unumsearch" => Stop::Needle("unumsearch --config /srv/s22/unumsearch/config.toml serve"),
        "phoned" => Stop::Needle("s22-phoned serve"),
        "llama" => Stop::Needle("llama-server -m /models/"),
        "modem" => Stop::ModemUp,
        _ => return None,
    })
}

pub struct KeepaliveServices {
    cfg: Arc<Config>,
    runner: Arc<dyn Runner>,
    kill: Box<dyn Fn(i32) -> bool + Send + Sync>,
}

impl KeepaliveServices {
    pub fn new(cfg: Arc<Config>, runner: Arc<dyn Runner>) -> Self {
        Self {
            cfg,
            runner,
            kill: Box::new(|pid| unsafe { libc::kill(pid, libc::SIGTERM) == 0 }),
        }
    }

    /// PIDs whose command line contains `needle`, never s22d itself, a grep, or keepalive.
    fn find(&self, needle: &str) -> Vec<i32> {
        let me = std::process::id() as i32;
        let Ok(rd) = std::fs::read_dir(self.cfg.root.join("proc")) else {
            return vec![];
        };
        let mut pids: Vec<i32> = rd
            .filter_map(|e| e.ok())
            .filter_map(|e| {
                let pid: i32 = e.file_name().to_string_lossy().parse().ok()?;
                let cmd = std::fs::read(e.path().join("cmdline")).ok()?;
                let cmd = String::from_utf8_lossy(&cmd).replace('\0', " ");
                (pid != me
                    && cmd.contains(needle)
                    && !cmd.contains("grep")
                    && !cmd.contains("s22-keepalive"))
                .then_some(pid)
            })
            .collect();
        pids.sort();
        pids
    }
}

impl Services for KeepaliveServices {
    fn availability(&self) -> Option<String> {
        if self.cfg.keepalive_enabled.exists() {
            None
        } else {
            Some(format!(
                "s22-keepalive is not enabled ({} missing): a stopped service would stay stopped",
                self.cfg.keepalive_enabled.display()
            ))
        }
    }

    fn restart(&self, name: &str) -> ApiResult {
        let stop = stop_for(name)
            .ok_or_else(|| ApiError::denied("not_allowed", "service is not allowlisted"))?;
        if let Some(why) = self.availability() {
            return Err(ApiError::busy("keepalive_disabled", why));
        }
        match stop {
            Stop::ModemUp => {
                let o = self
                    .runner
                    .run(
                        &self.cfg.modem_up.display().to_string(),
                        &["stop".to_string()],
                        Duration::from_secs(15),
                    )
                    .map_err(|e| ApiError::unavailable(format!("s22-modem-up: {e}")))?;
                if !o.success() {
                    return Err(ApiError::upstream(format!(
                        "s22-modem-up stop failed: {}",
                        o.stderr.trim()
                    )));
                }
                Ok(
                    json!({"ok": true, "service": name, "stopped": o.stdout.trim(), "restart_by": "s22-keepalive"}),
                )
            }
            Stop::Needle(n) => {
                let pids = self.find(n);
                let signalled: Vec<i32> =
                    pids.iter().copied().filter(|p| (self.kill)(*p)).collect();
                Ok(json!({
                    "ok": true,
                    "service": name,
                    "signalled_pids": signalled,
                    "was_running": !pids.is_empty(),
                    "restart_by": "s22-keepalive",
                }))
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::super::runner::fake::{fail, out, FakeRunner};
    use super::*;
    use crate::testutil::Tmp;
    use std::sync::Mutex;

    fn svc(
        t: &Tmp,
        rules: Vec<(&str, Vec<super::super::runner::Output>)>,
    ) -> (Arc<Mutex<Vec<i32>>>, Arc<FakeRunner>, KeepaliveServices) {
        let killed = Arc::new(Mutex::new(vec![]));
        let k = killed.clone();
        let r = Arc::new(FakeRunner::new(rules));
        let mut s = KeepaliveServices::new(Arc::new(Config::rooted(t.path())), r.clone());
        s.kill = Box::new(move |p| {
            k.lock().unwrap().push(p);
            true
        });
        (killed, r, s)
    }

    fn proc_entry(t: &Tmp, pid: i32, cmd: &str) {
        t.write(&format!("proc/{pid}/cmdline"), &cmd.replace(' ', "\0"));
    }

    #[test]
    fn restart_stops_matching_processes_only() {
        let t = Tmp::new();
        t.write("srv/s22/state/keepalive/enabled", "");
        proc_entry(&t, 100, "node src/server.mjs");
        proc_entry(&t, 101, "node other.mjs");
        proc_entry(&t, 102, "grep node src/server.mjs");
        proc_entry(&t, 103, "python3 s22-keepalive node src/server.mjs");
        let (killed, _r, s) = svc(&t, vec![]);
        let v = s.restart("openunum").unwrap();
        assert_eq!(*killed.lock().unwrap(), vec![100]);
        assert_eq!(v["was_running"], true);
        assert_eq!(v["restart_by"], "s22-keepalive");
    }

    #[test]
    fn never_signals_itself() {
        let t = Tmp::new();
        t.write("srv/s22/state/keepalive/enabled", "");
        proc_entry(&t, std::process::id() as i32, "s22-phoned serve");
        let (killed, _r, s) = svc(&t, vec![]);
        let v = s.restart("phoned").unwrap();
        assert!(killed.lock().unwrap().is_empty());
        assert_eq!(v["was_running"], false);
    }

    #[test]
    fn unknown_names_are_not_allowed_even_if_called_directly() {
        let t = Tmp::new();
        t.write("srv/s22/state/keepalive/enabled", "");
        let (killed, _r, s) = svc(&t, vec![]);
        for name in ["s22d", "sshd", "../x", ""] {
            assert_eq!(s.restart(name).unwrap_err().code, "not_allowed");
        }
        assert!(killed.lock().unwrap().is_empty());
    }

    #[test]
    fn refuses_when_keepalive_would_not_bring_it_back() {
        let t = Tmp::new();
        proc_entry(&t, 100, "node src/server.mjs");
        let (killed, _r, s) = svc(&t, vec![]);
        assert_eq!(
            s.restart("openunum").unwrap_err().code,
            "keepalive_disabled"
        );
        assert!(killed.lock().unwrap().is_empty());
        assert!(s.availability().is_some());
    }

    #[test]
    fn modem_goes_through_s22_modem_up() {
        let t = Tmp::new();
        t.write("srv/s22/state/keepalive/enabled", "");
        let (_k, r, s) = svc(&t, vec![("s22-modem-up stop", vec![out("stopped cbd 77")])]);
        assert_eq!(s.restart("modem").unwrap()["stopped"], "stopped cbd 77");
        assert!(r.called("s22-modem-up stop"));
        let (_k, _r, s) = svc(&t, vec![("s22-modem-up stop", vec![fail("boom")])]);
        assert_eq!(s.restart("modem").unwrap_err().code, "upstream_error");
    }

    #[test]
    fn allowlist_matches_keepalive_config() {
        for n in ALLOWLIST {
            assert!(stop_for(n).is_some(), "{n}");
        }
    }
}
