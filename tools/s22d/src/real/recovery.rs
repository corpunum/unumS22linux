//! `s22-reboot recovery`, the documented safe reboot (back in about 40 s; OpenUnum, buttons and
//! unumsearch start by themselves). It runs a few seconds after the request so the HTTP answer
//! and the audit record are written first, in its own process group so it outlives s22d.

use crate::backends::Recovery;
use crate::config::Config;
use crate::error::{ApiError, ApiResult};
use serde_json::json;
use std::os::unix::process::CommandExt;
use std::process::{Command, Stdio};
use std::sync::Arc;

pub struct RebootRecovery {
    cfg: Arc<Config>,
}

impl RebootRecovery {
    pub fn new(cfg: Arc<Config>) -> Self {
        Self { cfg }
    }
}

impl Recovery for RebootRecovery {
    fn availability(&self) -> Option<String> {
        if self.cfg.reboot_tool.exists() {
            None
        } else {
            Some(format!(
                "s22-reboot missing: {}",
                self.cfg.reboot_tool.display()
            ))
        }
    }

    fn schedule(&self) -> ApiResult {
        if let Some(why) = self.availability() {
            return Err(ApiError::unavailable(why));
        }
        Command::new("sh")
            .arg("-c")
            .arg("sleep \"$1\"; exec \"$2\" recovery")
            .arg("s22d-reboot")
            .arg(self.cfg.reboot_delay_s.to_string())
            .arg(&self.cfg.reboot_tool)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .process_group(0)
            .spawn()
            .map_err(|e| ApiError::unavailable(format!("cannot start s22-reboot: {e}")))?;
        Ok(json!({"ok": true, "scheduled_in_s": self.cfg.reboot_delay_s, "mode": "recovery"}))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::Tmp;
    use std::time::{Duration, Instant};

    #[test]
    fn runs_the_tool_with_recovery_after_the_delay() {
        let t = Tmp::new();
        t.script(
            "usr/local/sbin/s22-reboot",
            &format!("echo \"$@\" > {}/ran", t.path().display()),
        );
        let mut c = Config::rooted(t.path());
        c.reboot_delay_s = 0;
        let r = RebootRecovery::new(Arc::new(c));
        assert!(r.availability().is_none());
        assert_eq!(r.schedule().unwrap()["mode"], "recovery");
        let marker = t.path().join("ran");
        let until = Instant::now() + Duration::from_secs(5);
        while !marker.exists() && Instant::now() < until {
            std::thread::sleep(Duration::from_millis(20));
        }
        assert_eq!(std::fs::read_to_string(marker).unwrap().trim(), "recovery");
    }

    #[test]
    fn missing_tool_is_unavailable() {
        let t = Tmp::new();
        let r = RebootRecovery::new(Arc::new(Config::rooted(t.path())));
        assert_eq!(r.schedule().unwrap_err().code, "unavailable");
    }
}
