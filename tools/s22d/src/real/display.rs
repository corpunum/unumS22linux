//! Screen power and brightness. Power goes through `s22-touchd` when it is running (it locks the
//! UI before switching off, exactly like the Power key), and straight to `s22-display` otherwise.
//! `s22-display` does the real work: DPMS through the compositor so the OLED panel is truly off.

use super::runner::Runner;
use super::touch;
use crate::backends::Display;
use crate::config::Config;
use crate::error::{ApiError, ApiResult};
use serde_json::{json, Value};
use std::sync::Arc;
use std::time::Duration;

pub struct DisplayTool {
    cfg: Arc<Config>,
    runner: Arc<dyn Runner>,
}

impl DisplayTool {
    pub fn new(cfg: Arc<Config>, runner: Arc<dyn Runner>) -> Self {
        Self { cfg, runner }
    }

    fn num(&self, name: &str) -> Option<u32> {
        std::fs::read_to_string(self.cfg.backlight_dir.join(name))
            .ok()?
            .trim()
            .parse()
            .ok()
    }

    fn state(&self) -> Option<String> {
        std::fs::read_to_string(&self.cfg.display_state)
            .ok()
            .map(|s| s.trim().to_string())
    }

    fn info(&self) -> Value {
        let (b, max) = (self.num("brightness"), self.num("max_brightness"));
        json!({
            "state": self.state(),
            "brightness": b,
            "max_brightness": max,
            "percent": b.zip(max).filter(|(_, m)| *m > 0).map(|(b, m)| (b * 100 + m / 2) / m),
        })
    }
}

impl Display for DisplayTool {
    fn availability(&self) -> Option<String> {
        if self.cfg.display_tool.exists() {
            None
        } else {
            Some(format!(
                "s22-display missing: {}",
                self.cfg.display_tool.display()
            ))
        }
    }

    fn status(&self) -> ApiResult {
        let mut v = self.info();
        v["ok"] = json!(true);
        Ok(v)
    }

    fn set_power(&self, on: bool) -> ApiResult {
        let arg = if on { "on" } else { "off" };
        let via_touchd = touch::request(
            &self.cfg.touch_sock,
            &json!({"cmd": "display", "arg": arg}),
            Duration::from_secs(25),
        );
        let via = match via_touchd {
            Ok(r) if r["ok"] == true => "touchd",
            Ok(r) => {
                return Err(ApiError::upstream(format!(
                    "s22-touchd could not switch the display {arg}: {}",
                    r["error"].as_str().unwrap_or("unknown error")
                )))
            }
            Err(_) => {
                let o = self
                    .runner
                    .run(
                        &self.cfg.display_tool.display().to_string(),
                        &[arg.to_string()],
                        Duration::from_secs(25),
                    )
                    .map_err(|e| ApiError::unavailable(format!("s22-display: {e}")))?;
                if !o.success() {
                    return Err(ApiError::upstream(format!(
                        "s22-display {arg} failed: {}",
                        o.stderr.trim()
                    )));
                }
                "s22-display"
            }
        };
        let mut v = self.info();
        v["ok"] = json!(true);
        v["via"] = json!(via);
        Ok(v)
    }

    fn set_brightness(&self, percent: u8) -> ApiResult {
        let max = self
            .num("max_brightness")
            .filter(|m| *m > 0)
            .ok_or_else(|| ApiError::unavailable("backlight max_brightness unreadable"))?;
        let value = (max * u32::from(percent.clamp(1, 100)) / 100).max(1);
        std::fs::write(
            self.cfg.backlight_dir.join("brightness"),
            format!("{value}\n"),
        )
        .map_err(|e| ApiError::unavailable(format!("backlight write: {e}")))?;
        let mut v = self.info();
        v["ok"] = json!(true);
        Ok(v)
    }
}

#[cfg(test)]
mod tests {
    use super::super::runner::fake::{fail, out, FakeRunner};
    use super::*;
    use crate::testutil::Tmp;

    fn tool(
        t: &Tmp,
        rules: Vec<(&str, Vec<super::super::runner::Output>)>,
    ) -> (Arc<FakeRunner>, DisplayTool) {
        t.write("sys/class/backlight/panel/max_brightness", "255\n");
        t.write("sys/class/backlight/panel/brightness", "128\n");
        t.write("run/s22-display-state", "on\n");
        t.write("srv/s22/hardware/bin/s22-display", "");
        let r = Arc::new(FakeRunner::new(rules));
        (
            r.clone(),
            DisplayTool::new(Arc::new(Config::rooted(t.path())), r),
        )
    }

    #[test]
    fn status_reads_state_and_percent() {
        let t = Tmp::new();
        let (_r, d) = tool(&t, vec![]);
        let v = d.status().unwrap();
        assert_eq!(v["state"], "on");
        assert_eq!(v["percent"], 50);
        assert!(d.availability().is_none());
    }

    #[test]
    fn brightness_scales_and_never_reaches_zero() {
        let t = Tmp::new();
        let (_r, d) = tool(&t, vec![]);
        d.set_brightness(100).unwrap();
        assert_eq!(
            std::fs::read_to_string(t.path().join("sys/class/backlight/panel/brightness"))
                .unwrap()
                .trim(),
            "255"
        );
        d.set_brightness(0).unwrap(); // clamped to 1 %: a dark screen is /off, not brightness 0
        assert_eq!(
            std::fs::read_to_string(t.path().join("sys/class/backlight/panel/brightness"))
                .unwrap()
                .trim(),
            "2"
        );
    }

    #[test]
    fn power_prefers_touchd_so_the_ui_locks_first() {
        let t = Tmp::new();
        let (r, d) = tool(&t, vec![]);
        let rx = touch::fake::serve(
            &Config::rooted(t.path()).touch_sock,
            |_| json!({"ok": true, "display": "off"}),
        );
        let v = d.set_power(false).unwrap();
        assert_eq!(v["via"], "touchd");
        assert_eq!(rx.recv().unwrap(), json!({"cmd": "display", "arg": "off"}));
        assert!(r.calls().is_empty());
    }

    #[test]
    fn power_falls_back_to_s22_display() {
        let t = Tmp::new();
        let (r, d) = tool(&t, vec![("s22-display on", vec![out("")])]);
        let v = d.set_power(true).unwrap();
        assert_eq!(v["via"], "s22-display");
        assert!(r.called("s22-display on"));
        let t = Tmp::new();
        let (_r, d) = tool(&t, vec![("s22-display off", vec![fail("no compositor")])]);
        assert_eq!(d.set_power(false).unwrap_err().code, "upstream_error");
    }

    #[test]
    fn touchd_refusal_is_not_papered_over() {
        let t = Tmp::new();
        let (r, d) = tool(&t, vec![]);
        let _rx = touch::fake::serve(
            &Config::rooted(t.path()).touch_sock,
            |_| json!({"ok": false, "error": "bad_arg"}),
        );
        assert_eq!(d.set_power(true).unwrap_err().code, "upstream_error");
        assert!(r.calls().is_empty());
    }
}
