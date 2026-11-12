//! Camera capture through the reviewed `s22-camera.py` client. When the `s22-develop` software ISP
//! (a static Rust binary, tools/hardware/camera/s22-develop) is installed, a capture follows the
//! same path as the shell's camera button (s22-touchd `camera_photo`): a short meter frame,
//! `s22-develop meter` for exposure/gain, a raw burst at that exposure, then
//! `s22-develop develop --stack` to a full-resolution JPEG that goes to the OpenUnum gallery.
//!
//! The camera path can panic the kernel on a DMA lifecycle fault, so this module is deliberately
//! conservative: the client always runs with its own `--deadline`, a timed-out client gets
//! SIGTERM and a grace period but never SIGKILL, and the routes serialise captures behind one
//! global lock with a failure cooldown (`CameraGuard` in `app.rs`).

use super::runner::Runner;
use crate::backends::{Camera, CaptureReq, RecordReq};
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
/// Meter frame settings and burst length, as in s22-touchd `camera_photo` / `CAMERA_SENSORS`.
const METER_EXPOSURE_US: u32 = 10000;
const METER_GAIN: f32 = 2.0;
const BURST_FRAMES: u32 = 4;

/// Highest analog gain auto exposure may pick (the front sensor is noisier).
fn max_gain(sensor: &str) -> f32 {
    if sensor == "front" {
        8.0
    } else {
        16.0
    }
}

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

    #[allow(clippy::too_many_arguments)]
    fn client_args(
        &self,
        sensor: &str,
        exposure_us: Option<u32>,
        gain: Option<f32>,
        frames: u32,
        burst: bool,
        png: &Path,
        raw: &Path,
    ) -> Vec<String> {
        let mut a: Vec<String> = self.cfg.python_args.clone();
        a.push(self.cfg.camera_client.display().to_string());
        for s in [
            "capture",
            "--sensor",
            sensor,
            "--out",
            &png.display().to_string(),
            "--raw",
            &raw.display().to_string(),
            "--frames",
            &frames.to_string(),
            "--skip",
            "6",
            "--deadline",
            &self.cfg.camera_deadline_s.to_string(),
        ] {
            a.push(s.to_string());
        }
        if burst {
            // every kept frame is written as RAW.0, RAW.1, ... for `s22-develop develop --stack`
            a.push("--raw-burst".into());
        }
        if self.cfg.camera_allow_fw_stall {
            a.push("--allow-fw-stall".into());
        }
        // --cis-* is the only exposure path that reaches the sensor (CAMERA.md, review 2026-10-09).
        if let Some(e) = exposure_us {
            a.extend(["--cis-exposure-us".into(), e.to_string()]);
        }
        if let Some(g) = gain {
            a.extend(["--cis-again".into(), format!("{g}")]);
        }
        a
    }

    fn run_client(&self, args: &[String], deadline: Duration) -> Result<String, ApiError> {
        let mut child = Command::new(&self.cfg.python)
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| ApiError::unavailable(format!("cannot start camera client: {e}")))?;
        let out = child.stdout.take().map(drain);
        let err = child.stderr.take().map(drain);
        let hard = deadline + self.extra_wait;
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

    /// Meter frame (10 ms, 2x) -> `s22-develop meter` -> (exposure_us, gain) for the real shot.
    /// Falls back to 20 ms / 8x (the shell's default) when either step fails; never fails the capture.
    fn meter(&self, sensor: &str, base: &Path) -> (u32, f32, Value) {
        let (mut exp, mut gain) = (20000u32, 8.0f32);
        let mpng = base.with_extension("meter.png");
        let mraw = base.with_extension("meter.raw");
        let args = self.client_args(
            sensor,
            Some(METER_EXPOSURE_US),
            Some(METER_GAIN),
            1,
            false,
            &mpng,
            &mraw,
        );
        let info = match self.run_client(&args, Duration::from_secs(self.cfg.camera_deadline_s)) {
            Err(e) => json!({"ok": false, "error": e.message}),
            Ok(_) if !mraw.exists() => json!({"ok": false, "error": "meter frame wrote no raw"}),
            Ok(_) => {
                let a: Vec<String> = vec![
                    "meter".into(),
                    mraw.display().to_string(),
                    "--exposure-us".into(),
                    METER_EXPOSURE_US.to_string(),
                    "--gain".into(),
                    format!("{METER_GAIN}"),
                    "--max-gain".into(),
                    format!("{}", max_gain(sensor)),
                    "--max-exposure-us".into(),
                    EXPOSURE_US.end().to_string(),
                ];
                let tool = self.cfg.develop_tool.display().to_string();
                match self.runner.run(&tool, &a, Duration::from_secs(60)) {
                    Ok(o) => match last_json(&o.stdout) {
                        Some(m) if m["ok"] == true => {
                            if let (Some(e), Some(g)) =
                                (m["exposure_us"].as_u64(), m["gain"].as_f64())
                            {
                                exp = (e as u32).clamp(*EXPOSURE_US.start(), *EXPOSURE_US.end());
                                gain = (g as f32).clamp(*GAIN.start(), max_gain(sensor));
                            }
                            m
                        }
                        _ => {
                            json!({"ok": false, "error": tail(&format!("{} {}", o.stderr, o.stdout), 300)})
                        }
                    },
                    Err(e) => json!({"ok": false, "error": e.to_string()}),
                }
            }
        };
        let _ = std::fs::remove_file(&mpng);
        let _ = std::fs::remove_file(&mraw);
        let _ = std::fs::remove_file(base.with_extension("meter.raw.json"));
        (exp, gain, info)
    }

    /// `s22-develop develop RAW --stack --out JPG`: the software ISP, run as the binary it is.
    fn develop(&self, raw: &Path, base: &Path) -> Value {
        let jpg = base.with_extension("jpg");
        let args: Vec<String> = vec![
            "develop".into(),
            raw.display().to_string(),
            "--stack".into(),
            "--out".into(),
            jpg.display().to_string(),
        ];
        let tool = self.cfg.develop_tool.display().to_string();
        match self.runner.run(&tool, &args, Duration::from_secs(120)) {
            Ok(o) if o.success() && jpg.is_file() => json!({
                "ok": true,
                "jpeg": jpg.display().to_string(),
                "tool": last_json(&o.stdout),
            }),
            Ok(o) => {
                json!({"ok": false, "error": tail(&format!("{} {}", o.stderr, o.stdout), 300), "timed_out": o.timed_out})
            }
            Err(e) => json!({"ok": false, "error": e.to_string()}),
        }
    }

    /// Copy a finished capture into the OpenUnum gallery. The gallery keeps it after `prune`
    /// drops the working copy. Never fails the capture: errors come back as text.
    fn save_to_gallery(
        &self,
        img: &Path,
        stamp: u64,
        sensor: &str,
    ) -> Result<Option<PathBuf>, String> {
        let Some(dir) = &self.cfg.gallery_dir else {
            return Ok(None);
        };
        // Only when the OpenUnum home (<home>/assets/generated -> <home>) is there.
        if !dir
            .parent()
            .and_then(Path::parent)
            .is_some_and(Path::is_dir)
            || !img.is_file()
        {
            return Ok(None);
        }
        let ext = img.extension().and_then(|e| e.to_str()).unwrap_or("png");
        let dst = dir.join(format!("cam-{stamp}-{sensor}.{ext}"));
        copy_nofollow(img, &dst)
            .map(|_| Some(dst))
            .map_err(|e| format!("gallery copy failed: {e}"))
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

pub fn validate_record(req: &RecordReq) -> Result<(), ApiError> {
    if req.sensor != "rear" && req.sensor != "front" {
        return Err(ApiError::bad_request(
            "sensor must be \"rear\" or \"front\"",
        ));
    }
    if !(1..=30).contains(&req.seconds) {
        return Err(ApiError::bad_request("seconds must be 1-30"));
    }
    if req.fps.is_some_and(|fps| !(1..=120).contains(&fps)) {
        return Err(ApiError::bad_request("fps must be 1-120"));
    }
    if req.exposure_us.is_some_and(|e| !EXPOSURE_US.contains(&e)) {
        return Err(ApiError::bad_request("exposure_us must be 100-30000"));
    }
    if req
        .gain
        .is_some_and(|g| !g.is_finite() || !GAIN.contains(&g))
    {
        return Err(ApiError::bad_request("gain must be 1.0-16.0"));
    }
    Ok(())
}

impl CameraClient {
    fn record_args(&self, req: &RecordReq, out: &Path) -> Vec<String> {
        let record_safe = self
            .cfg
            .camera_client
            .parent()
            .unwrap()
            .join("record-safe.py");
        let mut a = self.cfg.python_args.clone();
        a.extend([
            record_safe.display().to_string(),
            "--sensor".into(),
            req.sensor.clone(),
            "--seconds".into(),
            req.seconds.to_string(),
            "--out".into(),
            out.display().to_string(),
            "--max-frames-per-session".into(),
            "7".to_string(),
        ]);
        if let Some(fps) = req.fps {
            a.extend(["--fps".into(), fps.to_string()]);
        }
        if let Some(e) = req.exposure_us {
            a.extend(["--exposure-us".into(), e.to_string()]);
        }
        if let Some(g) = req.gain {
            a.extend(["--gain".into(), g.to_string()]);
        }
        a.extend([
            "--deadline".into(),
            (u64::from(req.seconds) + 60).to_string(),
        ]);
        // record-safe.py always passes --allow-fw-stall to its own capture
        // sessions (parity with the old `record` command, which passed it
        // unconditionally); it is not a record-safe.py CLI flag.
        a
    }

    fn save_video(
        &self,
        video: &Path,
        stamp: u64,
        sensor: &str,
    ) -> Result<Option<PathBuf>, String> {
        let Some(dir) = &self.cfg.gallery_dir else {
            return Ok(None);
        };
        if !dir
            .parent()
            .and_then(Path::parent)
            .is_some_and(Path::is_dir)
            || !video.is_file()
        {
            return Ok(None);
        }
        let dst = dir.join(format!("vid-{stamp}-{sensor}.avi"));
        copy_nofollow(video, &dst)
            .map(|_| Some(dst))
            .map_err(|e| format!("gallery copy failed: {e}"))
    }

    fn prune_recordings(&self) {
        let Ok(rd) = std::fs::read_dir(&self.cfg.camera_dir) else {
            return;
        };
        let mut files: Vec<_> = rd
            .filter_map(Result::ok)
            .filter(|e| e.file_name().to_string_lossy().starts_with("record-") && e.path().is_dir())
            .collect();
        files.sort_by_key(|e| e.file_name());
        let count = files.len().saturating_sub(3);
        for e in files.into_iter().take(count) {
            let _ = std::fs::remove_dir_all(e.path());
        }
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
        let isp = req.develop && self.cfg.develop_tool.exists();
        // Auto exposure only when the caller set neither value; an explicit one is kept as given.
        let (exposure_us, gain, meter) = if isp && req.exposure_us.is_none() && req.gain.is_none() {
            let (e, g, m) = self.meter(&req.sensor, &base);
            (Some(e), Some(g), Some(m))
        } else {
            (req.exposure_us, req.gain, None)
        };
        let frames = if isp { BURST_FRAMES } else { 1 };
        let stdout = self.run_client(
            &self.client_args(&req.sensor, exposure_us, gain, frames, isp, &png, &raw),
            Duration::from_secs(self.cfg.camera_deadline_s),
        )?;
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
        let developed = if isp && raw.exists() {
            Some(self.develop(&raw, &base))
        } else if req.develop {
            Some(json!({"ok": false, "reason": "develop tool not installed"}))
        } else {
            None
        };
        // The developed JPEG is the photo; the client's quick PNG (1/4 resolution) is the fallback.
        let photo = developed
            .as_ref()
            .filter(|d| d["ok"] == true)
            .and_then(|d| d["jpeg"].as_str())
            .map(PathBuf::from)
            .unwrap_or_else(|| png.clone());
        let gallery = self.save_to_gallery(&photo, stamp, &req.sensor);
        let tool = developed.as_ref().and_then(|d| d.get("tool"));
        let out = json!({
            "ok": true,
            "sensor": req.sensor,
            "gallery_path": gallery.as_ref().ok().and_then(|p| p.as_ref()).map(|p| p.display().to_string()),
            "gallery_error": gallery.as_ref().err(),
            "path": photo.display().to_string(),
            "quick_png": png.display().to_string(),
            "raw": raw.exists().then(|| raw.display().to_string()),
            "developed": developed,
            "width": tool.and_then(|t| t.get("width")).unwrap_or(&client["width"]),
            "height": tool.and_then(|t| t.get("height")).unwrap_or(&client["height"]),
            "exposure_us": exposure_us,
            "gain": gain,
            "meter": meter,
            "ms": t0.elapsed().as_millis() as u64,
            "client": client,
        });
        let _ = std::fs::write(self.last_path(), out.to_string());
        self.prune();
        Ok(out)
    }

    fn record(&self, req: &RecordReq) -> ApiResult {
        validate_record(req)?;
        if let Some(why) = self.availability() {
            return Err(ApiError::unavailable(why));
        }
        std::fs::create_dir_all(&self.cfg.camera_dir)?;
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        let recording_dir = self
            .cfg
            .camera_dir
            .join(format!("record-{stamp}-{}", req.sensor));
        std::fs::create_dir_all(&recording_dir)?;
        let deadline = Duration::from_secs(u64::from(req.seconds) + 60);
        let stdout = self.run_client(&self.record_args(req, &recording_dir), deadline)?;
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
        let video = recording_dir.join("video.avi");
        if !video.is_file() {
            return Err(ApiError::upstream(
                "camera record completed without video.avi",
            ));
        }
        let gallery = self.save_video(&video, stamp, &req.sensor);
        let result = json!({
            "ok": true,
            "path": video.display().to_string(),
            "gallery_path": gallery.as_ref().ok().and_then(|p| p.as_ref()).map(|p| p.display().to_string()),
            "gallery_error": gallery.as_ref().err(),
            "frames": client["frames"],
            "sessions": client["sessions"],
            "seconds": req.seconds,
            "effective_fps": client["effective_fps"],
            "fps": req.fps.unwrap_or(30),
        });
        self.prune_recordings();
        Ok(result)
    }
}

/// Copy `src` to `dst` as root into a directory tree another user owns (the gallery belongs to the
/// phone's unprivileged agent since 2026-11-12). No component of the destination may be a symbolic link:
/// every directory is opened with `O_NOFOLLOW` from `/` down (missing ones are created and given their
/// parent's owner), and the file is created with `O_EXCL | O_NOFOLLOW` and handed to the directory's
/// owner. A planted link therefore cannot make root write anywhere else.
pub(crate) fn copy_nofollow(src: &Path, dst: &Path) -> std::io::Result<()> {
    use std::ffi::CString;
    use std::io::{Error, ErrorKind};
    use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
    use std::os::unix::ffi::OsStrExt;
    let bad = |m: &str| Error::new(ErrorKind::InvalidInput, m.to_string());
    if !dst.is_absolute() {
        return Err(bad("destination must be absolute"));
    }
    let name = dst.file_name().ok_or_else(|| bad("no file name"))?;
    let dir = dst.parent().ok_or_else(|| bad("no directory"))?;
    let cstr = |b: &std::ffi::OsStr| CString::new(b.as_bytes()).map_err(|_| bad("NUL in path"));
    let dflags = libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC;
    let owner = |fd: &OwnedFd| -> std::io::Result<(u32, u32)> {
        let mut st: libc::stat = unsafe { std::mem::zeroed() };
        if unsafe { libc::fstat(fd.as_raw_fd(), &mut st) } != 0 {
            return Err(Error::last_os_error());
        }
        Ok((st.st_uid, st.st_gid))
    };
    let root = unsafe { libc::open(c"/".as_ptr(), dflags) };
    if root < 0 {
        return Err(Error::last_os_error());
    }
    let mut cur = unsafe { OwnedFd::from_raw_fd(root) };
    for comp in dir.components() {
        let std::path::Component::Normal(part) = comp else {
            if matches!(comp, std::path::Component::RootDir) {
                continue;
            }
            return Err(bad("destination must not contain . or .."));
        };
        let p = cstr(part)?;
        let mut fd = unsafe { libc::openat(cur.as_raw_fd(), p.as_ptr(), dflags) };
        if fd < 0 && Error::last_os_error().kind() == ErrorKind::NotFound {
            let (uid, gid) = owner(&cur)?;
            if unsafe { libc::mkdirat(cur.as_raw_fd(), p.as_ptr(), 0o755) } != 0 {
                return Err(Error::last_os_error());
            }
            fd = unsafe { libc::openat(cur.as_raw_fd(), p.as_ptr(), dflags) };
            if fd >= 0 {
                unsafe { libc::fchown(fd, uid, gid) };
            }
        }
        if fd < 0 {
            return Err(Error::last_os_error());
        }
        cur = unsafe { OwnedFd::from_raw_fd(fd) };
    }
    let (uid, gid) = owner(&cur)?;
    let n = cstr(name)?;
    let fflags = libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL | libc::O_NOFOLLOW | libc::O_CLOEXEC;
    let fd = unsafe { libc::openat(cur.as_raw_fd(), n.as_ptr(), fflags, 0o644 as libc::c_uint) };
    if fd < 0 {
        return Err(Error::last_os_error());
    }
    let mut out = unsafe { std::fs::File::from_raw_fd(fd) };
    std::io::copy(&mut std::fs::File::open(src)?, &mut out)?;
    unsafe { libc::fchown(out.as_raw_fd(), uid, gid) };
    Ok(())
}

#[cfg(test)]
mod tests {
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
        t.script("srv/s22/hardware/bin/record-safe.py", script);
        let mut c = Config::rooted(t.path());
        c.python = "sh".into();
        c.python_args = vec![];
        let mut cam = CameraClient::new(Arc::new(c), Arc::new(SysRunner));
        cam.extra_wait = Duration::ZERO;
        cam.grace = Duration::from_millis(300);
        (t, cam)
    }

    fn record_req(sensor: &str) -> RecordReq {
        RecordReq {
            sensor: sensor.into(),
            seconds: 5,
            fps: None,
            exposure_us: None,
            gain: None,
        }
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
    fn video_recording_creates_gallery_copy_and_passes_deadline_controls() {
        let (t, cam) = setup(
            r#"
while [ $# -gt 0 ]; do case "$1" in --out) o=$2;; esac; shift; done
mkdir -p "$o"; echo avi > "$o/video.avi"
echo "{\"ok\":true,\"frames\":25,\"sessions\":4,\"seconds\":5,\"effective_fps\":5.0,\"path\":\"$o/video.avi\"}"
"#,
        );
        t.write("mnt/omarchy-trial/root/.openunum/openunum.json", "{}");
        let mut r = record_req("rear");
        r.exposure_us = Some(20000);
        r.gain = Some(8.0);
        let value = cam.record(&r).unwrap();
        assert_eq!(value["frames"], 25);
        assert_eq!(value["sessions"], 4);
        assert!(value["gallery_path"].as_str().unwrap().ends_with(".avi"));
        assert_eq!(
            std::fs::read_to_string(value["gallery_path"].as_str().unwrap()).unwrap(),
            "avi\n"
        );
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
    fn capture_is_copied_to_the_openunum_gallery() {
        let (t, cam) = setup(OK_SCRIPT);
        t.write("mnt/omarchy-trial/root/.openunum/openunum.json", "{}");
        let v = cam.capture(&req("rear")).unwrap();
        let g = v["gallery_path"].as_str().expect("gallery_path");
        assert!(
            g.contains(".openunum/assets/generated/cam-") && g.ends_with("-rear.png"),
            "{g}"
        );
        assert_eq!(std::fs::read_to_string(g).unwrap(), "x\n");
        assert!(v["gallery_error"].is_null());
    }

    #[test]
    fn no_gallery_copy_without_an_openunum_home() {
        let (t, cam) = setup(OK_SCRIPT);
        let v = cam.capture(&req("rear")).unwrap();
        assert_eq!(v["ok"], true);
        assert!(v["gallery_path"].is_null());
        assert!(!t.path().join("mnt/omarchy-trial/root/.openunum").exists());
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

    /// A stand-in for the static `s22-develop` binary: `meter` answers 15 ms / 4x, `develop`
    /// writes the --out JPEG. Every call is appended to dev-args.txt.
    const DEVELOP_SCRIPT: &str = r#"
echo "$@" >> "$(dirname "$0")/dev-args.txt"
case "$1" in
  meter) echo '{"ok": true, "mean": 0.04, "exposure_us": 15000, "gain": 4.0}';;
  develop) while [ $# -gt 0 ]; do case "$1" in --out) o=$2;; esac; shift; done
           echo jpeg > "$o"; echo '{"ok": true, "stacked": 4, "width": 4080, "height": 3060, "ms": 900}';;
esac
"#;

    #[test]
    fn isp_meters_bursts_develops_and_saves_the_jpeg() {
        let (t, cam) = setup(&format!(
            "echo \"$@\" >> \"$(dirname \"$0\")/args.txt\"\n{OK_SCRIPT}"
        ));
        t.script("srv/s22/hardware/bin/s22-develop", DEVELOP_SCRIPT);
        t.write("mnt/omarchy-trial/root/.openunum/openunum.json", "{}");
        let v = cam.capture(&req("rear")).unwrap();

        let bin = t.path().join("srv/s22/hardware/bin");
        let calls = std::fs::read_to_string(bin.join("args.txt")).unwrap();
        let calls: Vec<&str> = calls.lines().collect();
        assert_eq!(calls.len(), 2, "meter frame + burst: {calls:?}");
        assert!(calls[0].contains("--frames 1") && calls[0].contains("--cis-exposure-us 10000"));
        assert!(calls[0].contains("--cis-again 2") && !calls[0].contains("--raw-burst"));
        for want in [
            "--frames 4",
            "--raw-burst",
            "--cis-exposure-us 15000",
            "--cis-again 4",
        ] {
            assert!(calls[1].contains(want), "{want} missing from {}", calls[1]);
        }
        let dev = std::fs::read_to_string(bin.join("dev-args.txt")).unwrap();
        assert!(
            dev.contains("meter ") && dev.contains("--max-gain 16"),
            "{dev}"
        );
        assert!(dev.contains("develop ") && dev.contains("--stack"), "{dev}");

        assert_eq!(v["developed"]["ok"], true, "{v}");
        assert_eq!(
            (v["exposure_us"].as_u64(), v["gain"].as_f64()),
            (Some(15000), Some(4.0))
        );
        assert_eq!(
            (v["width"].as_u64(), v["height"].as_u64()),
            (Some(4080), Some(3060))
        );
        assert!(v["path"].as_str().unwrap().ends_with(".jpg"));
        let g = v["gallery_path"].as_str().expect("gallery_path");
        assert!(g.ends_with("-rear.jpg"), "{g}");
        assert_eq!(std::fs::read_to_string(g).unwrap(), "jpeg\n");
        // the meter frame is scratch: nothing of it is left behind
        let left: Vec<String> = std::fs::read_dir(&cam.cfg.camera_dir)
            .unwrap()
            .filter_map(|e| e.ok())
            .map(|e| e.file_name().to_string_lossy().into_owned())
            .filter(|n| n.contains("meter"))
            .collect();
        assert!(left.is_empty(), "{left:?}");
    }

    #[test]
    fn explicit_exposure_skips_metering_and_develop_false_skips_the_isp() {
        let (t, cam) = setup(&format!(
            "echo \"$@\" >> \"$(dirname \"$0\")/args.txt\"\n{OK_SCRIPT}"
        ));
        t.script("srv/s22/hardware/bin/s22-develop", DEVELOP_SCRIPT);
        let mut r = req("front");
        r.exposure_us = Some(8000);
        let v = cam.capture(&r).unwrap();
        assert!(v["meter"].is_null());
        assert_eq!(v["developed"]["ok"], true);
        let bin = t.path().join("srv/s22/hardware/bin");
        let dev = std::fs::read_to_string(bin.join("dev-args.txt")).unwrap();
        assert!(!dev.contains("meter"), "{dev}");

        let mut no = req("rear");
        no.develop = false;
        let v = cam.capture(&no).unwrap();
        assert!(v["developed"].is_null());
        assert!(v["path"].as_str().unwrap().ends_with(".png"));
        let calls = std::fs::read_to_string(bin.join("args.txt")).unwrap();
        assert!(calls.lines().last().unwrap().contains("--frames 1"));
    }

    #[test]
    fn develop_failure_falls_back_to_the_quick_png() {
        let (t, cam) = setup(OK_SCRIPT);
        t.script(
            "srv/s22/hardware/bin/s22-develop",
            "echo 'bad raw' >&2; exit 2",
        );
        t.write("mnt/omarchy-trial/root/.openunum/openunum.json", "{}");
        let v = cam.capture(&req("rear")).unwrap();
        assert_eq!(v["ok"], true);
        assert_eq!(v["developed"]["ok"], false);
        assert_eq!(v["meter"]["ok"], false);
        // the meter fallback is the shell's 20 ms / 8x
        assert_eq!(
            (v["exposure_us"].as_u64(), v["gain"].as_f64()),
            (Some(20000), Some(8.0))
        );
        assert!(v["gallery_path"].as_str().unwrap().ends_with("-rear.png"));
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
    fn gallery_copy_refuses_planted_links() {
        let t = Tmp::new();
        let src = t.write("src.jpg", "PHOTO");
        let victim = t.path().join("victim");
        std::fs::create_dir_all(&victim).unwrap();
        // a real directory tree works, creating what is missing
        let ok = t.path().join("home/assets/generated/cam-1-rear.jpg");
        std::fs::create_dir_all(t.path().join("home")).unwrap();
        copy_nofollow(&src, &ok).unwrap();
        assert_eq!(std::fs::read_to_string(&ok).unwrap(), "PHOTO");
        assert!(copy_nofollow(&src, &ok).is_err(), "never overwrites");
        // a directory component swapped for a link
        std::os::unix::fs::symlink(&victim, t.path().join("home/linked")).unwrap();
        assert!(copy_nofollow(&src, &t.path().join("home/linked/cam-2-rear.jpg")).is_err());
        assert!(
            std::fs::read_dir(&victim).unwrap().next().is_none(),
            "nothing written through the link"
        );
        // the file name itself planted as a link
        std::os::unix::fs::symlink(
            victim.join("x"),
            t.path().join("home/assets/generated/cam-3-rear.jpg"),
        )
        .unwrap();
        assert!(
            copy_nofollow(&src, &t.path().join("home/assets/generated/cam-3-rear.jpg")).is_err()
        );
        assert!(!victim.join("x").exists());
        assert!(copy_nofollow(&src, &t.path().join("home/../x.jpg")).is_err());
    }

    #[test]
    fn hung_client_gets_sigterm_only() {
        let t = Tmp::new();
        // exec: the signal must reach the process holding the pipe (a forked `sleep` kept it open for 30 s).
        t.script("srv/s22/hardware/bin/s22-camera", "exec sleep 30");
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
