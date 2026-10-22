//! Camera page: a live viewfinder from s22-campreview (a managed child that
//! streams while the page is open), photo from the running stream (4 raws ->
//! s22-develop in the background -> thumbnail), MJPEG video recording, front /
//! back switch.
//!
//! `Cam` is the pure state machine (unit-tested): UI actions and the child's
//! JSON event lines go in, `CamCmd`s (spawn, signal, develop) come out. The main
//! loop runs the commands. `Viewfinder` maps the child's shared-memory triple
//! buffer and blits the newest frame rotated / mirrored into the canvas.
use crate::gfx::{Canvas, Rect};
use serde_json::{json, Value};
use std::time::Instant;

pub const CAMPREVIEW: &str = "/srv/s22/hardware/bin/s22-campreview";
pub const DEVELOP: &str = "/srv/s22/hardware/bin/s22-develop";
pub const SHM: &str = "/run/s22-fastshell-cam.shm";
pub const WORK: &str = "/tmp/s22-cam";
pub const INHIBIT: &str = "/run/s22-autosleep/inhibit.d/fastshell-camera";
/// The preview stream on two pinned A710 workers; video JPEG on 4-5 is set by the child,
/// develop runs on the X2 (cpu 7, set by main before exec: busybox has no taskset).
const PREVIEW_CPUS: &str = "4-5";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    Off,
    Starting,
    Live,
    Stopping,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    Photo,
    Video,
}

#[derive(Clone, Debug, PartialEq)]
pub enum CamCmd {
    /// Start s22-campreview for this sensor; events carry `gen`.
    Spawn { gen: u64, argv: Vec<String> },
    Signal { pid: i32, sig: i32 },
    /// Develop a burst in the background; the result comes back as a "developed" event.
    Develop { gen: u64, argv: Vec<String>, preview: String, jpeg: String, raw: String },
    /// Autosleep inhibit file on/off.
    Inhibit(bool),
}

/// Rotation (clockwise degrees) and mirroring of the preview per sensor.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Orient {
    pub rotate: u32,
    pub mirror: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Orients {
    pub rear: Orient,
    pub front: Orient,
}

impl Default for Orients {
    fn default() -> Self {
        // Android-style mounting: the sensors' long side runs along the phone's long side;
        // the selfie preview is mirrored like a mirror (photos are saved unmirrored).
        Orients { rear: Orient { rotate: 90, mirror: false }, front: Orient { rotate: 270, mirror: true } }
    }
}

impl Orients {
    pub fn get(&self, sensor: &str) -> Orient {
        if sensor == "front" { self.front } else { self.rear }
    }
    pub fn parse(s: &str) -> Orients {
        let mut o = Orients::default();
        if let Ok(v) = serde_json::from_str::<Value>(s) {
            for (k, slot) in [("rear", &mut o.rear), ("front", &mut o.front)] {
                if let Some(r) = v[k]["rotate"].as_u64().filter(|r| r % 90 == 0) {
                    slot.rotate = (r % 360) as u32;
                }
                if let Some(m) = v[k]["mirror"].as_bool() {
                    slot.mirror = m;
                }
            }
        }
        o
    }
    pub fn to_json(&self) -> String {
        json!({"rear": {"rotate": self.rear.rotate, "mirror": self.rear.mirror},
               "front": {"rotate": self.front.rotate, "mirror": self.front.mirror}})
        .to_string()
    }
    /// Settings flip: rotate the sensor's preview a further 90 degrees.
    pub fn turn(&mut self, sensor: &str) {
        let o = if sensor == "front" { &mut self.front } else { &mut self.rear };
        o.rotate = (o.rotate + 90) % 360;
    }
}

pub struct Cam {
    pub phase: Phase,
    pub sensor: &'static str,
    pub mode: Mode,
    /// The page wants the stream (false after leaving it).
    pub want: bool,
    pub gen: u64,
    pub pid: Option<i32>,
    /// Sensor to start when the running child has exited (front/back switch).
    restart: bool,
    pub recording: Option<Instant>,
    pub rec_requested: bool,
    pub shots: u32,
    pub shutter_t: Option<(Instant, u64)>,
    pub status: String,
    pub last_jpeg: Option<String>,
    pub thumb_due: Option<String>,
    pub fails: u32,
    pub gallery_host: String,
    pub orient: Orients,
    /// Times of the last few photos: shutter -> thumbnail (ms).
    pub shot_ms: Vec<f64>,
}

impl Cam {
    pub fn new(gallery_host: &str) -> Cam {
        Cam {
            phase: Phase::Off,
            sensor: "rear",
            mode: Mode::Photo,
            want: false,
            gen: 0,
            pid: None,
            restart: false,
            recording: None,
            rec_requested: false,
            shots: 0,
            shutter_t: None,
            status: String::new(),
            last_jpeg: None,
            thumb_due: None,
            fails: 0,
            gallery_host: gallery_host.into(),
            orient: Orients::default(),
            shot_ms: vec![],
        }
    }

    pub fn argv(&self) -> Vec<String> {
        let o = self.orient.get(self.sensor);
        let mut v: Vec<String> = [
            CAMPREVIEW, "stream", "--sensor", self.sensor, "--until-signal", "--scale", "2", "--threads", "2", "--cpus", PREVIEW_CPUS,
            "--ae", "--shm", SHM, "--still-dir", WORK, "--still-frames", "4", "--rec-dir", &self.gallery_host,
            "--rec-rotate", &o.rotate.to_string(),
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();
        if o.mirror {
            v.push("--rec-mirror".into());
        }
        v
    }

    fn spawn(&mut self) -> Vec<CamCmd> {
        self.gen += 1;
        self.phase = Phase::Starting;
        self.restart = false;
        self.status = format!("starting the {} camera…", self.sensor);
        vec![CamCmd::Inhibit(true), CamCmd::Spawn { gen: self.gen, argv: self.argv() }]
    }

    fn term(&mut self) -> Vec<CamCmd> {
        self.phase = Phase::Stopping;
        match self.pid {
            Some(pid) => vec![CamCmd::Signal { pid, sig: libc::SIGTERM }],
            None => vec![],
        }
    }

    /// The Camera page opened (or the panel came back on with it showing).
    pub fn open(&mut self) -> Vec<CamCmd> {
        self.want = true;
        self.fails = 0;
        match self.phase {
            Phase::Off => self.spawn(),
            Phase::Stopping => {
                self.restart = true;
                vec![]
            }
            _ => vec![],
        }
    }

    /// The page closed, or the panel went off: stop streaming (a recording is finished by the child).
    pub fn close(&mut self) -> Vec<CamCmd> {
        self.want = false;
        self.restart = false;
        match self.phase {
            Phase::Starting | Phase::Live => {
                let mut c = self.term();
                if self.shots == 0 {
                    c.push(CamCmd::Inhibit(false));
                }
                c
            }
            _ => vec![],
        }
    }

    pub fn switch_sensor(&mut self) -> Vec<CamCmd> {
        self.sensor = if self.sensor == "rear" { "front" } else { "rear" };
        match self.phase {
            Phase::Starting | Phase::Live => {
                self.restart = true;
                self.status = format!("switching to the {} camera…", self.sensor);
                self.term()
            }
            Phase::Stopping => {
                self.restart = true;
                vec![]
            }
            Phase::Off => {
                if self.want {
                    self.spawn()
                } else {
                    vec![]
                }
            }
        }
    }

    pub fn shutter(&mut self, t_us: u64) -> Vec<CamCmd> {
        match (self.phase, self.mode, self.pid) {
            (Phase::Live, Mode::Photo, Some(pid)) => {
                self.shots += 1;
                self.shutter_t = Some((Instant::now(), t_us));
                self.status = "capturing…".into();
                vec![CamCmd::Signal { pid, sig: libc::SIGUSR1 }]
            }
            (Phase::Live, Mode::Video, Some(pid)) => {
                if self.rec_requested {
                    return vec![];
                }
                self.rec_requested = true;
                self.status = if self.recording.is_some() { "stopping the recording…".into() } else { "starting the recording…".into() };
                vec![CamCmd::Signal { pid, sig: libc::SIGUSR2 }]
            }
            _ => vec![],
        }
    }

    pub fn set_mode(&mut self, m: Mode) -> Vec<CamCmd> {
        if self.recording.is_some() || self.rec_requested {
            return vec![];
        }
        self.mode = m;
        vec![]
    }

    /// The child started (main has its pid).
    pub fn spawned(&mut self, gen: u64, pid: i32) -> Vec<CamCmd> {
        if gen != self.gen {
            return vec![CamCmd::Signal { pid, sig: libc::SIGTERM }];
        }
        self.pid = Some(pid);
        if self.phase == Phase::Stopping {
            // closed before the child was up
            return vec![CamCmd::Signal { pid, sig: libc::SIGTERM }];
        }
        vec![]
    }

    /// A JSON event line from the child (or main's synthetic "exit" / "spawn_failed" / "developed").
    pub fn event(&mut self, gen: u64, v: &Value) -> Vec<CamCmd> {
        let ev = v["event"].as_str().unwrap_or("");
        if ev == "developed" {
            return self.developed(v);
        }
        if gen != self.gen {
            return vec![];
        }
        match ev {
            "first_frame" => {
                if self.phase == Phase::Starting {
                    self.phase = Phase::Live;
                    self.fails = 0;
                    self.status = String::new();
                }
                if !self.want {
                    return self.close();
                }
                vec![]
            }
            "still" => {
                if v["ok"] == true {
                    let raw = v["raw"].as_str().unwrap_or("").to_string();
                    let stamp = raw.rsplit('-').next().and_then(|s| s.strip_suffix(".raw")).unwrap_or("0").to_string();
                    let jpeg = format!("{}/cam-{}-{}.jpg", self.gallery_host, &stamp[..stamp.len().min(10)], self.sensor);
                    let preview = format!("{WORK}/last-{stamp}.png");
                    let rot = self.orient.get(self.sensor).rotate;
                    self.status = "developing…".into();
                    let argv: Vec<String> = [
                        DEVELOP, "develop", &raw, "--stack", "--out", &jpeg, "--preview", &preview, "--preview-max", "1080x1440",
                        "--rotate", &rot.to_string(), "--quality", "90",
                    ]
                    .iter()
                    .map(|s| s.to_string())
                    .collect();
                    vec![CamCmd::Develop { gen, argv, preview, jpeg, raw }]
                } else {
                    self.shots = self.shots.saturating_sub(1);
                    self.status = "the photo failed (raw write)".into();
                    vec![]
                }
            }
            "rec_started" => {
                self.rec_requested = false;
                self.recording = Some(Instant::now());
                self.status = String::new();
                vec![]
            }
            "rec_stopped" => {
                self.rec_requested = false;
                self.recording = None;
                self.status = if v["ok"] == true {
                    let name = v["path"].as_str().and_then(|p| p.rsplit('/').next()).unwrap_or("");
                    format!("video saved: {name} · {} s · {} fps", v["seconds"], v["fps"])
                } else {
                    format!("recording failed: {}", v["error"].as_str().unwrap_or("?"))
                };
                vec![]
            }
            "spawn_failed" | "exit" => {
                let was = self.phase;
                self.phase = Phase::Off;
                self.pid = None;
                self.recording = None;
                self.rec_requested = false;
                if ev == "spawn_failed" || (was == Phase::Starting && !self.restart) {
                    self.fails += 1;
                    self.status = format!("camera failed: {}", v["error"].as_str().or(v["tail"].as_str()).unwrap_or("the stream ended"));
                }
                if self.want && (self.restart || (was == Phase::Live && self.fails < 3)) && self.fails < 3 {
                    return self.spawn();
                }
                self.restart = false;
                if self.shots == 0 {
                    vec![CamCmd::Inhibit(false)]
                } else {
                    vec![]
                }
            }
            _ => vec![],
        }
    }

    fn developed(&mut self, v: &Value) -> Vec<CamCmd> {
        self.shots = self.shots.saturating_sub(1);
        if v["ok"] == true {
            self.last_jpeg = v["jpeg"].as_str().map(String::from);
            self.thumb_due = v["preview"].as_str().map(String::from);
            if let Some((t, _)) = self.shutter_t {
                let ms = t.elapsed().as_secs_f64() * 1000.0;
                self.shot_ms.push(ms);
                if self.shot_ms.len() > 50 {
                    self.shot_ms.remove(0);
                }
            }
            let name = self.last_jpeg.as_deref().and_then(|p| p.rsplit('/').next()).unwrap_or("").to_string();
            self.status = format!("saved {name}");
        } else {
            self.status = format!("develop failed: {}", v["error"].as_str().unwrap_or("?"));
        }
        if self.shots == 0 && self.phase == Phase::Off {
            vec![CamCmd::Inhibit(false)]
        } else {
            vec![]
        }
    }

    pub fn live(&self) -> bool {
        self.phase == Phase::Live
    }
}

/// The child's shared-memory triple buffer: 4 KiB header ("S22P", w, h, stride,
/// fourcc, slots, slot bytes; seq u64 @32, latest slot u32 @40, frame-start ns
/// @48, publish ns @56) + 3 XRGB slots.
pub struct Viewfinder {
    ptr: *const u8,
    len: usize,
    pub w: usize,
    pub h: usize,
    stride: usize,
    slot_bytes: usize,
    pub seq: u64,
    /// blit tables for (dst rect, rotate, mirror)
    key: (Rect, u32, bool),
    tx: Vec<u32>,
    ty: Vec<u32>,
}

unsafe impl Send for Viewfinder {}

impl Viewfinder {
    pub fn open(path: &str) -> Option<Viewfinder> {
        use std::os::fd::AsRawFd;
        let f = std::fs::File::open(path).ok()?;
        let len = f.metadata().ok()?.len() as usize;
        if len < 4096 {
            return None;
        }
        let p = unsafe { libc::mmap(std::ptr::null_mut(), len, libc::PROT_READ, libc::MAP_SHARED, f.as_raw_fd(), 0) };
        if p == libc::MAP_FAILED {
            return None;
        }
        let ptr = p as *const u8;
        let hdr = unsafe { std::slice::from_raw_parts(ptr, 64) };
        let u = |o: usize| u32::from_le_bytes(hdr[o..o + 4].try_into().unwrap()) as usize;
        let (w, h, stride, slots, slot_bytes) = (u(8), u(12), u(16), u(24), u(28));
        if &hdr[0..4] != b"S22P" || slots != 3 || w == 0 || h == 0 || stride < w * 4 || 4096 + 3 * slot_bytes > len || slot_bytes < stride * h {
            unsafe { libc::munmap(p, len) };
            return None;
        }
        Some(Viewfinder { ptr, len, w, h, stride, slot_bytes, seq: 0, key: (Rect::new(0, 0, 0, 0), 0, false), tx: vec![], ty: vec![] })
    }

    fn hdr_u64(&self, o: usize) -> u64 {
        unsafe { std::ptr::read_volatile(self.ptr.add(o) as *const u64) }
    }

    /// (seq, publish ns) of the newest frame.
    pub fn latest(&self) -> (u64, u64) {
        let s = self.hdr_u64(32);
        std::sync::atomic::fence(std::sync::atomic::Ordering::Acquire);
        (s, self.hdr_u64(56))
    }

    fn slot(&self) -> &[u32] {
        let slot = unsafe { std::ptr::read_volatile(self.ptr.add(40) as *const u32) } as usize % 3;
        unsafe { std::slice::from_raw_parts(self.ptr.add(4096 + slot * self.slot_bytes) as *const u32, self.slot_bytes / 4) }
    }

    /// Output size of the rotated frame fitted to `max` (keeps the aspect).
    pub fn fit(&self, rotate: u32, max: Rect) -> Rect {
        fit(self.w, self.h, rotate, max)
    }

    /// Draw the newest frame into `dst` (clipped to the canvas clip), rotated clockwise and mirrored.
    pub fn blit(&mut self, cv: &mut Canvas, dst: Rect, rotate: u32, mirror: bool) {
        if self.key != (dst, rotate, mirror) {
            let (tx, ty) = tables(self.w, self.h, self.stride / 4, dst.w as usize, dst.h as usize, rotate, mirror);
            self.tx = tx;
            self.ty = ty;
            self.key = (dst, rotate, mirror);
        }
        let Some(i) = dst.intersect(&cv.clip) else { return };
        let src = self.slot();
        let cw = cv.w as usize;
        for y in i.y..i.y + i.h {
            let by = self.ty[(y - dst.y) as usize] as usize;
            let row = &mut cv.px[y as usize * cw + i.x as usize..y as usize * cw + (i.x + i.w) as usize];
            let tx = &self.tx[(i.x - dst.x) as usize..(i.x - dst.x + i.w) as usize];
            for (p, &ox) in row.iter_mut().zip(tx) {
                *p = unsafe { *src.get_unchecked(by + ox as usize) } & 0x00ff_ffff;
            }
        }
    }
}

impl Drop for Viewfinder {
    fn drop(&mut self) {
        unsafe { libc::munmap(self.ptr as *mut libc::c_void, self.len) };
    }
}

pub fn fit(w: usize, h: usize, rotate: u32, max: Rect) -> Rect {
    let (rw, rh) = if rotate % 180 == 90 { (h, w) } else { (w, h) };
    let s = (max.w as f64 / rw as f64).min(max.h as f64 / rh as f64);
    let (ow, oh) = ((rw as f64 * s) as i32, (rh as f64 * s) as i32);
    Rect::new(max.x + (max.w - ow) / 2, max.y + (max.h - oh) / 2, ow, oh)
}

/// Index tables: src index of output (x, y) = tx[x] + ty[y], for a source of
/// `sw` x `sh` pixels (row pitch `sp` pixels) shown rotated `rotate` degrees
/// clockwise (then mirrored left-right) in a `dw` x `dh` box (nearest pixel).
pub fn tables(sw: usize, sh: usize, sp: usize, dw: usize, dh: usize, rotate: u32, mirror: bool) -> (Vec<u32>, Vec<u32>) {
    let (rw, rh) = if rotate % 180 == 90 { (sh, sw) } else { (sw, sh) };
    // output (x, y) -> rotated-image (u, v)
    let u = |x: usize| -> usize {
        let x = if mirror { dw - 1 - x } else { x };
        (x * rw / dw.max(1)).min(rw - 1)
    };
    let v = |y: usize| -> usize { (y * rh / dh.max(1)).min(rh - 1) };
    // rotated (u, v) -> source (sx, sy); clockwise rotation by `rotate`:
    //   90: u = sh-1-sy, v = sx   -> sx = v, sy = sh-1-u
    //  180: sx = sw-1-u, sy = sh-1-v
    //  270: u = sy, v = sw-1-sx  -> sx = sw-1-v, sy = u
    let mut tx = vec![0u32; dw];
    let mut ty = vec![0u32; dh];
    match rotate % 360 {
        90 => {
            for (x, t) in tx.iter_mut().enumerate() {
                *t = ((sh - 1 - u(x)) * sp) as u32;
            }
            for (y, t) in ty.iter_mut().enumerate() {
                *t = v(y) as u32;
            }
        }
        180 => {
            for (x, t) in tx.iter_mut().enumerate() {
                *t = (sw - 1 - u(x)) as u32;
            }
            for (y, t) in ty.iter_mut().enumerate() {
                *t = ((sh - 1 - v(y)) * sp) as u32;
            }
        }
        270 => {
            for (x, t) in tx.iter_mut().enumerate() {
                *t = (u(x) * sp) as u32;
            }
            for (y, t) in ty.iter_mut().enumerate() {
                *t = (sw - 1 - v(y)) as u32;
            }
        }
        _ => {
            for (x, t) in tx.iter_mut().enumerate() {
                *t = u(x) as u32;
            }
            for (y, t) in ty.iter_mut().enumerate() {
                *t = (v(y) * sp) as u32;
            }
        }
    }
    (tx, ty)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ev(c: &mut Cam, v: Value) -> Vec<CamCmd> {
        let g = c.gen;
        c.event(g, &v)
    }

    #[test]
    fn open_stream_close_lifecycle() {
        let mut c = Cam::new("/g");
        let cmds = c.open();
        assert_eq!(c.phase, Phase::Starting);
        assert!(matches!(&cmds[..], [CamCmd::Inhibit(true), CamCmd::Spawn { gen: 1, .. }]));
        if let CamCmd::Spawn { argv, .. } = &cmds[1] {
            assert!(argv.contains(&"--until-signal".to_string()) && argv.contains(&"rear".to_string()));
            assert!(argv.windows(2).any(|w| w == ["--rec-rotate", "90"]));
        }
        assert!(c.open().is_empty(), "already starting");
        c.spawned(1, 42);
        assert!(ev(&mut c, json!({"event": "first_frame", "ms": 180.0})).is_empty());
        assert!(c.live());
        assert_eq!(c.close(), vec![CamCmd::Signal { pid: 42, sig: libc::SIGTERM }, CamCmd::Inhibit(false)]);
        assert_eq!(c.phase, Phase::Stopping);
        // exit after a close: off, no restart
        assert_eq!(ev(&mut c, json!({"event": "exit", "code": 0})), vec![CamCmd::Inhibit(false)]);
        assert_eq!(c.phase, Phase::Off);
        // a late event from an old child is ignored
        assert!(c.event(0, &json!({"event": "first_frame"})).is_empty());
        assert_eq!(c.phase, Phase::Off);
    }

    #[test]
    fn switch_sensor_restarts_after_exit_with_the_new_sensor() {
        let mut c = Cam::new("/g");
        c.open();
        c.spawned(1, 7);
        ev(&mut c, json!({"event": "first_frame"}));
        assert_eq!(c.switch_sensor(), vec![CamCmd::Signal { pid: 7, sig: libc::SIGTERM }]);
        assert_eq!(c.sensor, "front");
        let cmds = ev(&mut c, json!({"event": "exit", "code": 0}));
        match &cmds[..] {
            [CamCmd::Inhibit(true), CamCmd::Spawn { gen: 2, argv }] => {
                assert!(argv.windows(2).any(|w| w == ["--sensor", "front"]));
                assert!(argv.windows(2).any(|w| w == ["--rec-rotate", "270"]));
                assert!(argv.contains(&"--rec-mirror".to_string()));
            }
            o => panic!("{o:?}"),
        }
        assert_eq!(c.phase, Phase::Starting);
    }

    #[test]
    fn photo_shutter_develop_and_thumbnail() {
        let mut c = Cam::new("/g");
        assert!(c.shutter(1).is_empty(), "not live yet");
        c.open();
        c.spawned(1, 9);
        ev(&mut c, json!({"event": "first_frame"}));
        assert_eq!(c.shutter(5), vec![CamCmd::Signal { pid: 9, sig: libc::SIGUSR1 }]);
        assert_eq!(c.shots, 1);
        let cmds = ev(&mut c, json!({"event": "still", "ok": true, "raw": "/tmp/s22-cam/shot-1792672445099.raw", "frames": 4}));
        match &cmds[..] {
            [CamCmd::Develop { argv, jpeg, preview, .. }] => {
                assert_eq!(jpeg, "/g/cam-1792672445-rear.jpg");
                assert_eq!(preview, "/tmp/s22-cam/last-1792672445099.png");
                assert!(argv.contains(&"--stack".to_string()));
                assert!(argv.windows(2).any(|w| w == ["--rotate", "90"]));
            }
            o => panic!("{o:?}"),
        }
        // the develop result arrives on any generation (the stream may have restarted)
        c.gen += 1;
        c.event(0, &json!({"event": "developed", "ok": true, "jpeg": "/g/cam-1-rear.jpg", "preview": "/tmp/p.png"}));
        assert_eq!(c.shots, 0);
        assert_eq!(c.thumb_due.as_deref(), Some("/tmp/p.png"));
        assert_eq!(c.shot_ms.len(), 1);
        assert!(c.status.contains("cam-1-rear.jpg"));
    }

    #[test]
    fn video_record_toggle_and_mode_lock() {
        let mut c = Cam::new("/g");
        c.open();
        c.spawned(1, 3);
        ev(&mut c, json!({"event": "first_frame"}));
        c.set_mode(Mode::Video);
        assert_eq!(c.shutter(1), vec![CamCmd::Signal { pid: 3, sig: libc::SIGUSR2 }]);
        assert!(c.shutter(2).is_empty(), "one request at a time");
        ev(&mut c, json!({"event": "rec_started", "path": "/g/vid-1-rear.avi"}));
        assert!(c.recording.is_some());
        c.set_mode(Mode::Photo);
        assert_eq!(c.mode, Mode::Video, "no mode change while recording");
        assert_eq!(c.shutter(3), vec![CamCmd::Signal { pid: 3, sig: libc::SIGUSR2 }]);
        ev(&mut c, json!({"event": "rec_stopped", "ok": true, "path": "/g/vid-1-rear.avi", "seconds": 4.0, "fps": 29.8}));
        assert!(c.recording.is_none());
        assert!(c.status.contains("vid-1-rear.avi"));
        // the child dying while live: restarted (up to 3 failures)
        let cmds = ev(&mut c, json!({"event": "exit", "code": 1}));
        assert!(matches!(&cmds[..], [CamCmd::Inhibit(true), CamCmd::Spawn { gen: 2, .. }]));
        for _ in 0..3 {
            ev(&mut c, json!({"event": "exit", "code": 1}));
        }
        assert_eq!(c.phase, Phase::Off, "gives up after failed starts");
        assert!(c.status.starts_with("camera failed"));
    }

    #[test]
    fn close_while_starting_stops_on_first_frame() {
        let mut c = Cam::new("/g");
        c.open();
        assert_eq!(c.close(), vec![CamCmd::Inhibit(false)]);
        assert_eq!(c.spawned(1, 4), vec![CamCmd::Signal { pid: 4, sig: libc::SIGTERM }], "closed before the child was up");
        // no pid yet: the first frame then closes it
        let mut c = Cam::new("/g");
        c.open();
        c.want = false;
        c.spawned(1, 5);
        let cmds = ev(&mut c, json!({"event": "first_frame"}));
        assert_eq!(cmds[0], CamCmd::Signal { pid: 5, sig: libc::SIGTERM });
    }

    #[test]
    fn orientation_config_and_flip() {
        let mut o = Orients::parse(r#"{"rear":{"rotate":270},"front":{"mirror":false,"rotate":45}}"#);
        assert_eq!(o.rear, Orient { rotate: 270, mirror: false });
        assert_eq!(o.front, Orient { rotate: 270, mirror: false }, "45 is ignored");
        o.turn("rear");
        assert_eq!(o.rear.rotate, 0);
        assert_eq!(Orients::parse(&o.to_json()), o);
        assert_eq!(Orients::parse("junk"), Orients::default());
    }

    #[test]
    fn blit_tables_rotate_and_mirror() {
        // 3x2 source (pitch 4): values = index
        let src: Vec<u32> = (0..8).collect();
        let at = |rot: u32, mirror: bool, dw: usize, dh: usize| -> Vec<u32> {
            let (tx, ty) = tables(3, 2, 4, dw, dh, rot, mirror);
            let mut out = vec![];
            for y in 0..dh {
                for x in 0..dw {
                    out.push(src[(tx[x] + ty[y]) as usize]);
                }
            }
            out
        };
        // source rows: [0 1 2] [4 5 6]
        assert_eq!(at(0, false, 3, 2), vec![0, 1, 2, 4, 5, 6]);
        // clockwise 90: 2x3, top row = left column bottom-up
        assert_eq!(at(90, false, 2, 3), vec![4, 0, 5, 1, 6, 2]);
        assert_eq!(at(180, false, 3, 2), vec![6, 5, 4, 2, 1, 0]);
        assert_eq!(at(270, false, 2, 3), vec![2, 6, 1, 5, 0, 4]);
        assert_eq!(at(270, true, 2, 3), vec![6, 2, 5, 1, 4, 0]);
        // scaling 2x
        assert_eq!(at(0, false, 6, 4)[..6], [0, 0, 1, 1, 2, 2]);
        let r = fit(1020, 766, 90, Rect::new(0, 96, 1080, 1500));
        assert_eq!((r.w, r.h), (1080, 1438));
    }
}
