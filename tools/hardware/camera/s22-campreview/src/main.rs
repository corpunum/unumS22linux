//! s22-campreview: live camera preview spike for the Galaxy S22 (native Linux).
//!
//! One continuous VISION-scenario stream (the reviewed s22-camera ioctl path,
//! ported byte for byte in v4l2.rs), each frame developed in place from the
//! VC0 dma-buf to a 1/2 or 1/4 linear-size XRGB8888 preview (2x2 or 4x4 Bayer
//! binning, LUT white balance + gain + gamma), published to a triple-buffered
//! shared-memory file. Optional: sensor AE through S_EXT_CTRLS, a FIFO feed for
//! an encoder, and full-resolution raw stills taken from the running stream.
//!
//! Prints one JSON report (fps, latency, CPU) as the last stdout line.

mod avi;
mod v4l2;
mod process;

use process::{AutoDisplay, Cfa, Stats};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::Write;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::time::Instant;
use v4l2::*;

static STOP: AtomicBool = AtomicBool::new(false);
static STILL_REQ: AtomicU32 = AtomicU32::new(0);
static REC_REQ: AtomicU32 = AtomicU32::new(0);

extern "C" fn on_stop(_: libc::c_int) {
    STOP.store(true, Ordering::SeqCst);
}
extern "C" fn on_still(_: libc::c_int) {
    STILL_REQ.fetch_add(1, Ordering::SeqCst);
}
extern "C" fn on_rec(_: libc::c_int) {
    REC_REQ.fetch_add(1, Ordering::SeqCst);
}

/// One JSON event line on stdout (service mode: the shell reads these).
fn event(v: Value) {
    let mut o = std::io::stdout().lock();
    let _ = writeln!(o, "{v}");
    let _ = o.flush();
}

fn unix_ms() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
}

/// Pin the calling thread to these cores (best effort).
fn pin_thread(cpus: &[usize]) {
    unsafe {
        let mut set: libc::cpu_set_t = std::mem::zeroed();
        for &c in cpus {
            libc::CPU_SET(c, &mut set);
        }
        libc::sched_setaffinity(0, std::mem::size_of::<libc::cpu_set_t>(), &set);
    }
}

/// Video recording: preview frames -> half size, rotated -> JPEG -> MJPEG AVI, on its own thread.
struct Recorder {
    tx: std::sync::mpsc::SyncSender<Vec<u32>>,
    thread: std::thread::JoinHandle<()>,
    dropped: u64,
}

fn start_recorder(dir: &str, w: usize, h: usize, rot: u32, mirror: bool, quality: u8, sensor: &str) -> Result<Recorder, String> {
    let _ = std::fs::create_dir_all(dir);
    let stamp = unix_ms() / 1000;
    let path = format!("{dir}/vid-{stamp}-{sensor}.avi");
    let f = std::fs::File::create(&path).map_err(|e| format!("{path}: {e}"))?;
    let (hw, hh) = (w / 2 & !1, h / 2 & !1);
    let (ow, oh) = if rot % 180 == 90 { (hh, hw) } else { (hw, hh) };
    let mut avi = avi::AviWriter::new(std::io::BufWriter::with_capacity(1 << 20, f), ow as u32, oh as u32).map_err(|e| e.to_string())?;
    let (tx, rx) = std::sync::mpsc::sync_channel::<Vec<u32>>(3);
    let p2 = path.clone();
    let thread = std::thread::spawn(move || {
        pin_thread(&[4, 5]);
        let t0 = Instant::now();
        let mut enc_ms: Vec<f64> = vec![];
        let mut bytes = 0u64;
        let mut err: Option<String> = None;
        for frame in rx {
            let te = Instant::now();
            let (rgb, fw, fh) = avi::half_rgb(&frame, w, h, rot, mirror);
            let mut jpg = Vec::with_capacity(64 * 1024);
            let r = image::codecs::jpeg::JpegEncoder::new_with_quality(&mut jpg, quality)
                .encode(&rgb, fw as u32, fh as u32, image::ExtendedColorType::Rgb8);
            if let Err(e) = r {
                err = Some(e.to_string());
                break;
            }
            enc_ms.push(te.elapsed().as_secs_f64() * 1000.0);
            bytes += jpg.len() as u64;
            if let Err(e) = avi.frame(&jpg) {
                err = Some(e.to_string());
                break;
            }
        }
        let secs = t0.elapsed().as_secs_f64();
        let frames = avi.frames();
        let fin = avi.finish(secs).map(|_| ());
        event(json!({"event": "rec_stopped", "path": p2, "frames": frames, "seconds": (secs * 100.0).round() / 100.0,
                     "fps": if secs > 0.0 { (frames as f64 / secs * 10.0).round() / 10.0 } else { 0.0 },
                     "size": [ow, oh], "bytes": bytes, "encode_ms_p50": pct(&mut enc_ms, 0.5),
                     "ok": err.is_none() && fin.is_ok(), "error": err.or(fin.err().map(|e| e.to_string()))}));
    });
    event(json!({"event": "rec_started", "path": path, "size": [ow, oh], "format": "MJPEG AVI"}));
    Ok(Recorder { tx, thread, dropped: 0 })
}

pub fn mono_ns() -> u64 {
    let mut ts = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    ts.tv_sec as u64 * 1_000_000_000 + ts.tv_nsec as u64
}

pub fn rusage_ms() -> f64 {
    let mut ru: libc::rusage = unsafe { std::mem::zeroed() };
    unsafe { libc::getrusage(libc::RUSAGE_SELF, &mut ru) };
    let tv = |t: libc::timeval| t.tv_sec as f64 * 1000.0 + t.tv_usec as f64 / 1000.0;
    tv(ru.ru_utime) + tv(ru.ru_stime)
}

fn sys_cpu() -> (u64, u64) {
    let s = std::fs::read_to_string("/proc/stat").unwrap_or_default();
    let v: Vec<u64> = s.lines().next().unwrap_or("").split_whitespace().skip(1).filter_map(|x| x.parse().ok()).collect();
    if v.len() < 5 {
        return (0, 0);
    }
    (v.iter().sum(), v[3] + v[4])
}

pub fn pct(v: &mut [f64], p: f64) -> f64 {
    if v.is_empty() {
        return f64::NAN;
    }
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let i = ((v.len() - 1) as f64 * p).round() as usize;
    (v[i] * 100.0).round() / 100.0
}

struct Args {
    sensor: String,
    frames: u64,
    seconds: f64,
    scale: usize,
    threads: usize,
    leader_bufs: u32,
    vc_bufs: u32,
    shm: Option<String>,
    fifo: Option<String>,
    fifo_format: String,
    exposure_us: i64,
    again: f64,
    ae: bool,
    ae_every: u64,
    cpus: Option<String>,
    still_at: Option<u64>,
    still_frames: usize,
    still_out: String,
    no_develop: bool,
    skip_node_check: bool,
    /// service mode: run until SIGTERM/SIGINT (only the no-frame watchdog ends it otherwise)
    until_signal: bool,
    /// write each SIGUSR1 burst while streaming, here (RAW, RAW.0..RAW.n-1, RAW.json) + a "still" event
    still_dir: Option<String>,
    /// SIGUSR2 starts/stops an MJPEG AVI recording here
    rec_dir: Option<String>,
    rec_rotate: u32,
    rec_mirror: bool,
    rec_quality: u8,
}

fn usage() -> ! {
    eprintln!(
        "usage: s22-campreview stream [--sensor rear|front] [--frames N | --seconds S] [--scale 2|4]\n\
         \t[--threads N] [--leader-bufs 4] [--buffers 4] [--shm /run/s22-campreview.shm]\n\
         \t[--fifo PATH --fifo-format i420|xrgb] [--exposure-us 10000 --again 2] [--ae] [--ae-every 6]\n\
         \t[--cpus 4-7] [--still-at-frame N --still-frames K --still-out /srv/s22/tmp/still.raw]\n\
         \t[--no-develop]   (SIGUSR1 also takes a still; SIGINT/SIGTERM stop cleanly)\n\
         \t[--until-signal] [--still-dir DIR] [--rec-dir DIR --rec-rotate 0|90|180|270 --rec-mirror --rec-quality 75]\n\
         \t   (service mode for the shell: JSON event lines on stdout; SIGUSR2 starts/stops a recording)\n\
       s22-campreview bench [REPS [CPUS]]       (develop speed on a dma-buf, no camera)\n\
       s22-campreview dump-structs     (byte images of every ioctl struct, for the host test)"
    );
    std::process::exit(2)
}

fn parse(argv: &[String]) -> Args {
    let mut a = Args {
        sensor: "rear".into(), frames: 0, seconds: 0.0, scale: 2, threads: 1, leader_bufs: 4, vc_bufs: 4,
        shm: None, fifo: None, fifo_format: "i420".into(), exposure_us: 0, again: 0.0, ae: false, ae_every: 6,
        cpus: None, still_at: None, still_frames: 1, still_out: "/srv/s22/tmp/campreview-still.raw".into(),
        no_develop: false, skip_node_check: false, until_signal: false, still_dir: None, rec_dir: None,
        rec_rotate: 0, rec_mirror: false, rec_quality: 75,
    };
    let mut i = 0;
    while i < argv.len() {
        let k = argv[i].as_str();
        let mut val = || {
            i += 1;
            argv.get(i).cloned().unwrap_or_else(|| usage())
        };
        match k {
            "--sensor" => a.sensor = val(),
            "--frames" => a.frames = val().parse().unwrap_or_else(|_| usage()),
            "--seconds" => a.seconds = val().parse().unwrap_or_else(|_| usage()),
            "--scale" => a.scale = val().parse().unwrap_or_else(|_| usage()),
            "--threads" => a.threads = val().parse().unwrap_or_else(|_| usage()),
            "--leader-bufs" => a.leader_bufs = val().parse().unwrap_or_else(|_| usage()),
            "--buffers" => a.vc_bufs = val().parse().unwrap_or_else(|_| usage()),
            "--shm" => a.shm = Some(val()),
            "--fifo" => a.fifo = Some(val()),
            "--fifo-format" => a.fifo_format = val(),
            "--exposure-us" => a.exposure_us = val().parse().unwrap_or_else(|_| usage()),
            "--again" => a.again = val().parse().unwrap_or_else(|_| usage()),
            "--ae" => a.ae = true,
            "--ae-every" => a.ae_every = val().parse().unwrap_or_else(|_| usage()),
            "--cpus" => a.cpus = Some(val()),
            "--still-at-frame" => a.still_at = Some(val().parse().unwrap_or_else(|_| usage())),
            "--still-frames" => a.still_frames = val().parse().unwrap_or_else(|_| usage()),
            "--still-out" => a.still_out = val(),
            "--no-develop" => a.no_develop = true,
            "--skip-node-check" => a.skip_node_check = true,
            "--until-signal" => a.until_signal = true,
            "--still-dir" => a.still_dir = Some(val()),
            "--rec-dir" => a.rec_dir = Some(val()),
            "--rec-rotate" => a.rec_rotate = val().parse().unwrap_or_else(|_| usage()),
            "--rec-mirror" => a.rec_mirror = true,
            "--rec-quality" => a.rec_quality = val().parse().unwrap_or_else(|_| usage()),
            _ => usage(),
        }
        i += 1;
    }
    if !(a.scale == 2 || a.scale == 4) || a.threads == 0 || a.leader_bufs < 2 || a.vc_bufs < 2 || a.vc_bufs > 8
        || a.leader_bufs > 8 || a.still_frames == 0 || a.still_frames > 8 {
        usage();
    }
    if a.frames == 0 && a.seconds <= 0.0 {
        a.seconds = 5.0;
    }
    a
}

fn set_affinity(spec: &str) -> Result<Vec<usize>, String> {
    let mut cpus = vec![];
    for part in spec.split(',') {
        if let Some((a, b)) = part.split_once('-') {
            let (a, b): (usize, usize) = (a.parse().map_err(|_| "bad --cpus")?, b.parse().map_err(|_| "bad --cpus")?);
            cpus.extend(a..=b);
        } else {
            cpus.push(part.parse().map_err(|_| "bad --cpus")?);
        }
    }
    let mut set: libc::cpu_set_t = unsafe { std::mem::zeroed() };
    for &c in &cpus {
        unsafe { libc::CPU_SET(c, &mut set) };
    }
    let r = unsafe { libc::sched_setaffinity(0, std::mem::size_of::<libc::cpu_set_t>(), &set) };
    if r != 0 {
        return Err(format!("sched_setaffinity: {}", std::io::Error::last_os_error()));
    }
    Ok(cpus)
}

fn prechecks(s: &Session, skip_names: bool) -> Result<(), String> {
    if !skip_names {
        let name = |node: &str| {
            std::fs::read_to_string(format!("/sys/class/video4linux/{}/name", node.trim_start_matches("/dev/")))
                .map(|s| s.trim().to_string())
                .unwrap_or_default()
        };
        let (lp, vp) = (s.leader_path(), s.vc0_path());
        let (le, ve) = (format!("exynos-is-ss{}", s.device), format!("exynos-is-ss{}vc0", s.device));
        if name(&lp) != le {
            return Err(format!("{lp} is {:?}, expected {le:?}", name(&lp)));
        }
        let vn = name(&vp);
        if vn != ve && vn != "exynos-is-ssdevice_idvc0" {
            return Err(format!("{vp} is {vn:?}, expected {ve:?}"));
        }
    }
    let st = std::fs::read_to_string("/sys/module/fimc_is/parameters/test_sensor_run").unwrap_or_default();
    if st.contains("act 1") {
        return Err("Pablo self-test stream is active".into());
    }
    Ok(())
}

/// Triple-buffered shared-memory preview: a 4 KiB header + 3 XRGB8888 slots.
struct Shm {
    ptr: *mut u8,
    len: usize,
    slot_bytes: usize,
    next: u32,
}

const SHM_HDR: usize = 4096;

impl Shm {
    fn create(path: &str, w: usize, h: usize) -> Result<Shm, String> {
        let slot_bytes = w * h * 4;
        let len = SHM_HDR + 3 * slot_bytes;
        let f = std::fs::OpenOptions::new().read(true).write(true).create(true).truncate(true).open(path)
            .map_err(|e| format!("{path}: {e}"))?;
        f.set_len(len as u64).map_err(|e| e.to_string())?;
        use std::os::fd::AsRawFd;
        let ptr = unsafe {
            libc::mmap(std::ptr::null_mut(), len, libc::PROT_READ | libc::PROT_WRITE, libc::MAP_SHARED, f.as_raw_fd(), 0)
        };
        if ptr == libc::MAP_FAILED {
            return Err("mmap shm failed".into());
        }
        let s = Shm { ptr: ptr as *mut u8, len, slot_bytes, next: 0 };
        let hdr = s.hdr();
        hdr[0..4].copy_from_slice(b"S22P");
        hdr[4..8].copy_from_slice(&1u32.to_le_bytes());
        hdr[8..12].copy_from_slice(&(w as u32).to_le_bytes());
        hdr[12..16].copy_from_slice(&(h as u32).to_le_bytes());
        hdr[16..20].copy_from_slice(&((w * 4) as u32).to_le_bytes());
        hdr[20..24].copy_from_slice(&fourcc(b"XR24").to_le_bytes());
        hdr[24..28].copy_from_slice(&3u32.to_le_bytes());
        hdr[28..32].copy_from_slice(&(slot_bytes as u32).to_le_bytes());
        Ok(s)
    }
    fn hdr(&self) -> &mut [u8] {
        unsafe { std::slice::from_raw_parts_mut(self.ptr, SHM_HDR) }
    }
    fn slot(&mut self) -> &mut [u32] {
        let off = SHM_HDR + self.next as usize * self.slot_bytes;
        unsafe { std::slice::from_raw_parts_mut(self.ptr.add(off) as *mut u32, self.slot_bytes / 4) }
    }
    /// Publish the slot just written: seq (u64 @32), latest slot (u32 @40), frame-start/publish ns.
    fn publish(&mut self, seq: u64, frame_start: u64, now: u64) {
        let slot = self.next;
        std::sync::atomic::fence(Ordering::Release);
        let h = self.hdr();
        h[40..44].copy_from_slice(&slot.to_le_bytes());
        h[48..56].copy_from_slice(&frame_start.to_le_bytes());
        h[56..64].copy_from_slice(&now.to_le_bytes());
        std::sync::atomic::fence(Ordering::Release);
        h[32..40].copy_from_slice(&seq.to_le_bytes());
        self.next = (slot + 1) % 3;
    }
}

impl Drop for Shm {
    fn drop(&mut self) {
        unsafe { libc::munmap(self.ptr as *mut libc::c_void, self.len) };
    }
}

fn xrgb_to_i420(src: &[u32], w: usize, h: usize, out: &mut Vec<u8>) {
    let (cw, ch) = (w / 2, h / 2);
    out.resize(w * h + 2 * cw * ch, 0);
    let (y_pl, rest) = out.split_at_mut(w * h);
    let (u_pl, v_pl) = rest.split_at_mut(cw * ch);
    for y in 0..h {
        for x in 0..w {
            let p = src[y * w + x];
            let (r, g, b) = (((p >> 16) & 255) as i32, ((p >> 8) & 255) as i32, (p & 255) as i32);
            y_pl[y * w + x] = (((66 * r + 129 * g + 25 * b + 128) >> 8) + 16) as u8;
        }
    }
    for y in 0..ch {
        for x in 0..cw {
            let p = src[2 * y * w + 2 * x];
            let (r, g, b) = (((p >> 16) & 255) as i32, ((p >> 8) & 255) as i32, (p & 255) as i32);
            u_pl[y * cw + x] = (((-38 * r - 74 * g + 112 * b + 128) >> 8) + 128) as u8;
            v_pl[y * cw + x] = (((112 * r - 94 * g - 18 * b + 128) >> 8) + 128) as u8;
        }
    }
}

struct FrameRec {
    fcount: u32,
    t_dq: u64,
    t_pub: u64,
    proc_ms: f64,
    error: bool,
}

/// Sensor AE: exposure first (up to 30 ms), then analog gain; damped, 10 % tolerance.
struct SensorAe {
    exp_us: i64,
    again: f64,
    max_again: f64,
    target: f32,
    updates: u32,
    errors: Vec<String>,
}

impl SensorAe {
    fn plan(&mut self, raw_green_mean: f32) -> Vec<(u32, i64)> {
        let m = raw_green_mean.max(1e-4);
        let ratio = (self.target / m).clamp(0.5, 2.0) as f64;
        if (ratio - 1.0).abs() < 0.10 {
            return vec![];
        }
        let total = (self.exp_us as f64 * self.again * ratio).clamp(100.0, 30000.0 * self.max_again);
        let exp = total.min(30000.0).max(100.0).round() as i64;
        let gain = (total / exp as f64).clamp(1.0, self.max_again);
        let mut c = vec![];
        if exp != self.exp_us {
            c.push((CID_SENSOR_SET_AE_TARGET, exp));
        }
        let gp = (gain * 1000.0).round() as i64;
        if gp != (self.again * 1000.0).round() as i64 {
            c.extend([(CID_SENSOR_SET_ANALOG_GAIN, gp - 1), (CID_SENSOR_SET_ANALOG_GAIN, gp),
                      (CID_SENSOR_SET_DIGITAL_GAIN, 1000)]);
        }
        self.exp_us = exp;
        self.again = gain;
        c
    }
}

fn dump_structs() {
    let hex = |b: &[u8]| b.iter().map(|x| format!("{x:02x}")).collect::<String>();
    let addr = 0x1122_3344_5566_7788u64;
    let (arr, hdr) = make_ext_controls(&[(CID_SENSOR_SET_AE_TARGET, 10000), (CID_SENSOR_SET_ANALOG_GAIN, 1999)], addr);
    let v = json!({
        "ioctls": {"S_FMT": VIDIOC_S_FMT, "REQBUFS": VIDIOC_REQBUFS, "QBUF": VIDIOC_QBUF, "DQBUF": VIDIOC_DQBUF,
                   "STREAMON": VIDIOC_STREAMON, "STREAMOFF": VIDIOC_STREAMOFF, "S_PARM": VIDIOC_S_PARM,
                   "G_CTRL": VIDIOC_G_CTRL, "S_CTRL": VIDIOC_S_CTRL, "S_INPUT": VIDIOC_S_INPUT,
                   "S_EXT_CTRLS": VIDIOC_S_EXT_CTRLS, "HEAP_ALLOC": DMA_HEAP_IOCTL_ALLOC, "SYNC": DMA_BUF_IOCTL_SYNC},
        "s_input_rear": s_input_value(1, 0, 1, 1), "s_input_front": s_input_value(1, 1, 2, 1),
        "size_rear": sensor_size_value(2040, 1532), "size_front": sensor_size_value(1824, 1368),
        "fmt_leader": hex(&make_format_mplane(BUF_TYPE_OUTPUT_MPLANE, 2040, 1532, PIX_SRGB36P_SP, 2, 0)),
        "fmt_vc0": hex(&make_format_mplane(BUF_TYPE_CAPTURE_MPLANE, 2040, 1532, PIX_SBGGR16, 2, vc0_stride(2040) as u32)),
        "reqbufs": hex(&make_reqbufs(4, BUF_TYPE_CAPTURE_MPLANE)),
        "parm": hex(&make_streamparm_capture(30)),
        "control": hex(&make_control(CID_IS_S_STREAM, 1)),
        "buffer": hex(&make_buffer(3, BUF_TYPE_OUTPUT_MPLANE, addr, 2)),
        "planes": hex(&make_planes(&[(7, 0x10_0000, 0x10_0000), (9, 57344, 0)])),
        "shot_rear": hex(&build_shot(2040, 1532, PIX_SBGGR16, 0)),
        "shot_front": hex(&build_shot(1824, 1368, PIX_SBGGR16, 1)),
        "ext_array": hex(&arr), "ext_hdr": hex(&hdr),
        "stride_rear": vc0_stride(2040), "stride_front": vc0_stride(1824),
        "leader_img_rear": page_round(leader_image_size(2040, 1532)),
    });
    println!("{v}");
}

/// Develop-speed microbenchmark without the camera: a dma-heap buffer of the
/// rear frame size, synthetic Bayer data, timed sync / develop / copy.
fn bench(argv: &[String]) {
    let reps: usize = argv.first().and_then(|s| s.parse().ok()).unwrap_or(30);
    if let Some(c) = argv.get(1) {
        set_affinity(c).expect("affinity");
    }
    let (w, h) = (2040usize, 1532usize);
    let stride = vc0_stride(w as u32);
    let len = stride * h;
    let db = DmaBuf::alloc(page_round(len) + VC0_GUARD_BYTES).expect("dma alloc");
    let mut heap = vec![0u8; len];
    for y in 0..h {
        for x in 0..w {
            let v = (64 + ((x * 7 + y * 3) % 900)) as u16;
            heap[y * stride + 2 * x..y * stride + 2 * x + 2].copy_from_slice(&v.to_le_bytes());
        }
    }
    db.write(&heap);
    let mut out = vec![0u32; (w / 2) * (h / 2)];
    let mut res = serde_json::Map::new();
    let time = |f: &mut dyn FnMut()| -> f64 {
        let mut v: Vec<f64> = (0..reps).map(|_| { let t = mono_ns(); f(); (mono_ns() - t) as f64 / 1e6 }).collect();
        pct(&mut v, 0.5)
    };
    res.insert("sync_start_end_ms".into(), json!(time(&mut || { db.sync(DMA_BUF_SYNC_READ); db.sync(DMA_BUF_SYNC_READ | DMA_BUF_SYNC_END); })));
    res.insert("memcpy_dmabuf_to_heap_ms".into(), json!(time(&mut || { heap.copy_from_slice(db.slice(len)); })));
    let mut tmp = vec![0u8; len];
    res.insert("memcpy_heap_to_heap_ms".into(), json!(time(&mut || { tmp.copy_from_slice(&heap); })));
    for scale in [2usize, 4] {
        let luts = if scale == 2 { process::build_luts(1, 2, 1.8, 1.6, 4.0) } else { process::build_luts(4, 8, 1.8, 1.6, 4.0) };
        let (ow, oh) = (w / scale, h / scale);
        let a = time(&mut || { process::develop(db.slice(len), stride, Cfa::Gbrg, scale, &mut out, ow, oh, &luts, 1); });
        res.insert(format!("bin{scale}_1thread_from_dmabuf_ms"), json!(a));
        for cpus in [vec![4usize, 5], vec![4, 5, 6], vec![4, 5, 6, 7], vec![0, 1, 2, 3]] {
            let pool = process::Pool::new(&cpus);
            let b = time(&mut || { pool.develop(db.slice(len), stride, Cfa::Gbrg, scale, &mut out, ow, oh, &luts); });
            res.insert(format!("bin{scale}_pool{:?}_ms", cpus).replace(' ', ""), json!(b));
        }
    }
    res.insert("luts_build_ms".into(), json!(time(&mut || { let _ = process::build_luts(1, 2, 1.8, 1.6, 4.0); })));
    let mut i420 = Vec::new();
    res.insert("xrgb_to_i420_1020x766_ms".into(), json!(time(&mut || xrgb_to_i420(&out, 1020, 766, &mut i420))));
    println!("{}", Value::Object(res));
}

fn main() {
    let argv: Vec<String> = std::env::args().collect();
    match argv.get(1).map(|s| s.as_str()) {
        Some("dump-structs") => return dump_structs(),
        Some("bench") => return bench(&argv[2..]),
        Some("stream") => {}
        _ => usage(),
    }
    let args = parse(&argv[2..]);
    let code = run(&args);
    std::process::exit(code);
}

fn run(args: &Args) -> i32 {
    let mut report = serde_json::Map::new();
    let p = match preset(&args.sensor) {
        Some(p) => p,
        None => usage(),
    };
    unsafe {
        libc::signal(libc::SIGINT, on_stop as *const () as libc::sighandler_t);
        libc::signal(libc::SIGTERM, on_stop as *const () as libc::sighandler_t);
        libc::signal(libc::SIGUSR1, on_still as *const () as libc::sighandler_t);
        libc::signal(libc::SIGUSR2, on_rec as *const () as libc::sighandler_t);
        libc::signal(libc::SIGPIPE, libc::SIG_IGN);
    }
    let mut cpu_list = vec![4usize, 5, 6, 7];
    if let Some(c) = &args.cpus {
        match set_affinity(c) {
            Ok(v) => { report.insert("cpus".into(), json!(v)); cpu_list = v; }
            Err(e) => { println!("{}", json!({"ok": false, "error": e})); return 1; }
        }
    }
    let pool = if args.threads > 1 {
        let cpus: Vec<usize> = cpu_list.iter().cycle().take(args.threads).copied().collect();
        report.insert("worker_cpus".into(), json!(cpus));
        Some(process::Pool::new(&cpus))
    } else {
        None
    };
    let cfa = if p.bayer == "GBRG" { Cfa::Gbrg } else { Cfa::Grbg };
    let (ow, oh) = (p.width as usize / args.scale, p.height as usize / args.scale);
    let (rn, gn) = if args.scale == 2 { (1, 2) } else { (4, 8) };
    report.insert("sensor".into(), json!(p.name));
    report.insert("mode".into(), json!(format!("{}x{}@{}", p.width, p.height, p.fps)));
    report.insert("preview".into(), json!(format!("{ow}x{oh} XRGB8888 (bin {}x{})", args.scale, args.scale)));
    report.insert("buffers".into(), json!({"leader": args.leader_bufs, "vc0": args.vc_bufs}));
    report.insert("threads".into(), json!(args.threads));

    let mut shm = match &args.shm {
        Some(path) => match Shm::create(path, ow, oh) {
            Ok(s) => Some(s),
            Err(e) => { println!("{}", json!({"ok": false, "error": e})); return 1; }
        },
        None => None,
    };
    let mut local = vec![0u32; ow * oh];

    // Encoder feed: a writer thread with a 2-deep queue; frames are dropped when it is full.
    let (fifo_tx, fifo_thread) = match &args.fifo {
        Some(path) => {
            let mut f = match std::fs::OpenOptions::new().write(true).open(path) {
                Ok(f) => f,
                Err(e) => { println!("{}", json!({"ok": false, "error": format!("{path}: {e}")})); return 1; }
            };
            let (tx, rx) = std::sync::mpsc::sync_channel::<Vec<u8>>(2);
            let h = std::thread::spawn(move || {
                let mut n = 0u64;
                for buf in rx {
                    if f.write_all(&buf).is_err() {
                        break;
                    }
                    n += 1;
                }
                n
            });
            (Some(tx), Some(h))
        }
        None => (None, None),
    };
    let mut fifo_dropped = 0u64;
    let mut fifo_conv_ms: Vec<f64> = vec![];

    let mut s = Session::new(&p, args.leader_bufs, args.vc_bufs);
    if let Err(e) = prechecks(&s, args.skip_node_check) {
        println!("{}", json!({"ok": false, "error": e}));
        return 1;
    }
    let t0 = mono_ns();
    let hard_deadline = Instant::now()
        + if args.until_signal {
            std::time::Duration::from_secs(365 * 86400)
        } else {
            std::time::Duration::from_secs_f64(if args.seconds > 0.0 { args.seconds } else { args.frames as f64 / 10.0 } + 30.0)
        };
    let mut recorder: Option<Recorder> = None;
    let mut rec_seen = REC_REQ.load(Ordering::SeqCst);
    let mut still_writes: Vec<std::thread::JoinHandle<()>> = vec![];
    let mut error: Option<String> = None;
    let mut frames: Vec<FrameRec> = vec![];
    let mut leader_ts: HashMap<u32, u64> = HashMap::new();
    let mut leader_metas: Vec<Vec<u8>> = vec![];
    let mut recent_fcounts: Vec<u32> = vec![];
    let mut ts_off: Option<usize> = None;
    let mut fc_off: Option<usize> = None;
    let mut disp = AutoDisplay::new();
    let mut sae = SensorAe { exp_us: args.exposure_us.max(100).min(30000), again: args.again.clamp(1.0, p.max_again),
                             max_again: p.max_again, target: 0.16, updates: 0, errors: vec![] };
    if args.exposure_us == 0 {
        sae.exp_us = 10000;
    }
    if args.again == 0.0 {
        sae.again = 2.0;
    }
    let mut luts = process::build_luts(rn, gn, disp.wb_r, disp.wb_b, disp.gain);
    let mut stills: Vec<(u64, Vec<u8>, u32)> = vec![];
    let mut still_pending = 0usize;
    let mut still_trigger_ns = 0u64;
    let mut still_seen = 0u32;
    let mut still_report: Vec<Value> = vec![];
    let (mut cpu0, mut sys0, mut t_steady0) = (0.0, (0, 0), 0u64);
    let mut start_ms = 0.0;
    let mut first_ms = 0.0;
    let mut last_stats = Stats::default();
    let mut ae_trace: Vec<Value> = vec![];

    let res: Result<(), String> = (|| {
        s.start()?;
        start_ms = (mono_ns() - t0) as f64 / 1e6;
        // initial exposure/gain, as the reviewed client does once after the blocking start
        let mut init = vec![(CID_SENSOR_SET_AE_TARGET, sae.exp_us)];
        let gp = (sae.again * 1000.0).round() as i64;
        init.extend([(CID_SENSOR_SET_ANALOG_GAIN, gp - 1), (CID_SENSOR_SET_ANALOG_GAIN, gp), (CID_SENSOR_SET_DIGITAL_GAIN, 1000)]);
        if let Err(e) = s.set_cis(&init) {
            sae.errors.push(format!("initial: {e}"));
        }
        let mut got_first = false;
        let mut last_frame = Instant::now();
        let mut seq = 0u64;
        loop {
            if STOP.load(Ordering::SeqCst) {
                break;
            }
            if Instant::now() > hard_deadline {
                return Err("hard deadline".into());
            }
            if !args.until_signal && args.frames > 0 && frames.len() as u64 >= args.frames && still_pending == 0 {
                break;
            }
            if !args.until_signal && args.seconds > 0.0 && got_first && (mono_ns() - t_steady0) as f64 / 1e9 >= args.seconds && still_pending == 0 {
                break;
            }
            let limit = if got_first { 5.0 } else { 20.0 };
            if last_frame.elapsed().as_secs_f64() > limit {
                return Err(format!("no VC0 frame within {limit} s"));
            }
            let mut pfd = [
                libc::pollfd { fd: s.vfd, events: libc::POLLIN, revents: 0 },
                libc::pollfd { fd: s.lfd, events: libc::POLLOUT, revents: 0 },
            ];
            let r = unsafe { libc::poll(pfd.as_mut_ptr(), 2, 200) };
            if r < 0 {
                let e = std::io::Error::last_os_error();
                if e.raw_os_error() == Some(libc::EINTR) {
                    continue;
                }
                return Err(format!("poll: {e}"));
            }
            if pfd[1].revents & libc::POLLOUT != 0 {
                if let Some((idx, _)) = s.dqbuf(true)? {
                    let now = mono_ns();
                    let meta = &s.lbufs[idx].1;
                    meta.sync(DMA_BUF_SYNC_READ);
                    let m = meta.slice(SIZE_OF_META_PLANE).to_vec();
                    meta.sync(DMA_BUF_SYNC_READ | DMA_BUF_SYNC_END);
                    if ts_off.is_none() {
                        ts_off = (0..SIZE_OF_META_PLANE - 8).step_by(8)
                            .find(|&o| { let v = get64(&m, o); v <= now && v + 2_000_000_000 > now });
                    }
                    if fc_off.is_none() && leader_metas.len() < 40 {
                        leader_metas.push(m.clone());
                    }
                    if let (Some(to), Some(fo)) = (ts_off, fc_off) {
                        leader_ts.insert(get32(&m, fo), get64(&m, to));
                    }
                    s.queue_leader(idx)?;
                }
            }
            if pfd[0].revents & libc::POLLIN == 0 {
                continue;
            }
            let (idx, flags) = match s.dqbuf(false)? {
                Some(x) => x,
                None => continue,
            };
            let t_dq = mono_ns();
            last_frame = Instant::now();
            if !got_first {
                got_first = true;
                first_ms = (t_dq - t0) as f64 / 1e6;
                cpu0 = rusage_ms();
                sys0 = sys_cpu();
                t_steady0 = t_dq;
                if args.until_signal {
                    event(json!({"event": "first_frame", "ms": (first_ms * 10.0).round() / 10.0, "start_ms": (start_ms * 10.0).round() / 10.0,
                                 "sensor": p.name, "preview": [ow, oh]}));
                }
            }
            let img = &s.vbufs[idx].0;
            let vmeta = &s.vbufs[idx].1;
            vmeta.sync(DMA_BUF_SYNC_READ);
            let fcount = get32(vmeta.slice(16), 4);
            vmeta.sync(DMA_BUF_SYNC_READ | DMA_BUF_SYNC_END);
            recent_fcounts.push(fcount);
            // discover the frameCount offset in the leader meta: the u32 that equals VC0 fcounts
            // (a u32 whose values increase from meta to meta and are VC0 fcounts)
            if fc_off.is_none() && leader_metas.len() >= 12 {
                let k = leader_metas.len();
                fc_off = (0..18384).step_by(4).find(|&o| {
                    let vals: Vec<u32> = leader_metas.iter().map(|m| get32(m, o)).collect();
                    let increasing = vals.windows(2).all(|w| w[1] > w[0]);
                    let hits = vals.iter().filter(|v| recent_fcounts.contains(v)).count();
                    increasing && hits + 2 >= k
                });
                if let (Some(o), Some(to)) = (fc_off, ts_off) {
                    for m in &leader_metas {
                        leader_ts.insert(get32(m, o), get64(m, to));
                    }
                }
                if fc_off.is_none() && k >= 40 {
                    leader_metas.clear();
                }
            }
            let error_frame = flags & BUF_FLAG_ERROR != 0;
            let tp0 = mono_ns();
            if !error_frame {
                img.sync(DMA_BUF_SYNC_READ);
                let src = img.slice(s.image_len);
                // stills: full-resolution raw copies from the running stream
                let req = STILL_REQ.load(Ordering::SeqCst);
                if req != still_seen || args.still_at == Some(frames.len() as u64) {
                    if req != still_seen {
                        still_seen = req;
                    }
                    still_pending = args.still_frames;
                    still_trigger_ns = t_dq;
                }
                if still_pending > 0 {
                    stills.push((t_dq, src.to_vec(), fcount));
                    still_pending -= 1;
                    if still_pending == 0 {
                        let in_ram_ms = (mono_ns() - still_trigger_ns) as f64 / 1e6;
                        still_report.push(json!({"trigger_to_last_raw_in_ram_ms": in_ram_ms, "frames": args.still_frames}));
                        if let Some(dir) = &args.still_dir {
                            // live mode: written now by a helper thread (tmpfs), the stream keeps running
                            let burst = std::mem::take(&mut stills);
                            let base = format!("{dir}/shot-{}.raw", unix_ms());
                            let meta = json!({"width": p.width, "height": p.height, "stride": s.stride, "pixfmt": "BYR2", "bayer": p.bayer,
                                              "sequence": burst.first().map(|b| b.2), "timestamp": still_trigger_ns as f64 / 1e9, "sensor": p.sensor,
                                              "mode": format!("{}x{}@{}", p.width, p.height, p.fps), "source": "s22-campreview live stream",
                                              "exposure_us": sae.exp_us, "again": sae.again, "frames": burst.len()});
                            let (sensor, dir) = (p.name, dir.clone());
                            still_writes.retain(|h| !h.is_finished());
                            still_writes.push(std::thread::spawn(move || {
                                let t = Instant::now();
                                let _ = std::fs::create_dir_all(&dir);
                                let mut ok = !burst.is_empty();
                                for (i, (_, raw, _)) in burst.iter().enumerate() {
                                    ok &= std::fs::write(format!("{base}.{i}"), raw).is_ok();
                                }
                                ok &= std::fs::hard_link(format!("{base}.0"), &base).is_ok()
                                    && std::fs::write(format!("{base}.json"), serde_json::to_string_pretty(&meta).unwrap()).is_ok();
                                event(json!({"event": "still", "ok": ok, "raw": base, "frames": burst.len(), "sensor": sensor,
                                             "in_ram_ms": (in_ram_ms * 10.0).round() / 10.0, "write_ms": t.elapsed().as_millis() as u64,
                                             "exposure_us": meta["exposure_us"], "again": meta["again"]}));
                            }));
                        }
                    }
                }
                if !args.no_develop {
                    let out: &mut [u32] = match shm.as_mut() { Some(sh) => sh.slot(), None => &mut local };
                    last_stats = match &pool {
                        Some(pl) => pl.develop(src, s.stride, cfa, args.scale, out, ow, oh, &luts),
                        None => process::develop(src, s.stride, cfa, args.scale, out, ow, oh, &luts, 1),
                    };
                }
                img.sync(DMA_BUF_SYNC_READ | DMA_BUF_SYNC_END);
            }
            s.queue_vc(idx)?;
            seq += 1;
            let t_pub = mono_ns();
            if !error_frame && !args.no_develop {
                if let Some(sh) = shm.as_mut() {
                    sh.publish(seq, leader_ts.get(&fcount).copied().unwrap_or(0), t_pub);
                }
                if let Some(tx) = &fifo_tx {
                    let tc = mono_ns();
                    let src: &[u32] = match shm.as_ref() {
                        Some(sh) => unsafe {
                            let slot = (sh.next + 2) % 3;
                            std::slice::from_raw_parts(sh.ptr.add(SHM_HDR + slot as usize * sh.slot_bytes) as *const u32, ow * oh)
                        },
                        None => &local,
                    };
                    let mut buf = Vec::new();
                    if args.fifo_format == "i420" {
                        xrgb_to_i420(src, ow & !1, oh & !1, &mut buf);
                    } else {
                        buf.extend(src.iter().flat_map(|p| p.to_le_bytes()));
                    }
                    fifo_conv_ms.push((mono_ns() - tc) as f64 / 1e6);
                    if tx.try_send(buf).is_err() {
                        fifo_dropped += 1;
                    }
                }
                // video: SIGUSR2 toggles; frames go to the encoder thread (dropped when it lags)
                let rq = REC_REQ.load(Ordering::SeqCst);
                if rq != rec_seen {
                    rec_seen = rq;
                    match recorder.take() {
                        Some(r) => {
                            drop(r.tx);
                            let _ = r.dropped;
                            std::thread::spawn(move || { let _ = r.thread.join(); });
                        }
                        None => match &args.rec_dir {
                            Some(dir) => match start_recorder(dir, ow, oh, args.rec_rotate, args.rec_mirror, args.rec_quality, p.name) {
                                Ok(r) => recorder = Some(r),
                                Err(e) => event(json!({"event": "rec_stopped", "ok": false, "error": e})),
                            },
                            None => event(json!({"event": "rec_stopped", "ok": false, "error": "no --rec-dir"})),
                        },
                    }
                }
                if let Some(r) = recorder.as_mut() {
                    let src: &[u32] = match shm.as_ref() {
                        Some(sh) => unsafe {
                            let slot = (sh.next + 2) % 3;
                            std::slice::from_raw_parts(sh.ptr.add(SHM_HDR + slot as usize * sh.slot_bytes) as *const u32, ow * oh)
                        },
                        None => &local,
                    };
                    if r.tx.try_send(src.to_vec()).is_err() {
                        r.dropped += 1;
                    }
                }
                // display AWB / gain for the next frame
                disp.update(&last_stats);
                luts = process::build_luts(rn, gn, disp.wb_r, disp.wb_b, disp.gain);
                if args.ae && frames.len() as u64 % args.ae_every == 0 && frames.len() > 2 {
                    let g = last_stats.means().1;
                    let c = sae.plan(g);
                    if !c.is_empty() {
                        match s.set_cis(&c) {
                            Ok(()) => sae.updates += 1,
                            Err(e) => { sae.errors.push(e); }
                        }
                        if ae_trace.len() < 40 {
                            ae_trace.push(json!({"frame": frames.len(), "raw_green": (g * 1000.0).round() / 1000.0,
                                                 "exp_us": sae.exp_us, "again": (sae.again * 100.0).round() / 100.0}));
                        }
                    }
                }
            }
            frames.push(FrameRec { fcount, t_dq, t_pub, proc_ms: (t_pub - tp0) as f64 / 1e6, error: error_frame });
            if args.until_signal && frames.len() > 20000 {
                // service mode runs for hours: keep the stats bounded
                frames.drain(..10000);
                leader_ts.retain(|&fc, _| fc.wrapping_add(2000) > fcount);
                recent_fcounts.drain(..recent_fcounts.len().saturating_sub(64));
            }
        }
        Ok(())
    })();
    let t_end = mono_ns();
    if let Some(r) = recorder.take() {
        drop(r.tx);
        let _ = r.thread.join();
    }
    for h in still_writes {
        let _ = h.join();
    }
    let cpu1 = rusage_ms();
    let sys1 = sys_cpu();
    let ts_stop = mono_ns();
    s.stop();
    let stop_ms = (mono_ns() - ts_stop) as f64 / 1e6;
    if let Err(e) = res {
        error = Some(e);
    }
    drop(fifo_tx);
    let fifo_frames = fifo_thread.map(|h| h.join().unwrap_or(0));

    // stills are written only after teardown (as the reviewed client does)
    let mut still_paths = vec![];
    for (i, (ts, raw, fc)) in stills.iter().enumerate() {
        let path = if i == 0 { args.still_out.clone() } else { format!("{}.{}", args.still_out, i - 1) };
        let meta = json!({"width": p.width, "height": p.height, "stride": s.stride, "pixfmt": "BYR2", "bayer": p.bayer,
                          "sequence": fc, "timestamp": *ts as f64 / 1e9, "sensor": p.sensor,
                          "mode": format!("{}x{}@{}", p.width, p.height, p.fps), "source": "s22-campreview live stream",
                          "exposure_us": sae.exp_us, "again": sae.again});
        if std::fs::write(&path, raw).is_ok() && std::fs::write(format!("{path}.json"), serde_json::to_string_pretty(&meta).unwrap()).is_ok() {
            still_paths.push(path);
        }
    }

    report.insert("start_ms".into(), json!((start_ms * 10.0).round() / 10.0));
    report.insert("first_frame_ms".into(), json!((first_ms * 10.0).round() / 10.0));
    report.insert("stop_ms".into(), json!((stop_ms * 10.0).round() / 10.0));
    let n = frames.len();
    if n > 1 {
        let span = (t_end - t_steady0) as f64 / 1e9;
        let mut dts: Vec<f64> = frames.windows(2).map(|w| (w[1].t_dq - w[0].t_dq) as f64 / 1e6).collect();
        let mut proc: Vec<f64> = frames.iter().filter(|f| !f.error).map(|f| f.proc_ms).collect();
        let mut lat_dq: Vec<f64> = vec![];
        let mut lat_pub: Vec<f64> = vec![];
        for f in &frames {
            if let Some(&ts) = leader_ts.get(&f.fcount) {
                if ts > 0 && ts < f.t_dq {
                    lat_dq.push((f.t_dq - ts) as f64 / 1e6);
                    lat_pub.push((f.t_pub - ts) as f64 / 1e6);
                }
            }
        }
        let fc_gaps = frames.windows(2).filter(|w| w[1].fcount != w[0].fcount.wrapping_add(1)).count();
        let (st, si) = (sys1.0 - sys0.0, sys1.1 - sys0.1);
        report.insert("steady".into(), json!({
            "frames": n, "seconds": (span * 1000.0).round() / 1000.0,
            "fps": ((n as f64 - 1.0) / span * 100.0).round() / 100.0,
            "dt_ms": {"p50": pct(&mut dts.clone(), 0.5), "p95": pct(&mut dts.clone(), 0.95), "max": pct(&mut dts, 1.0)},
            "error_flag_frames": frames.iter().filter(|f| f.error).count(),
            "fcount_gaps": fc_gaps,
            "process_ms": {"p50": pct(&mut proc.clone(), 0.5), "p95": pct(&mut proc.clone(), 0.95), "max": pct(&mut proc, 1.0)},
            "process_cpu_pct_of_one_core": ((cpu1 - cpu0) / (span * 1000.0) * 1000.0).round() / 10.0,
            "system_busy_pct_all_8_cores": if st > 0 { ((1.0 - si as f64 / st as f64) * 1000.0).round() / 10.0 } else { f64::NAN },
        }));
        report.insert("latency_ms".into(), json!({
            "basis": "CSI frame-start timestamp (shot dm.sensor.timeStamp, CLOCK_MONOTONIC) matched by fcount",
            "matched_frames": lat_dq.len(),
            "frame_start_to_dqbuf": {"p50": pct(&mut lat_dq.clone(), 0.5), "p95": pct(&mut lat_dq.clone(), 0.95), "max": pct(&mut lat_dq, 1.0)},
            "frame_start_to_published": {"p50": pct(&mut lat_pub.clone(), 0.5), "p95": pct(&mut lat_pub.clone(), 0.95), "max": pct(&mut lat_pub, 1.0)},
            "offsets": {"dm_timestamp": ts_off, "dm_framecount": fc_off},
        }));
    }
    let (r, g, b) = last_stats.means();
    report.insert("display".into(), json!({"wb_r": disp.wb_r, "wb_b": disp.wb_b, "gain": disp.gain,
                                           "raw_means": [r, g, b], "clipped_quads_sampled": last_stats.clipped}));
    report.insert("sensor_ae".into(), json!({"enabled": args.ae, "exp_us": sae.exp_us, "again": sae.again,
                                             "updates": sae.updates, "errors": sae.errors, "trace": ae_trace}));
    if args.fifo.is_some() {
        report.insert("fifo".into(), json!({"format": args.fifo_format, "written": fifo_frames, "dropped": fifo_dropped,
                                            "convert_ms_p50": pct(&mut fifo_conv_ms, 0.5)}));
    }
    if !stills.is_empty() || args.still_at.is_some() {
        report.insert("stills".into(), json!({"paths": still_paths, "events": still_report}));
    }
    report.insert("teardown_errors".into(), json!(s.teardown_errors));
    let ok = error.is_none() && s.teardown_errors.is_empty() && n > 0;
    if let Some(e) = error {
        report.insert("error".into(), json!(e));
    }
    report.insert("ok".into(), json!(ok));
    println!("{}", Value::Object(report));
    if ok { 0 } else { 1 }
}
