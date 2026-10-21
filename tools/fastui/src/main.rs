//! fastui: S22 fast-UI spike. Owns the panel through DRM/KMS (card1,
//! exynos-drmdpu), reads the touchscreen through evdev, and draws a
//! full-screen tile grid. Two renderers:
//!   sw   hand-rolled CPU renderer, damage-clipped, copied into dumb buffers;
//!   gpu  frames rendered by the Samsung Vulkan driver in the isolated Bionic
//!        runtime (fastui-vk), shared over a unix socket as dma-bufs.
//! It measures frame time, touch-to-flip latency and CPU use, and prints JSON.
//!
//! usage:
//!   fastui sw  [--seconds S] [--bench N] [--full] [--card /dev/dri/card1] [--touch /dev/input/event7]
//!   fastui gpu --sock PATH --gpu-mode export|import|readback [--seconds S] [--bench N]
//!   fastui info
//! Stops early when /run/fastui-stop exists. Needs DRM master (run with the
//! desktop switched away, see fastui-trial.sh).
mod drm;
mod render;
mod touch;

use drm::{now_us, Buffer, Card};
use render::{copy_rects, tile_rects, Canvas, Rect};
use std::io;
use std::os::fd::RawFd;
use touch::{Touch, TouchDev};

struct Args {
    cmd: String,
    seconds: u64,
    bench: usize,
    full: bool,
    card: String,
    touch: String,
    sock: String,
    gpu_mode: String,
}

fn parse() -> Args {
    let v: Vec<String> = std::env::args().collect();
    let get = |k: &str| v.iter().position(|a| a == k).and_then(|i| v.get(i + 1)).cloned();
    Args {
        cmd: v.get(1).cloned().unwrap_or_else(|| "info".into()),
        seconds: get("--seconds").and_then(|s| s.parse().ok()).unwrap_or(30),
        bench: get("--bench").and_then(|s| s.parse().ok()).unwrap_or(0),
        full: v.iter().any(|a| a == "--full"),
        card: get("--card").unwrap_or_else(|| "/dev/dri/card1".into()),
        touch: get("--touch").unwrap_or_else(|| "/dev/input/event7".into()),
        sock: get("--sock").unwrap_or_else(|| "/srv/s22/gpu-compat-20260921/llama-vulkan/tmp/fastui.sock".into()),
        gpu_mode: get("--gpu-mode").unwrap_or_else(|| "export".into()),
    }
}

#[derive(Default, Clone)]
struct Frame {
    kind: &'static str,
    render_us: u64,
    copy_us: u64,
    flip_us: u64,  // submit (page_flip ioctl) -> flip complete event
    frame_us: u64, // start of work -> flip complete
    input_us: Option<u64>,
    bytes: usize,
    gpu_us: u64,
}

trait Presenter {
    fn present(&mut self, card: &Card, hl: Option<usize>, damage: &[Rect], status: &str, f: &mut Frame) -> io::Result<()>;
    fn back_fb(&self) -> u32;
    fn swap(&mut self);
    fn first_fb(&self) -> u32;
}

// ---------------------------------------------------------------- CPU path
struct SwPresenter {
    canvas: Canvas,
    bufs: Vec<Buffer>,
    back: usize,
    prev_damage: Vec<Rect>,
}

impl Presenter for SwPresenter {
    fn present(&mut self, _card: &Card, hl: Option<usize>, damage: &[Rect], status: &str, f: &mut Frame) -> io::Result<()> {
        let t0 = now_us();
        for d in damage {
            self.canvas.draw(d, hl, status);
        }
        let t1 = now_us();
        // The back buffer is two frames old: it also needs last frame's damage.
        let mut rects: Vec<Rect> = damage.to_vec();
        for r in &self.prev_damage {
            if !rects.contains(r) {
                rects.push(*r);
            }
        }
        let b = &self.bufs[self.back];
        f.bytes = copy_rects(&self.canvas, b.map, b.pitch as usize, &rects);
        let t2 = now_us();
        self.prev_damage = damage.to_vec();
        f.render_us = t1 - t0;
        f.copy_us = t2 - t1;
        Ok(())
    }
    fn back_fb(&self) -> u32 {
        self.bufs[self.back].fb_id
    }
    fn swap(&mut self) {
        self.back ^= 1;
    }
    fn first_fb(&self) -> u32 {
        self.bufs[0].fb_id
    }
}

// ---------------------------------------------------------------- GPU path
mod ipc {
    use std::io;
    use std::os::fd::RawFd;
    pub const MAGIC: u32 = 0x4653_5455;
    pub fn send(fd: RawFd, msg: &[u32; 16], fds: &[RawFd]) -> io::Result<()> {
        unsafe {
            let mut iov = libc::iovec { iov_base: msg.as_ptr() as *mut _, iov_len: 64 };
            let mut cbuf = [0u8; 256];
            let mut mh: libc::msghdr = std::mem::zeroed();
            mh.msg_iov = &mut iov;
            mh.msg_iovlen = 1;
            if !fds.is_empty() {
                let len = libc::CMSG_SPACE((fds.len() * 4) as u32) as usize;
                mh.msg_control = cbuf.as_mut_ptr() as *mut _;
                mh.msg_controllen = len as _;
                let c = libc::CMSG_FIRSTHDR(&mh);
                (*c).cmsg_level = libc::SOL_SOCKET;
                (*c).cmsg_type = libc::SCM_RIGHTS;
                (*c).cmsg_len = libc::CMSG_LEN((fds.len() * 4) as u32) as _;
                std::ptr::copy_nonoverlapping(fds.as_ptr(), libc::CMSG_DATA(c) as *mut RawFd, fds.len());
            }
            if libc::sendmsg(fd, &mh, 0) != 64 {
                return Err(io::Error::last_os_error());
            }
        }
        Ok(())
    }
    pub fn recv(fd: RawFd, msg: &mut [u32; 16], fds: &mut Vec<RawFd>) -> io::Result<()> {
        unsafe {
            let mut iov = libc::iovec { iov_base: msg.as_mut_ptr() as *mut _, iov_len: 64 };
            let mut cbuf = [0u8; 256];
            let mut mh: libc::msghdr = std::mem::zeroed();
            mh.msg_iov = &mut iov;
            mh.msg_iovlen = 1;
            mh.msg_control = cbuf.as_mut_ptr() as *mut _;
            mh.msg_controllen = cbuf.len() as _;
            let n = libc::recvmsg(fd, &mut mh, libc::MSG_WAITALL);
            if n != 64 {
                return Err(if n < 0 { io::Error::last_os_error() } else { io::Error::new(io::ErrorKind::UnexpectedEof, "short ipc read") });
            }
            let mut c = libc::CMSG_FIRSTHDR(&mh);
            while !c.is_null() {
                if (*c).cmsg_level == libc::SOL_SOCKET && (*c).cmsg_type == libc::SCM_RIGHTS {
                    let n = ((*c).cmsg_len as usize - libc::CMSG_LEN(0) as usize) / 4;
                    let p = libc::CMSG_DATA(c) as *const RawFd;
                    for i in 0..n {
                        fds.push(*p.add(i));
                    }
                }
                c = libc::CMSG_NXTHDR(&mh, c);
            }
        }
        Ok(())
    }
}

struct GpuPresenter {
    conn: RawFd,
    bufs: Vec<Buffer>,
    back: usize,
    frame: u32,
    /// readback mode: shared memfds the GPU process copies into; we copy them to the dumb buffers
    shm: Vec<*mut u8>,
}

impl GpuPresenter {
    fn connect(card: &Card, sock: &str, mode: &str) -> io::Result<GpuPresenter> {
        let _ = std::fs::remove_file(sock);
        let lfd = unsafe { libc::socket(libc::AF_UNIX, libc::SOCK_STREAM | libc::SOCK_CLOEXEC, 0) };
        let mut sa: libc::sockaddr_un = unsafe { std::mem::zeroed() };
        sa.sun_family = libc::AF_UNIX as _;
        for (i, b) in sock.bytes().enumerate() {
            sa.sun_path[i] = b as _;
        }
        if unsafe { libc::bind(lfd, &sa as *const _ as *const libc::sockaddr, std::mem::size_of::<libc::sockaddr_un>() as u32) } != 0 {
            return Err(io::Error::last_os_error());
        }
        let c = std::ffi::CString::new(sock).unwrap();
        unsafe {
            libc::chmod(c.as_ptr(), 0o666);
            libc::listen(lfd, 1);
        }
        eprintln!("fastui: waiting for fastui-vk on {sock}");
        let mut pfd = libc::pollfd { fd: lfd, events: libc::POLLIN, revents: 0 };
        if unsafe { libc::poll(&mut pfd, 1, 30_000) } <= 0 {
            return Err(io::Error::new(io::ErrorKind::TimedOut, "no GPU client within 30 s"));
        }
        let conn = unsafe { libc::accept4(lfd, std::ptr::null_mut(), std::ptr::null_mut(), libc::SOCK_CLOEXEC) };
        unsafe { libc::close(lfd) };
        let _ = std::fs::remove_file(sock);
        if conn < 0 {
            return Err(io::Error::last_os_error());
        }
        let m = match mode {
            "export" => 0,
            "import" => 1,
            _ => 2,
        };
        let mut bufs = Vec::new();
        let mut shm = Vec::new();
        let mut hello = [0u32; 16];
        hello[0] = ipc::MAGIC;
        hello[1] = m;
        hello[2] = card.width;
        hello[3] = card.height;
        hello[5] = 2;
        let mut out_fds = Vec::new();
        if m != 0 {
            // import / readback: the display owns the buffers (dumb), the GPU process gets dma-bufs.
            for _ in 0..2 {
                let b = card.create_dumb()?;
                hello[4] = b.pitch;
                hello[6] = b.size as u32;
                if m == 1 {
                    out_fds.push(card.export_fd(b.handle)?);
                } else {
                    // readback: the sandbox cannot mmap the dumb dma-buf for writing (EACCES),
                    // so it gets a plain shared memfd per buffer instead.
                    let len = b.pitch as usize * card.height as usize;
                    let name = std::ffi::CString::new("fastui-readback").unwrap();
                    let fd = unsafe { libc::memfd_create(name.as_ptr(), libc::MFD_CLOEXEC) };
                    if fd < 0 || unsafe { libc::ftruncate(fd, len as i64) } != 0 {
                        return Err(io::Error::last_os_error());
                    }
                    let p = unsafe { libc::mmap(std::ptr::null_mut(), len, libc::PROT_READ | libc::PROT_WRITE, libc::MAP_SHARED, fd, 0) };
                    if p == libc::MAP_FAILED {
                        return Err(io::Error::last_os_error());
                    }
                    shm.push(p as *mut u8);
                    out_fds.push(fd);
                }
                bufs.push(b);
            }
        } else {
            hello[4] = card.width * 4;
        }
        ipc::send(conn, &hello, &out_fds)?;
        for fd in out_fds {
            unsafe { libc::close(fd) };
        }
        let mut ack = [0u32; 16];
        let mut fds = Vec::new();
        ipc::recv(conn, &mut ack, &mut fds)?;
        if ack[0] != ipc::MAGIC || ack[1] != 0 {
            return Err(io::Error::new(io::ErrorKind::Other, format!("GPU client failed: status {} (vk result {})", ack[1], ack[7] as i32)));
        }
        eprintln!("fastui: GPU client ready: mode={mode} pitch={} nbuf={} size={} fds={} memtype={} flags=0x{:x}", ack[2], ack[3], ack[4], fds.len(), ack[8], ack[9]);
        if m == 0 {
            for fd in &fds {
                let b = card.import_fd(*fd, ack[2], ack[4] as usize);
                unsafe { libc::close(*fd) };
                match b {
                    Ok(b) => bufs.push(b),
                    Err(e) => return Err(io::Error::new(e.kind(), format!("PRIME import of Vulkan dma-buf into card1 failed: {e}"))),
                }
            }
        }
        Ok(GpuPresenter { conn, bufs, back: 0, frame: 0, shm })
    }
}

impl Presenter for GpuPresenter {
    fn present(&mut self, _card: &Card, hl: Option<usize>, _damage: &[Rect], _status: &str, f: &mut Frame) -> io::Result<()> {
        let t0 = now_us();
        let mut msg = [0u32; 16];
        msg[0] = 1;
        msg[1] = self.back as u32;
        msg[2] = hl.map(|h| h as u32).unwrap_or(u32::MAX);
        msg[3] = self.frame;
        self.frame += 1;
        ipc::send(self.conn, &msg, &[])?;
        let mut rep = [0u32; 16];
        let mut fds = Vec::new();
        ipc::recv(self.conn, &mut rep, &mut fds)?;
        if rep[1] != 0 {
            return Err(io::Error::new(io::ErrorKind::Other, format!("GPU render failed: {}", rep[1] as i32)));
        }
        f.gpu_us = rep[2] as u64;
        f.render_us = now_us() - t0; // IPC round trip incl. GPU submit/wait and the sandbox-side readback copy
        if !self.shm.is_empty() {
            // copy #2: shared memfd -> dumb scanout buffer
            let b = &self.bufs[self.back];
            let len = b.pitch as usize * 2340;
            let t1 = now_us();
            unsafe { std::ptr::copy_nonoverlapping(self.shm[self.back], b.map, len) };
            f.copy_us = rep[3] as u64 + (now_us() - t1);
            f.bytes = 2 * len;
        }
        Ok(())
    }
    fn back_fb(&self) -> u32 {
        self.bufs[self.back].fb_id
    }
    fn swap(&mut self) {
        self.back ^= 1;
    }
    fn first_fb(&self) -> u32 {
        self.bufs[0].fb_id
    }
}

impl Drop for GpuPresenter {
    fn drop(&mut self) {
        let msg = [0u32; 16];
        let _ = ipc::send(self.conn, &msg, &[]);
        unsafe { libc::close(self.conn) };
    }
}

// ---------------------------------------------------------------- loop
fn flip(card: &Card, p: &mut dyn Presenter, f: &mut Frame, t_start: u64) -> io::Result<()> {
    let ts = now_us();
    card.page_flip(p.back_fb(), 1)?;
    let ev = card.wait_flip(1000)?;
    let te = now_us();
    p.swap();
    f.flip_us = te - ts;
    f.frame_us = te - t_start;
    let _ = ev;
    Ok(())
}

fn cpu_s() -> f64 {
    let mut ru: libc::rusage = unsafe { std::mem::zeroed() };
    unsafe { libc::getrusage(libc::RUSAGE_SELF, &mut ru) };
    ru.ru_utime.tv_sec as f64 + ru.ru_utime.tv_usec as f64 / 1e6 + ru.ru_stime.tv_sec as f64 + ru.ru_stime.tv_usec as f64 / 1e6
}

fn stats(name: &str, mut v: Vec<u64>) -> String {
    if v.is_empty() {
        return format!("\"{name}\": null");
    }
    v.sort();
    let p = |q: f64| v[((v.len() - 1) as f64 * q).round() as usize] as f64 / 1000.0;
    let mean = v.iter().sum::<u64>() as f64 / v.len() as f64 / 1000.0;
    format!("\"{name}\": {{\"n\": {}, \"p50_ms\": {:.2}, \"p95_ms\": {:.2}, \"max_ms\": {:.2}, \"mean_ms\": {:.2}}}", v.len(), p(0.5), p(0.95), p(1.0), mean)
}

fn report(label: &str, frames: &[Frame], wall_s: f64, cpu: f64, extra: &str) {
    let pick = |f: &dyn Fn(&Frame) -> Option<u64>| frames.iter().filter_map(|x| f(x)).collect::<Vec<u64>>();
    let bytes: usize = frames.iter().map(|f| f.bytes).sum();
    println!(
        "{{\"label\": \"{label}\", \"frames\": {}, \"wall_s\": {:.2}, \"cpu_s\": {:.3}, \"cpu_pct_one_core\": {:.2}, \"avg_bytes_copied\": {}, {}, {}, {}, {}, {}, {}{}}}",
        frames.len(),
        wall_s,
        cpu,
        cpu / wall_s * 100.0,
        if frames.is_empty() { 0 } else { bytes / frames.len() },
        stats("render", pick(&|f| Some(f.render_us))),
        stats("copy", pick(&|f| Some(f.copy_us))),
        stats("gpu", pick(&|f| if f.gpu_us > 0 { Some(f.gpu_us) } else { None })),
        stats("flip_wait", pick(&|f| Some(f.flip_us))),
        stats("frame", pick(&|f| Some(f.frame_us))),
        stats("touch_to_flip", pick(&|f| f.input_us)),
        extra
    );
}

fn stop_requested() -> bool {
    std::path::Path::new("/run/fastui-stop").exists()
}

fn run(a: &Args) -> io::Result<()> {
    if a.cmd == "dump" {
        // Offscreen render of the scene (PPM on stdout) to check layout without a panel.
        let (w, h) = (1080, 2340);
        let mut c = Canvas::new(w, h);
        c.draw(&Rect::new(0, 0, w, h), Some(5), "FRAME 1.2 MS  TOUCH 9.8 MS");
        let mut out = format!("P6 {w} {h} 255\n").into_bytes();
        for p in &c.px {
            out.extend_from_slice(&[(p >> 16) as u8, (p >> 8) as u8, *p as u8]);
        }
        use std::io::Write;
        std::io::stdout().write_all(&out)?;
        return Ok(());
    }
    let card = Card::open(&a.card)?;
    eprintln!("fastui: card {} crtc {} connector {} mode {} {}x{}", a.card, card.crtc_id, card.connector_id, card.mode_name(), card.width, card.height);
    if a.cmd == "info" {
        let b = card.create_dumb()?;
        let fd = card.export_fd(b.handle);
        println!("{{\"crtc\": {}, \"connector\": {}, \"mode\": \"{}\", \"w\": {}, \"h\": {}, \"dumb_pitch\": {}, \"dumb_size\": {}, \"dumb_prime_export\": {}}}",
            card.crtc_id, card.connector_id, card.mode_name(), card.width, card.height, b.pitch, b.size, fd.is_ok());
        card.destroy(&b);
        return Ok(());
    }
    let (w, h) = (card.width as i32, card.height as i32);
    let full = Rect::new(0, 0, w, h);
    let tiles = tile_rects(w, h);
    let bar = Rect::new(0, 0, w, render::STATUS_H);
    let t_setup = now_us();
    let mut p: Box<dyn Presenter> = if a.cmd == "gpu" {
        Box::new(GpuPresenter::connect(&card, &a.sock, &a.gpu_mode)?)
    } else {
        let bufs = vec![card.create_dumb()?, card.create_dumb()?];
        let mut c = Canvas::new(w, h);
        c.draw(&full, None, "");
        Box::new(SwPresenter { canvas: c, bufs, back: 0, prev_damage: vec![full] })
    };
    // First frame: full draw, mode-set onto our buffer.
    let mut f0 = Frame { kind: "first", ..Default::default() };
    p.present(&card, None, &[full], "READY", &mut f0)?;
    card.set_crtc(p.back_fb())?;
    p.swap();
    let first_ms = (now_us() - t_setup) as f64 / 1000.0;
    eprintln!("fastui: first frame on panel {:.1} ms after setup start (render {} us copy {} us)", first_ms, f0.render_us, f0.copy_us);
    // Second buffer also needs full content in the sw path.
    let mut warm = Frame::default();
    p.present(&card, None, &[full], "READY", &mut warm)?;
    flip(&card, p.as_mut(), &mut warm, now_us())?;

    let mut frames: Vec<Frame> = Vec::new();
    let cpu0 = cpu_s();
    let w0 = now_us();
    let mut hl: Option<usize> = None;
    let mut status = String::from("READY");
    if a.bench > 0 {
        for i in 0..a.bench {
            let new = Some(i % tiles.len());
            let mut dmg = vec![bar];
            if let Some(o) = hl {
                dmg.push(tiles[o]);
            }
            dmg.push(tiles[new.unwrap()]);
            if a.full {
                dmg = vec![full];
            }
            hl = new;
            let mut f = Frame { kind: "bench", ..Default::default() };
            let ts = now_us();
            p.present(&card, hl, &dmg, &status, &mut f)?;
            flip(&card, p.as_mut(), &mut f, ts)?;
            status = format!("FRAME {:.1} MS", f.frame_us as f64 / 1000.0);
            frames.push(f);
            if stop_requested() {
                break;
            }
        }
        let wall = (now_us() - w0) as f64 / 1e6;
        let label = format!("{}-bench{}", a.cmd, if a.full { "-full" } else { "-damage" });
        report(&label, &frames, wall, cpu_s() - cpu0, &format!(", \"first_frame_ms\": {:.1}, \"gpu_mode\": \"{}\"", first_ms, if a.cmd == "gpu" { &a.gpu_mode } else { "-" }));
        return Ok(());
    }

    // Interactive: touch drives highlight.
    let mut td = TouchDev::open(&a.touch, card.width, card.height)?;
    eprintln!("fastui: touch {} ranges {:?}; running {} s", a.touch, td.ranges(), a.seconds);
    let deadline = now_us() + a.seconds * 1_000_000;
    let mut evs = Vec::new();
    while now_us() < deadline && !stop_requested() {
        let mut pfd = libc::pollfd { fd: td.fd, events: libc::POLLIN, revents: 0 };
        let r = unsafe { libc::poll(&mut pfd, 1, 200) };
        if r <= 0 {
            continue;
        }
        evs.clear();
        td.read(&mut evs);
        // Coalesce: only the newest state matters; keep the earliest input timestamp of the batch.
        let mut new = hl;
        let mut t_in: Option<u64> = None;
        let mut kind = "touch-move";
        for e in &evs {
            match *e {
                Touch::Down(x, y, t) => {
                    new = tiles.iter().position(|r| r.contains(x, y));
                    t_in.get_or_insert(t);
                    kind = "touch-down";
                }
                Touch::Move(x, y, t) => {
                    let n = tiles.iter().position(|r| r.contains(x, y));
                    if n != new {
                        t_in.get_or_insert(t);
                    }
                    new = n;
                }
                Touch::Up(t) => {
                    if new.is_some() {
                        t_in.get_or_insert(t);
                    }
                    new = None;
                    kind = "touch-up";
                }
            }
        }
        if new == hl {
            continue;
        }
        let mut dmg = vec![bar];
        if let Some(o) = hl {
            dmg.push(tiles[o]);
        }
        if let Some(n) = new {
            dmg.push(tiles[n]);
        }
        hl = new;
        let mut f = Frame { kind, ..Default::default() };
        let ts = now_us();
        p.present(&card, hl, &dmg, &status, &mut f)?;
        flip(&card, p.as_mut(), &mut f, ts)?;
        let tdone = now_us();
        f.input_us = t_in.map(|t| tdone.saturating_sub(t));
        status = format!("FRAME {:.1} MS  TOUCH {:.1} MS", f.frame_us as f64 / 1000.0, f.input_us.unwrap_or(0) as f64 / 1000.0);
        eprintln!("fastui: {} hl={:?} frame {:.2} ms (render {:.2} copy {:.2} gpu {:.2} flip {:.2}) touch->flip {:.2} ms",
            f.kind, hl, f.frame_us as f64 / 1e3, f.render_us as f64 / 1e3, f.copy_us as f64 / 1e3, f.gpu_us as f64 / 1e3, f.flip_us as f64 / 1e3, f.input_us.unwrap_or(0) as f64 / 1e3);
        frames.push(f);
    }
    let wall = (now_us() - w0) as f64 / 1e6;
    report(&format!("{}-interactive", a.cmd), &frames, wall, cpu_s() - cpu0, &format!(", \"first_frame_ms\": {:.1}", first_ms));
    Ok(())
}

fn main() {
    let a = parse();
    if let Err(e) = run(&a) {
        eprintln!("fastui: error: {e}");
        std::process::exit(1);
    }
}

#[allow(dead_code)]
fn _unused(_: RawFd) {}
