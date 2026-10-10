//! Camera capture through the reviewed `s22-camera.py` client, with the optional develop step
//! (raw to DNG to LibRaw, draft PR #32) when that tool is installed.
//!
//! The camera path can panic the kernel on a DMA lifecycle fault, so this module is deliberately
//! conservative: the client always runs with its own `--deadline`, a timed-out client gets
//! SIGTERM and a grace period but never SIGKILL, and the routes serialise captures behind one
//! global lock with a failure cooldown (`CameraGuard` in `app.rs`).

use super::runner::Runner;
use crate::backends::{Camera, CaptureReq};
use crate::config::Config;
use crate::error::{ApiError, ApiResult};
use axum::http::StatusCode;
use serde_json::{json, Value};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub const EXPOSURE_US: std::ops::RangeInclusive<u32> = 100..=30000;
pub const GAIN: std::ops::RangeInclusive<f32> = 1.0..=16.0;

pub struct CameraClient {
    cfg: Arc<Config>,
    pub(crate) runner: Arc<dyn Runner>,
    /// Added to the client's own `--deadline` to get the hard limit.
    pub extra_wait: Duration,
    /// Time a client gets to exit after SIGTERM before the capture is reported as wedged.
    pub grace: Duration,
}

pub fn validate(req: &CaptureReq) -> Result<(), ApiError> {
    if req.sensor != "rear" && req.sensor != "front" {
        return Err(ApiError::bad_request(
            "sensor must be \"rear\" or \"front\"",
        ));
    }
    if let Some(e) = req.exposure_us {
        if !EXPOSURE_US.contains(&e) {
            return Err(ApiError::bad_request("exposure_us must be 100-30000"));
        }
    }
    if let Some(g) = req.gain {
        if !g.is_finite() || !GAIN.contains(&g) {
            return Err(ApiError::bad_request("gain must be 1.0-16.0"));
        }
    }
    Ok(())
}

/// The last line of `out` that parses as a JSON object (the client prints its result last).
pub fn last_json(out: &str) -> Option<Value> {
    out.lines()
        .rev()
        .map(str::trim)
        .filter(|l| l.starts_with('{'))
        .find_map(|l| serde_json::from_str::<Value>(l).ok())
}

fn tail(s: &str, n: usize) -> String {
    let t = s.trim();
    t.chars()
        .skip(t.chars().count().saturating_sub(n))
        .collect()
}

fn drain<R: Read + Send + 'static>(mut r: R) -> std::thread::JoinHandle<String> {
    std::thread::spawn(move || {
        let mut b = Vec::new();
        let _ = r.by_ref().take(1 << 20).read_to_end(&mut b);
        String::from_utf8_lossy(&b).into_owned()
    })
}

impl CameraClient {
    pub fn new(cfg: Arc<Config>, runner: Arc<dyn Runner>) -> Self {
        Self {
            cfg,
            runner,
            extra_wait: Duration::from_secs(60),
            grace: Duration::from_secs(20),
        }
    }

    fn client_args(&self, req: &CaptureReq, png: &Path, raw: &Path) -> Vec<String> {
        let mut a: Vec<String> = self.cfg.python_args.clone();
        a.push(self.cfg.camera_client.display().to_string());
        for s in [
            "capture",
            "--sensor",
            req.sensor.as_str(),
            "--out",
            &png.display().to_string(),
            "--raw",
            &raw.display().to_string(),
            "--frames",
            "1",
            "--skip",
            "6",
            "--deadline",
            &self.cfg.camera_deadline_s.to_string(),
        ] {
            a.push(s.to_string());
        }
        if self.cfg.camera_allow_fw_stall {
            a.push("--allow-fw-stall".into());
        }
        // --cis-* is the only exposure path that reaches the sensor (CAMERA.md, review 2026-10-09).
        if let Some(e) = req.exposure_us {
            a.extend(["--cis-exposure-us".into(), e.to_string()]);
        }
        if let Some(g) = req.gain {
            a.extend(["--cis-again".into(), format!("{g}")]);
        }
        a
    }

    fn run_client(&self, args: &[String]) -> Result<String, ApiError> {
        let mut child = Command::new(&self.cfg.python)
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| ApiError::unavailable(format!("cannot start camera client: {e}")))?;
        let out = child.stdout.take().map(drain);
        let err = child.stderr.take().map(drain);
        let hard = Duration::from_secs(self.cfg.camera_deadline_s) + self.extra_wait;
        let start = Instant::now();
        let mut termed: Option<Instant> = None;
        loop {
            if let Ok(Some(status)) = child.try_wait() {
                let so = out
                    .map(|h| h.join().unwrap_or_default())
                    .unwrap_or_default();
                let se = err
                    .map(|h| h.join().unwrap_or_default())
                    .unwrap_or_default();
                if termed.is_some() {
                    return Err(ApiError::timeout(format!(
                        "camera client exceeded {}s and was terminated: {}",
                        hard.as_secs(),
                        tail(&se, 300)
                    )));
                }
                return if status.success() || last_json(&so).is_some() {
                    Ok(so)
                } else {
                    Err(ApiError::new(
                        StatusCode::BAD_GATEWAY,
                        "camera_failed",
                        format!("camera client exited {status}: {}", tail(&se, 400)),
                    ))
                };
            }
            match termed {
                None if start.elapsed() >= hard => {
                    unsafe { libc::kill(child.id() as i32, libc::SIGTERM) };
                    termed = Some(Instant::now());
                }
                Some(t) if t.elapsed() >= self.grace => {
                    // Never SIGKILL: the sensor DMA must be torn down by the client itself.
                    return Err(ApiError::new(
                        StatusCode::GATEWAY_TIMEOUT,
                        "camera_wedged",
                        format!(
                            "camera client pid {} ignored SIGTERM; left running, do not retry",
                            child.id()
                        ),
                    ));
                }
                _ => {}
            }
            std::thread::sleep(Duration::from_millis(25));
        }
    }

    fn develop(&self, raw: &Path, base: &Path) -> Value {
        if !self.cfg.develop_tool.exists() {
            return json!({"ok": false, "reason": "develop tool not installed"});
        }
        let png = base.with_extension("developed.png");
        let dng = base.with_extension("dng");
        let mut args = self.cfg.python_args.clone();
        args.extend([
            self.cfg.develop_tool.display().to_string(),
            "develop".into(),
            raw.display().to_string(),
            "--out-png".into(),
            png.display().to_string(),
            "--out-dng".into(),
            dng.display().to_string(),
            "--json".into(),
        ]);
        match self
            .runner
            .run(&self.cfg.python, &args, Duration::from_secs(120))
        {
            Ok(o) if o.success() => json!({
                "ok": true,
                "png": png.display().to_string(),
                "dng": dng.display().to_string(),
                "tool": last_json(&o.stdout),
            }),
            Ok(o) => {
                json!({"ok": false, "error": tail(&format!("{} {}", o.stderr, o.stdout), 300), "timed_out": o.timed_out})
            }
            Err(e) => json!({"ok": false, "error": e.to_string()}),
        }
    }

    /// Keep the newest `camera_keep` captures; delete the rest (raw frames are 6 MB each).
    fn prune(&self) {
        let Ok(rd) = std::fs::read_dir(&self.cfg.camera_dir) else {
            return;
        };
        let mut stems: Vec<String> = rd
            .filter_map(|e| e.ok())
            .filter_map(|e| {
                let n = e.file_name().to_string_lossy().to_string();
                n.starts_with("cap-")
                    .then(|| n.split('.').next().unwrap_or("").to_string())
            })
            .collect();
        stems.sort();
        stems.dedup();
        let drop = stems.len().saturating_sub(self.cfg.camera_keep);
        for stem in &stems[..drop] {
            if let Ok(rd) = std::fs::read_dir(&self.cfg.camera_dir) {
                for e in rd.filter_map(|e| e.ok()) {
                    if e.file_name()
                        .to_string_lossy()
                        .starts_with(&format!("{stem}."))
                    {
                        let _ = std::fs::remove_file(e.path());
                    }
                }
            }
        }
    }

    fn last_path(&self) -> PathBuf {
        self.cfg.camera_dir.join("last.json")
    }
}

impl Camera for CameraClient {
    fn availability(&self) -> Option<String> {
        if self.cfg.camera_client.exists() {
            None
        } else {
            Some(format!(
                "camera client missing: {}",
                self.cfg.camera_client.display()
            ))
        }
    }

    fn status(&self) -> ApiResult {
        let last = std::fs::read_to_string(self.last_path())
            .ok()
            .and_then(|s| serde_json::from_str::<Value>(&s).ok());
        Ok(json!({
            "ok": true,
            "client_present": self.cfg.camera_client.exists(),
            "develop_available": self.cfg.develop_tool.exists(),
            "sensors": ["rear", "front"],
            "dir": self.cfg.camera_dir.display().to_string(),
            "last": last,
        }))
    }

    fn capture(&self, req: &CaptureReq) -> ApiResult {
        validate(req)?;
        if let Some(why) = self.availability() {
            return Err(ApiError::unavailable(why));
        }
        std::fs::create_dir_all(&self.cfg.camera_dir)?;
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        let base = self
            .cfg
            .camera_dir
            .join(format!("cap-{stamp}-{}", req.sensor));
        let (png, raw) = (base.with_extension("png"), base.with_extension("raw"));
        let t0 = Instant::now();
        let stdout = self.run_client(&self.client_args(req, &png, &raw))?;
        let client = last_json(&stdout)
            .ok_or_else(|| ApiError::upstream("camera client printed no JSON result"))?;
        if client["ok"] != true {
            return Err(ApiError::new(
                StatusCode::BAD_GATEWAY,
                "camera_failed",
                client["error"]
                    .as_str()
                    .unwrap_or("camera client reported failure")
                    .to_string(),
            ));
        }
        let developed = if req.develop && raw.exists() {
            Some(self.develop(&raw, &base))
        } else {
            None
        };
        let out = json!({
            "ok": true,
            "sensor": req.sensor,
            "path": png.display().to_string(),
            "raw": raw.exists().then(|| raw.display().to_string()),
            "developed": developed,
            "width": client["width"],
            "height": client["height"],
            "exposure_us": req.exposure_us,
            "gain": req.gain,
            "ms": t0.elapsed().as_millis() as u64,
            "client": client,
        });
        let _ = std::fs::write(self.last_path(), out.to_string());
        self.prune();
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::super::runner::fake::{out, FakeRunner};
    use super::super::runner::SysRunner;
    use super::*;
    use crate::testutil::Tmp;

    const OK_SCRIPT: &str = r#"
while [ $# -gt 0 ]; do case "$1" in --out) o=$2;; --raw) r=$2;; esac; shift; done
echo "ioctl noise" >&2
echo "starting"
echo x > "$o"; echo y > "$r"
echo "{\"ok\": true, \"path\": \"$o\", \"width\": 510, \"height\": 383}"
"#;

    fn setup(script: &str) -> (Tmp, CameraClient) {
        let t = Tmp::new();
        t.script("srv/s22/hardware/bin/s22-camera", script);
        let mut c = Config::rooted(t.path());
        c.python = "sh".into();
        c.python_args = vec![];
        let mut cam = CameraClient::new(Arc::new(c), Arc::new(SysRunner));
        cam.extra_wait = Duration::ZERO;
        cam.grace = Duration::from_millis(300);
        (t, cam)
    }

    fn req(sensor: &str) -> CaptureReq {
        CaptureReq {
            sensor: sensor.into(),
            exposure_us: None,
            gain: None,
            develop: true,
        }
    }

    #[test]
    fn validation() {
        assert!(validate(&req("rear")).is_ok());
        assert!(validate(&req("top")).is_err());
        let mut r = req("front");
        r.exposure_us = Some(50);
        assert!(validate(&r).is_err());
        r.exposure_us = Some(10000);
        r.gain = Some(32.0);
        assert!(validate(&r).is_err());
        r.gain = Some(f32::NAN);
        assert!(validate(&r).is_err());
        r.gain = Some(2.0);
        assert!(validate(&r).is_ok());
    }

    #[test]
    fn last_json_skips_noise() {
        assert_eq!(last_json("a\n{\"ok\":true}\nstray\n").unwrap()["ok"], true);
        assert!(last_json("no json here").is_none());
        assert_eq!(last_json("{\"a\":1}\n{broken\n").unwrap()["a"], 1);
    }

    #[test]
    fn capture_passes_sensor_and_cis_flags() {
        let (t, cam) = setup(&format!(
            "echo \"$@\" > \"$(dirname \"$0\")/args.txt\"\n{OK_SCRIPT}"
        ));
        let mut r = req("front");
        r.exposure_us = Some(12000);
        r.gain = Some(2.0);
        let v = cam.capture(&r).unwrap();
        assert_eq!(v["ok"], true);
        assert_eq!(v["width"], 510);
        assert!(Path::new(v["path"].as_str().unwrap()).exists());
        assert_eq!(v["developed"]["ok"], false); // no develop tool installed
        let args = std::fs::read_to_string(t.path().join("srv/s22/hardware/bin/args.txt")).unwrap();
        for want in [
            "capture --sensor front",
            "--cis-exposure-us 12000",
            "--cis-again 2",
            "--allow-fw-stall",
            "--deadline 120",
            "--frames 1",
        ] {
            assert!(args.contains(want), "{want} missing from {args}");
        }
        assert_eq!(cam.status().unwrap()["last"]["sensor"], "front");
    }

    #[test]
    fn rear_without_exposure_sends_no_cis_flags() {
        let (t, cam) = setup(&format!(
            "echo \"$@\" > \"$(dirname \"$0\")/args.txt\"\n{OK_SCRIPT}"
        ));
        cam.capture(&req("rear")).unwrap();
        let args = std::fs::read_to_string(t.path().join("srv/s22/hardware/bin/args.txt")).unwrap();
        assert!(!args.contains("--cis-"));
        assert!(args.contains("--sensor rear"));
    }

    #[test]
    fn develop_step_runs_when_the_tool_is_installed() {
        let (t, mut cam) = setup(OK_SCRIPT);
        t.write("srv/s22/hardware/bin/s22-develop", "");
        let runner = Arc::new(FakeRunner::new(vec![(
            "develop",
            vec![out("{\"ok\": true, \"stage_ms\": 12}")],
        )]));
        cam.runner = runner.clone();
        let v = cam.capture(&req("rear")).unwrap();
        assert_eq!(v["developed"]["ok"], true);
        assert!(v["developed"]["png"]
            .as_str()
            .unwrap()
            .ends_with(".developed.png"));
        assert!(runner.called("--out-dng"));
        let mut no = req("rear");
        no.develop = false;
        assert!(cam.capture(&no).unwrap()["developed"].is_null());
    }

    #[test]
    fn client_failure_is_reported_not_swallowed() {
        let (_t, cam) =
            setup("echo '{\"ok\": false, \"error\": \"S_FMT failed: EINVAL\"}'; exit 1");
        let e = cam.capture(&req("rear")).unwrap_err();
        assert_eq!(
            (e.status, e.code),
            (StatusCode::BAD_GATEWAY, "camera_failed")
        );
        assert!(e.message.contains("S_FMT"));
        let (_t, cam) = setup("echo 'Traceback: boom' >&2; exit 3");
        let e = cam.capture(&req("rear")).unwrap_err();
        assert_eq!(e.code, "camera_failed");
        assert!(e.message.contains("boom"));
    }

    #[test]
    fn bad_input_and_missing_client_never_start_a_process() {
        let (_t, cam) = setup(OK_SCRIPT);
        assert_eq!(cam.capture(&req("side")).unwrap_err().code, "bad_request");
        let t = Tmp::new();
        let cam = CameraClient::new(Arc::new(Config::rooted(t.path())), Arc::new(SysRunner));
        assert_eq!(cam.capture(&req("rear")).unwrap_err().code, "unavailable");
        assert!(cam.availability().is_some());
    }

    #[test]
    fn hung_client_gets_sigterm_only() {
        let t = Tmp::new();
        t.script("srv/s22/hardware/bin/s22-camera", "sleep 30");
        let mut c = Config::rooted(t.path());
        c.python = "sh".into();
        c.python_args = vec![];
        c.camera_deadline_s = 0;
        let mut cam = CameraClient::new(Arc::new(c), Arc::new(SysRunner));
        cam.extra_wait = Duration::ZERO;
        cam.grace = Duration::from_millis(500);
        let e = cam.capture(&req("rear")).unwrap_err();
        assert_eq!(e.code, "timeout", "{e:?}");
    }

    #[test]
    fn client_ignoring_sigterm_is_left_alone_and_reported_wedged() {
        let t = Tmp::new();
        t.script("srv/s22/hardware/bin/s22-camera", "trap '' TERM\nsleep 2");
        let mut c = Config::rooted(t.path());
        c.python = "sh".into();
        c.python_args = vec![];
        c.camera_deadline_s = 0;
        let mut cam = CameraClient::new(Arc::new(c), Arc::new(SysRunner));
        cam.extra_wait = Duration::from_millis(300); // let the shell install its trap first
        cam.grace = Duration::from_millis(300);
        let e = cam.capture(&req("rear")).unwrap_err();
        assert_eq!(e.code, "camera_wedged");
    }

    #[test]
    fn only_the_newest_captures_are_kept() {
        let t = Tmp::new();
        let mut c = Config::rooted(t.path());
        c.camera_keep = 2;
        for n in 1..=4 {
            t.write(
                &format!("srv/s22/state/s22d/camera/cap-{n:04}-rear.png"),
                "p",
            );
            t.write(
                &format!("srv/s22/state/s22d/camera/cap-{n:04}-rear.raw"),
                "r",
            );
        }
        let cam = CameraClient::new(Arc::new(c), Arc::new(SysRunner));
        cam.prune();
        let mut left: Vec<String> = std::fs::read_dir(t.path().join("srv/s22/state/s22d/camera"))
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().to_string())
            .collect();
        left.sort();
        assert_eq!(
            left,
            [
                "cap-0003-rear.png",
                "cap-0003-rear.raw",
                "cap-0004-rear.png",
                "cap-0004-rear.raw"
            ]
        );
    }
}
