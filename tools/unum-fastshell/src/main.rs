//! unum-fastshell: the S22's fast touch shell.
//!
//! One process on the native root: a CPU renderer with damage tracking that
//! draws straight into two KMS dumb buffers on card1 (exynos-drmdpu) and
//! page-flips them, evdev touch, and a seatd session next to sway. sway stays
//! running underneath (foot and other Wayland apps); the shell hands the panel
//! to sway and takes it back with seatd session switches.
//!
//! usage:
//!   unum-fastshell                 run (started by s22-touchd, backend "fastshell")
//!   unum-fastshell dump DIR        offscreen PNGs of every screen (no panel needed)
//!   unum-fastshell bench [N]       offscreen full-frame render timings per page
//! env: S22_TOUCH_RUN (touchd run dir, native path), FASTSHELL_CHROOT,
//!      S22_SHELL_API, S22_SHELL_S22D, S22_SHELL_PHONED, S22_SHELL_SESSION.
mod agent;
mod buttons;
mod camera;
// config: FASTSHELL_CONFIG (JSON; see config.rs and PORTING.md). The
//      built-in defaults are the S22's, so the S22 needs no config file.
mod config;
mod device;
mod ctl;
mod drm;
mod font;
mod gfx;
mod haptic;
mod idle;
mod kbd;
mod net;
mod seat;
mod touch;
mod ui;

use drm::{now_us, Buffer, Card};
use gfx::{copy_rects, Canvas, Rect};
use net::{Msg, Net};
use seat::{Seat, SeatEvent};
use std::io::Write;
use std::os::fd::RawFd;
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use ui::{App, Effect, Page, H, W};

static STOP: AtomicBool = AtomicBool::new(false);

extern "C" fn on_signal(_: libc::c_int) {
    STOP.store(true, Ordering::SeqCst);
}

fn env(k: &str, d: &str) -> String {
    std::env::var(k).unwrap_or_else(|_| d.to_string())
}

fn log(msg: &str) {
    let t = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs_f64();
    eprintln!("{t:.3} fastshell: {msg}");
}

struct Presenter {
    card: Card,
    bufs: Vec<Buffer>,
    back: usize,
    prev: Vec<Rect>,
    pending: bool,
    pending_mark: Option<(u64, &'static str)>,
    /// publish ns of the camera frame in the pending flip
    pending_cam: Option<u64>,
    need_modeset: bool,
    /// canvas offset on the panel; None when the panel is 1080x2340 (S22)
    off: Option<(i32, i32)>,
}

/// Paths and environment of the sway session underneath (for wtype / swaymsg).
struct Sway {
    chroot: String,
}

impl Sway {
    fn session(&self) -> (String, String) {
        let s = std::fs::read_to_string(format!("{}/run/s22-desktop/session", self.chroot)).unwrap_or_default();
        let mut l = s.lines();
        (l.next().unwrap_or("").trim().to_string(), l.next().unwrap_or("").trim().to_string())
    }

    fn run(&self, argv: &[&str]) -> bool {
        let (wl, sock) = self.session();
        let mut c = std::process::Command::new("chroot");
        c.arg(&self.chroot).arg("/usr/bin/env").arg("-i").arg("HOME=/root").arg("PATH=/usr/local/bin:/usr/bin:/bin").arg("XDG_RUNTIME_DIR=/run/user/0").arg("LANG=C.UTF-8");
        if self.chroot.is_empty() {
            // no chroot: sway (if any) runs on the host
            c = std::process::Command::new("/usr/bin/env");
            c.arg("-i").arg(format!("HOME={}", std::env::var("HOME").unwrap_or_else(|_| "/root".into()))).arg("PATH=/usr/local/bin:/usr/bin:/bin");
            c.arg(format!("XDG_RUNTIME_DIR={}", std::env::var("XDG_RUNTIME_DIR").unwrap_or_else(|_| "/run/user/0".into()))).arg("LANG=C.UTF-8");
        }
        if !wl.is_empty() {
            c.arg(format!("WAYLAND_DISPLAY={wl}"));
        }
        if !sock.is_empty() {
            c.arg(format!("SWAYSOCK={sock}"));
        }
        if let Some(bus) = sway_bus() {
            c.arg(format!("DBUS_SESSION_BUS_ADDRESS={bus}"));
        }
        c.args(argv).stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null());
        c.status().map(|s| s.success()).unwrap_or(false)
    }
}

/// The session bus of the sway session (squeekboard lives there).
fn sway_bus() -> Option<String> {
    for e in std::fs::read_dir("/proc").ok()?.flatten() {
        let p = e.path();
        if std::fs::read_to_string(p.join("comm")).map(|c| c.trim() == "sway").unwrap_or(false) {
            let env = std::fs::read(p.join("environ")).ok()?;
            for kv in env.split(|b| *b == 0) {
                if let Some(v) = kv.strip_prefix(b"DBUS_SESSION_BUS_ADDRESS=") {
                    return Some(String::from_utf8_lossy(v).into_owned());
                }
            }
        }
    }
    None
}

fn write_state(path: &str, v: &str) {
    let tmp = format!("{path}.tmp");
    if std::fs::write(&tmp, format!("{v}\n")).is_ok() {
        let _ = std::fs::rename(&tmp, path);
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("dump") => return dump(args.get(2).map(String::as_str).unwrap_or(".")),
        Some("bench") => return bench(args.get(2).and_then(|s| s.parse().ok()).unwrap_or(20)),
        Some("-h") | Some("--help") => {
            println!("unum-fastshell [dump DIR | bench N]");
            return;
        }
        _ => {}
    }
    if let Err(e) = run() {
        log(&format!("error: {e}"));
        std::process::exit(1);
    }
}

fn run() -> std::io::Result<()> {
    unsafe {
        libc::signal(libc::SIGTERM, on_signal as *const () as usize);
        libc::signal(libc::SIGINT, on_signal as *const () as usize);
        libc::signal(libc::SIGHUP, on_signal as *const () as usize);
        libc::signal(libc::SIGPIPE, libc::SIG_IGN);
    }
    let (cfg, cfg_path, cfg_err) = config::Config::load();
    config::init(cfg);
    let cfg = config::get();
    if let Some(e) = cfg_err {
        log(&format!("config: {e}"));
    }
    let chroot = cfg.chroot.clone();
    // musl reads zoneinfo from /usr/share/zoneinfo, which the native root may lack:
    // use the chroot's copy by absolute path, else the POSIX fallback rule (Athens on the S22).
    let tz = cfg.tz.clone();
    if !tz.starts_with('/') && !tz.contains(',') && !std::path::Path::new(&format!("/usr/share/zoneinfo/{tz}")).exists() {
        let alt = format!("{chroot}/usr/share/zoneinfo/{tz}");
        std::env::set_var("TZ", if std::path::Path::new(&alt).exists() { alt } else { cfg.tz_fallback.clone() });
    }
    unsafe { tzset() };
    let run_dir = cfg.run_dir();
    let state_dir = cfg.state_dir.clone();
    let _ = std::fs::create_dir_all(&state_dir);
    let _ = std::fs::write(&cfg.pid_file, format!("{}\n", std::process::id()));
    let drm_card = config::detect_drm_card(std::path::Path::new("/sys/class/drm"), std::path::Path::new("/dev/dri"), &cfg.drm_card).unwrap_or_else(|| "/dev/dri/card1".into());
    let sys = config::sys();
    log(&format!(
        "config {}: drm {drm_card}, touch {}, battery {}, backlight {}, thermal {:?}",
        cfg_path.as_deref().unwrap_or("(built-in defaults)"),
        touch::find_touchscreen().unwrap_or_else(|| "none".into()),
        sys.battery.as_ref().map(|p| p.display().to_string()).unwrap_or_else(|| "none".into()),
        sys.backlight.as_ref().map(|p| p.display().to_string()).unwrap_or_else(|| "none".into()),
        sys.thermal.iter().map(|z| z.0.as_str()).collect::<Vec<_>>()
    ));
    let display_state = cfg.display_state_file.as_str();
    let mut pipe = [0 as RawFd; 2];
    unsafe { libc::pipe2(pipe.as_mut_ptr(), libc::O_CLOEXEC | libc::O_NONBLOCK) };
    let (tx, rx) = std::sync::mpsc::channel::<Msg>();
    let netw = Net {
        tx,
        wake_fd: pipe[1],
        api: cfg.api.clone(),
        s22d: cfg.s22d.clone(),
        phoned: cfg.phoned.clone(),
        session: cfg.session.clone(),
        touchd_sock: format!("{run_dir}/ctl.sock"),
        turn: Arc::new(AtomicU64::new(0)),
    };
    ctl::serve(format!("{run_dir}/shell.sock"), netw.clone())?;
    let mut app = App::new(Some(netw.clone()), &run_dir, &chroot);
    app.idle_path = env("FASTSHELL_IDLE_CFG", &format!("{state_dir}/screen-timeout.json"));
    app.load_idle();
    app.cam_orient_path = format!("{state_dir}/camera.json");
    app.load_orient();
    app.load_haptics();
    let mut haptic = haptic::Haptic::new(haptic::SOCK);
    let mut cam_child: Option<i32> = None;
    log(&format!("screen timeout: unlocked {} s, locked {} s (0 = never)", app.idle_cfg.unlocked_s, app.idle_cfg.locked_s));
    net::refresh_agent(&netw);
    net::fetch_history(&netw);

    let sway = Arc::new(Sway { chroot: chroot.clone() });
    let mut seat = Seat::connect(&cfg.seatd_socket())?;
    seat.open_seat()?;
    let active_flag = Arc::new(AtomicBool::new(false));
    let my_session = Arc::new(AtomicI32::new(0));
    let activating = Arc::new(AtomicBool::new(false));
    let request_activation = {
        let sway = sway.clone();
        let flag = active_flag.clone();
        let sess = my_session.clone();
        let busy = activating.clone();
        move || {
            if busy.swap(true, Ordering::SeqCst) {
                return;
            }
            let sway = sway.clone();
            let flag = flag.clone();
            let sess = sess.clone();
            let busy = busy.clone();
            std::thread::spawn(move || {
                let known = sess.load(Ordering::SeqCst);
                let mut cands: Vec<i32> = if known > 0 { vec![known] } else { vec![] };
                cands.extend((2..=12).filter(|&n| n != known));
                for n in cands {
                    if flag.load(Ordering::SeqCst) {
                        break;
                    }
                    sway.run(&["wtype", "-k", &format!("XF86Switch_VT_{n}")]);
                    for _ in 0..25 {
                        if flag.load(Ordering::SeqCst) {
                            sess.store(n, Ordering::SeqCst);
                            break;
                        }
                        std::thread::sleep(Duration::from_millis(20));
                    }
                    if flag.load(Ordering::SeqCst) {
                        log(&format!("active as seat session {n}"));
                        break;
                    }
                }
                busy.store(false, Ordering::SeqCst);
            });
        }
    };
    request_activation();
    // The shell wants the panel unless it handed it to sway on purpose (terminal,
    // sway desktop). At boot sway may not take the switch keysym yet: retry.
    let mut want_active = true;
    let mut last_try = Instant::now();

    let mut pres: Option<Presenter> = None;
    let mut cv = Canvas::new(W, H);
    let mut tdev = touch::find_touchscreen().and_then(|p| touch::TouchDev::open(&p, W as u32, H as u32).ok());
    let mut tmap = cfg.touch_map();
    let mut evs = Vec::new();
    let mut sevs = Vec::new();
    let mut next_tick = Instant::now();
    let mut next_sys = Instant::now() + Duration::from_secs(10);
    let mut next_agent = Instant::now() + Duration::from_secs(15);
    let mut last_anim = Instant::now();
    let mut opened_device = false;
    let mut handing: Option<Instant> = None;
    let mut lat_log = std::fs::OpenOptions::new().create(true).append(true).open(format!("{state_dir}/latency.jsonl")).ok();
    // physical buttons: read the key nodes directly (see buttons.rs for the hand-over with s22-buttons)
    let key_nodes = buttons::find_key_nodes("/sys/class/input");
    let mut keys: Vec<buttons::KeyDev> = key_nodes.iter().filter_map(|p| buttons::KeyDev::open(p).map_err(|e| log(&format!("key node {p}: {e}"))).ok()).collect();
    let mut kevs: Vec<(buttons::Key, bool, u64)> = Vec::new();
    let mut machine = buttons::Machine::new(buttons::Timing::default());
    app.buttons_cfg = env("FASTSHELL_BUTTONS_CFG", &format!("{state_dir}/buttons.json"));
    app.buttons_enabled = buttons::load_enabled(&app.buttons_cfg);
    app.level = buttons::read_level(buttons::SAY_LEVEL);
    let mut claimed = app.buttons_enabled && !keys.is_empty();
    buttons::claim(claimed);
    log(&format!("buttons: nodes {:?}, handled by the shell: {claimed}, level {}", key_nodes, app.level));
    let mut shot_due = false;
    log(&format!("started pid {} run_dir {run_dir}", std::process::id()));

    while !STOP.load(Ordering::SeqCst) {
        // ---- poll
        let mut fds = vec![
            libc::pollfd { fd: pipe[0], events: libc::POLLIN, revents: 0 },
            libc::pollfd { fd: seat.fd, events: libc::POLLIN, revents: 0 },
            libc::pollfd { fd: tdev.as_ref().map(|t| t.fd).unwrap_or(-1), events: libc::POLLIN, revents: 0 },
            libc::pollfd { fd: pres.as_ref().map(|p| p.card.fd).unwrap_or(-1), events: libc::POLLIN, revents: 0 },
        ];
        for k in &keys {
            fds.push(libc::pollfd { fd: k.fd, events: libc::POLLIN, revents: 0 });
        }
        let mut timeout = next_tick.saturating_duration_since(Instant::now()).as_millis().min(1000) as i32;
        if app.animating || app.repeat_at.is_some() || app.hold.is_some() {
            timeout = timeout.min(16);
        }
        if app.vf.is_some() && app.page == Page::Camera && app.display_on && app.active {
            // viewfinder: look for the next published frame every 4 ms (30 fps source)
            timeout = timeout.min(4);
        }
        if let Some(dl) = machine.next_deadline() {
            timeout = timeout.min((dl.saturating_sub(now_us()) / 1000) as i32 + 1);
        }
        if let Some(dl) = app.overlay_deadline() {
            timeout = timeout.min(dl.saturating_duration_since(Instant::now()).as_millis() as i32 + 1);
        }
        let r = unsafe { libc::poll(fds.as_mut_ptr(), fds.len() as _, timeout) };
        if r < 0 {
            continue;
        }
        // ---- flip complete
        if fds[3].revents & libc::POLLIN != 0 {
            if let Some(p) = pres.as_mut() {
                if let Ok(Some(_)) = p.card.read_events() {
                    if p.pending {
                        p.pending = false;
                        p.back ^= 1;
                        let tdone = now_us();
                        if let Some(pubns) = p.pending_cam.take() {
                            let age = (tdone as f64 - pubns as f64 / 1000.0) / 1000.0;
                            app.cam_shown.push((tdone, age));
                            if app.cam_shown.len() > 3000 {
                                app.cam_shown.drain(..1000);
                            }
                        }
                        if let Some((t_in, kind)) = p.pending_mark.take() {
                            let ms = tdone.saturating_sub(t_in) as f64 / 1000.0;
                            if ms < 2000.0 {
                                if kind == "shutter" {
                                    log(&format!("camera: shutter -> thumbnail on the panel {ms:.0} ms"));
                                }
                                app.stats.lat.push((kind, ms));
                                if app.stats.lat.len() > 4000 {
                                    app.stats.lat.drain(..1000);
                                }
                                if let Some(f) = lat_log.as_mut() {
                                    let _ = writeln!(f, "{{\"t\":{tdone},\"kind\":\"{kind}\",\"touch_to_flip_ms\":{ms:.2},\"page\":\"{}\"}}", app.page.name());
                                }
                            }
                        }
                    }
                }
            }
        }
        // ---- seat
        if fds[1].revents & (libc::POLLIN | libc::POLLHUP) != 0 {
            sevs.clear();
            seat.read(&mut sevs)?;
            for e in sevs.drain(..) {
                match e {
                    SeatEvent::Enable => {
                        log("seat enabled");
                        active_flag.store(true, Ordering::SeqCst);
                        app.active = true;
                        if !opened_device {
                            seat.open_device(&drm_card)?;
                            opened_device = true;
                        } else if let Some(p) = pres.as_mut() {
                            p.need_modeset = true;
                            p.pending = false;
                            p.prev = vec![Rect::new(0, 0, W, H)];
                            app.display_on = true;
                            write_state(display_state, "on");
                            app.dirty_all();
                        }
                        // drain touches that happened under sway
                        if let Some(t) = tdev.as_mut() {
                            evs.clear();
                            t.read(&mut evs);
                        }
                    }
                    SeatEvent::Disable => {
                        log("seat disabled (sway takes the panel)");
                        active_flag.store(false, Ordering::SeqCst);
                        app.active = false;
                        if let Some(p) = pres.as_mut() {
                            p.pending = false;
                        }
                        seat.ack_disable()?;
                    }
                    SeatEvent::Device(_, fd) => {
                        if fd < 0 {
                            log("seatd gave no card fd");
                            continue;
                        }
                        let card = Card::from_fd(fd)?;
                        log(&format!("{drm_card} crtc {} conn {} mode {} {}x{}", card.crtc_id, card.connector_id, card.mode_name(), card.width, card.height));
                        let (pw, ph) = (card.width as i32, card.height as i32);
                        let off = if (pw, ph) == (W, H) {
                            None
                        } else {
                            tmap = cfg.touch_map().with_panel(pw, ph);
                            log(&format!("panel {pw}x{ph} is not {W}x{H}: canvas centred at {},{}", tmap.ox, tmap.oy));
                            Some((tmap.ox, tmap.oy))
                        };
                        let bufs = vec![card.create_dumb()?, card.create_dumb()?];
                        pres = Some(Presenter { card, bufs, back: 0, prev: vec![Rect::new(0, 0, W, H)], pending: false, pending_mark: None, pending_cam: None, need_modeset: true, off });
                        write_state(display_state, "on");
                        app.dirty_all();
                    }
                    SeatEvent::Error(c) => log(&format!("seatd error {c}")),
                    SeatEvent::Opened(n) => log(&format!("seat {n} opened")),
                    SeatEvent::Closed => {
                        log("seatd closed the connection");
                        return Ok(());
                    }
                }
            }
        }
        // ---- touch
        if fds[2].revents & libc::POLLIN != 0 {
            if let Some(t) = tdev.as_mut() {
                evs.clear();
                t.read(&mut evs);
                if app.active {
                    for e in evs.drain(..) {
                        app.touch(tmap.apply(e));
                    }
                }
            }
        }
        // ---- physical buttons
        let key_ctx = |app: &App| buttons::Ctx {
            display_on: if app.active { app.display_on } else { std::fs::read_to_string("/run/s22-display-state").map(|s| s.trim() != "off").unwrap_or(true) },
            ptt_hold: buttons::ptt_hold_mode(buttons::BUTTONS_BASE),
        };
        if fds[4..].iter().any(|f| f.revents & libc::POLLIN != 0) {
            kevs.clear();
            for k in keys.iter_mut() {
                k.read(&mut kevs);
            }
            kevs.sort_by_key(|e| e.2);
            if app.buttons_enabled {
                for &(k, down, t) in &kevs {
                    if down && !machine.any_down() {
                        // others write the level too (s22-buttons fallback, quiet hours): start from the file
                        app.level = buttons::read_level(buttons::SAY_LEVEL);
                    }
                    let ctx = key_ctx(&app);
                    let acts = if down { machine.press(k, t, ctx) } else { machine.release(k, t, ctx) };
                    if !acts.is_empty() {
                        log(&format!("key {} {} -> {:?}", k.name(), if down { "down" } else { "up" }, acts));
                    }
                    app.button_actions(&acts, t);
                }
            }
        }
        if machine.next_deadline().is_some_and(|dl| now_us() >= dl) {
            let acts = machine.tick(now_us(), key_ctx(&app));
            if !acts.is_empty() {
                log(&format!("key hold -> {acts:?}"));
            }
            app.button_actions(&acts, now_us());
        }
        if fds.iter().skip(4).any(|f| f.revents & (libc::POLLERR | libc::POLLHUP | libc::POLLNVAL) != 0) {
            log("a key node went away: reopening");
            keys = buttons::find_key_nodes("/sys/class/input").iter().filter_map(|p| buttons::KeyDev::open(p).ok()).collect();
        }
        if claimed != (app.buttons_enabled && !keys.is_empty()) {
            claimed = !claimed;
            buttons::claim(claimed);
            log(&format!("buttons handled by the shell: {claimed}"));
        }
        app.expire_overlays(Instant::now());
        // ---- messages and calls
        if fds[0].revents & libc::POLLIN != 0 {
            let mut b = [0u8; 256];
            while unsafe { libc::read(pipe[0], b.as_mut_ptr() as *mut _, b.len()) } > 0 {}
        }
        while let Ok(m) = rx.try_recv() {
            match m {
                Msg::Ctl(call, reply) => {
                    let r = app.call(&call);
                    let _ = reply.send(r);
                }
                Msg::Camera(gen, v) => {
                    if v["event"] != "first_frame" || gen != app.cam.gen {
                        log(&format!("camera[{gen}]: {}", v.to_string().chars().take(300).collect::<String>()));
                    } else {
                        log(&format!("camera[{gen}]: first frame {} ms", v["ms"]));
                    }
                    if v["event"] == "exit" && cam_child == v["pid"].as_i64().map(|p| p as i32) {
                        cam_child = None;
                    }
                    app.camera_event(gen, v);
                }
                Msg::CamThumb(px, w, h) => app.camera_thumb(px, w, h),
                Msg::Card(turn, gen, v) => {
                    log(&format!("card {}[{gen}]: {}", if turn { "turn" } else { "listen" }, v.to_string().chars().take(200).collect::<String>()));
                    app.card_event(turn, gen, v);
                }
                Msg::DeviceCap(id, st, v) => {
                    if v["ok"] != true || id.ends_with(".set") {
                        log(&format!("device {id}: {st} {}", v.to_string().chars().take(240).collect::<String>()));
                    }
                    app.dev_answer(id, st, v);
                }
                other => app.on_msg(other),
            }
        }
        // ---- timers
        let now = Instant::now();
        if now >= next_tick {
            next_tick = now + Duration::from_secs(1);
            app.tick();
            if app.display_on && app.active {
                let say = idle::flock_held(&cfg.say_lock);
                if app.idle_check(now, say) {
                    log(&format!("screen timeout: idle {} s, locked {} -> panel off", now.saturating_duration_since(app.last_touch).as_secs(), app.locked));
                    app.screen_timeout_off();
                }
            }
            if now >= next_sys {
                next_sys = now + Duration::from_secs(10);
                if app.display_on {
                    app.load_idle();
                    app.load_haptics();
                    app.read_sys();
                }
            }
            if now >= next_agent {
                next_agent = now + Duration::from_secs(20);
                if app.display_on && app.active {
                    net::refresh_agent(&netw);
                }
            }
        }
        if want_active && !app.active && last_try.elapsed() > Duration::from_secs(6) {
            log("not active yet: asking sway to switch again");
            request_activation();
            last_try = Instant::now();
        }
        app.check_repeat();
        app.check_hold();
        // ---- effects (level first: a wake's DPMS-on blocks ~180 ms and must not delay it)
        let mut effs = std::mem::take(&mut app.effects);
        effs.sort_by_key(|e| !matches!(e, Effect::Level(_) | Effect::PttRelease(_)));
        for e in effs {
            match e {
                Effect::Activate => {
                    want_active = true;
                    if !app.active {
                        request_activation();
                        last_try = Instant::now();
                    }
                }
                Effect::HandToSway => {
                    if app.active {
                        log("handing the panel to sway (session 1)");
                        let _ = seat.switch_session(1);
                        handing = Some(Instant::now());
                        want_active = false;
                    }
                }
                Effect::Display(on) => {
                    if let Some(p) = pres.as_mut() {
                        if on {
                            if !app.display_on {
                                let r = p.card.dpms(true);
                                log(&format!("dpms on: {r:?}"));
                                p.need_modeset = true;
                                p.pending = false;
                                p.prev = vec![Rect::new(0, 0, W, H)];
                                app.display_on = true;
                                app.dirty_all();
                            }
                            write_state(display_state, "on");
                        } else {
                            // draw the lock cover first so it is what comes back
                            if app.display_on && app.active {
                                if p.pending {
                                    let _ = p.card.wait_flip(100);
                                    p.pending = false;
                                    p.back ^= 1;
                                }
                                if frame(&mut app, &mut cv, p).is_ok() && p.pending {
                                    let _ = p.card.wait_flip(100);
                                    p.pending = false;
                                    p.back ^= 1;
                                }
                            }
                            app.display_on = false;
                            let r = p.card.dpms(false);
                            log(&format!("dpms off: {r:?}"));
                            p.pending = false;
                            write_state(display_state, "off");
                        }
                    }
                }
                Effect::Assistant(cmd, envs) => {
                    if envs.iter().any(|(k, _)| k == "S22_PTT") {
                        let _ = std::fs::remove_file(format!("{}/ptt-release", buttons::BUTTONS_BASE));
                    }
                    netw.spawn(move |n| {
                        let mut parts = cmd.split_whitespace();
                        let prog = parts.next().unwrap_or("true").to_string();
                        let st = std::process::Command::new(&prog)
                            .args(parts)
                            .envs(envs)
                            .stdin(std::process::Stdio::null())
                            .stdout(std::process::Stdio::null())
                            .stderr(std::process::Stdio::null())
                            .status();
                        log(&format!("assistant exited: {st:?}"));
                        let last = std::fs::read_to_string(&config::get().assistant_log).ok().and_then(|s| s.lines().last().map(String::from)).unwrap_or_default();
                        n.send(Msg::Assistant(serde_json::from_str(&last).unwrap_or(serde_json::Value::Null)));
                    });
                }
                Effect::PttRelease(held) => {
                    if let Err(e) = buttons::write_ptt_release(buttons::BUTTONS_BASE, held) {
                        log(&format!("ptt release: {e}"));
                    }
                }
                Effect::Level(l) => {
                    let playing = idle::flock_held(&env("FASTSHELL_SAY_LOCK", "/run/s22-say.lock")) || buttons::playback_running();
                    if let Err(e) = buttons::apply_level(l, playing) {
                        log(&format!("level {l}: {e}"));
                    }
                }
                Effect::Native(argv) => {
                    std::thread::spawn(move || {
                        let st = std::process::Command::new(&argv[0]).args(&argv[1..]).stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).status();
                        log(&format!("{argv:?}: {st:?}"));
                    });
                }
                Effect::Screenshot => shot_due = true,
                Effect::Haptic(what) => {
                    haptic.send(what);
                }
                Effect::Spawn(argv) => {
                    {
                        let sw = sway.clone();
                        std::thread::spawn(move || {
                            let mut a: Vec<&str> = argv.iter().map(String::as_str).collect();
                            let mut delay = 300;
                            if let Some(ms) = a.first().and_then(|x| x.strip_prefix("@delay")).and_then(|x| x.parse().ok()) {
                                delay = ms;
                                a.remove(0);
                            }
                            std::thread::sleep(Duration::from_millis(delay));
                            sw.run(&a);
                        });
                    }
                }
            }
        }
        // ---- camera: stream only while its page shows; run the state machine's commands
        app.sync_camera();
        for c in std::mem::take(&mut app.cam_cmds) {
            run_cam(c, &netw, &mut app, &mut cam_child);
        }
        app.camera_frame_due();
        for c in std::mem::take(&mut app.card_cmds) {
            run_card(c, &netw, &mut app);
        }
        // ---- animation
        let dt = last_anim.elapsed().as_secs_f32().min(0.05);
        last_anim = Instant::now();
        if app.animating && app.active && pres.as_ref().is_some_and(|p| !p.pending) {
            app.animate(dt);
        }
        // ---- render
        if handing.is_some_and(|t| t.elapsed() > Duration::from_millis(800)) || !app.active {
            handing = None;
        }
        if app.active && app.display_on && handing.is_none() {
            if let Some(p) = pres.as_mut() {
                if !p.pending && (!app.damage.is_empty() || app.relayout) {
                    if let Err(e) = frame(&mut app, &mut cv, p) {
                        log(&format!("frame: {e}"));
                    }
                }
            }
        }
        for (kind, ms) in std::mem::take(&mut app.lat_unlogged) {
            if let Some(f) = lat_log.as_mut() {
                let _ = writeln!(f, "{{\"t\":{},\"kind\":\"{kind}\",\"touch_to_flip_ms\":{ms:.2},\"page\":\"{}\",\"modeset\":true}}", now_us(), app.page.name());
            }
        }
        // ---- screenshot: once the frame without the level overlay is on the canvas
        if shot_due {
            let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs();
            let name = format!("screenshot-{stamp}.png");
            let rel = format!("{}/{name}", ui::GALLERY);
            if app.active && pres.is_some() && app.display_on {
                if app.damage.is_empty() && !app.relayout {
                    shot_due = false;
                    let (px, sw, sh) = if std::mem::take(&mut app.shot_half) { (half(&cv.px, W, H), W / 2, H / 2) } else { (cv.px.clone(), W, H) };
                    let host = format!("{chroot}{rel}");
                    std::thread::spawn(move || {
                        let t = Instant::now();
                        let r = save_png_px(&px, sw, sh, &host);
                        log(&format!("screenshot {host}: {r:?} in {} ms", t.elapsed().as_millis()));
                    });
                    app.screenshot_taken(&name);
                }
            } else {
                // sway has the panel: grim under sway
                shot_due = false;
                let sw = sway.clone();
                let rel2 = rel.clone();
                std::thread::spawn(move || {
                    let ok = sw.run(&["sh", "-c", &format!("mkdir -p {} && grim {rel2}", ui::GALLERY)]);
                    log(&format!("screenshot (grim) {rel2}: {ok}"));
                });
                app.screenshot_taken(&name);
            }
        }
    }
    log("stopping");
    if let Some(pid) = cam_child {
        unsafe { libc::kill(pid, libc::SIGTERM) };
    }
    let _ = std::fs::remove_file(camera::INHIBIT);
    buttons::claim(false);
    let _ = seat.close_seat();
    let _ = std::fs::remove_file(&cfg.pid_file);
    Ok(())
}

extern "C" {
    fn tzset();
}

/// Run one Agent-card command: s22-assistant's card modes in their own process groups,
/// the release note, STOP (group SIGTERM + the OpenUnum cancel API).
fn run_card(c: agent::CardCmd, netw: &Net, app: &mut App) {
    use agent::CardCmd;
    let assistant = std::env::var("FASTSHELL_ASSISTANT").unwrap_or_else(|_| config::get().assistant.clone());
    let spawn = |turn: bool, gen: u64, args: Vec<String>, app: &mut App| {
        use std::os::unix::process::CommandExt;
        let mut cmd = std::process::Command::new(&assistant);
        cmd.args(&args).stdin(std::process::Stdio::null()).stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::null()).process_group(0);
        match cmd.spawn() {
            Ok(mut ch) => {
                let pid = ch.id() as i32;
                app.card.spawned(turn, gen, pid);
                let out = ch.stdout.take();
                netw.spawn(move |n| {
                    use std::io::BufRead;
                    if let Some(o) = out {
                        for line in std::io::BufReader::new(o).lines().map_while(Result::ok) {
                            if let Ok(v) = serde_json::from_str::<serde_json::Value>(&line) {
                                n.send(Msg::Card(turn, gen, v));
                            }
                        }
                    }
                    let code = ch.wait().ok().and_then(|s| s.code()).unwrap_or(-1);
                    n.send(Msg::Card(turn, gen, serde_json::json!({"event": "exit", "code": code})));
                });
            }
            Err(e) => {
                log(&format!("card: spawn {assistant}: {e}"));
                app.card_event(turn, gen, serde_json::json!({"state": "error", "error": e.to_string()}));
                app.card_event(turn, gen, serde_json::json!({"event": "exit", "code": -1}));
            }
        }
    };
    match c {
        CardCmd::Listen { gen } => {
            let _ = std::fs::remove_file(agent::RELEASE_FILE);
            spawn(false, gen, vec!["--card".into(), "listen".into(), "--stop-file".into(), agent::RELEASE_FILE.into()], app);
        }
        CardCmd::Release => {
            let t = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
            let tmp = format!("{}.tmp", agent::RELEASE_FILE);
            if std::fs::write(&tmp, format!("{{\"t\": {t:.3}}}\n")).is_ok() {
                let _ = std::fs::rename(&tmp, agent::RELEASE_FILE);
            }
        }
        CardCmd::Turn { gen, text } => spawn(true, gen, vec!["--card".into(), "turn".into(), "--text".into(), text], app),
        CardCmd::Kill { pid } => {
            // the whole group: s22-assistant, its fillers, s22-say and the player
            let r = unsafe { libc::kill(-pid, libc::SIGTERM) };
            log(&format!("card: stop group {pid}: {r}"));
        }
        CardCmd::CancelApi => {
            let api = netw.api.clone();
            netw.spawn(move |_| {
                let r = net::http("POST", &format!("{api}/api/chat/cancel"), Some(&serde_json::json!({"sessionId": "voice"})), Duration::from_secs(10));
                log(&format!("card: cancel the voice turn: {:?}", r.map(|x| x.0)));
            });
        }
    }
}

/// Run one camera command: the s22-campreview child, its signals, a background develop,
/// the autosleep inhibit file.
fn run_cam(c: camera::CamCmd, netw: &Net, app: &mut App, child: &mut Option<i32>) {
    use camera::CamCmd;
    match c {
        CamCmd::Spawn { gen, argv } => {
            // a fresh shm inode per child: the old mapping can never see a truncate (SIGBUS)
            let _ = std::fs::remove_file(camera::SHM);
            let _ = std::fs::create_dir_all(camera::WORK);
            let err = std::fs::File::create(format!("{}/campreview.err", camera::WORK)).map(std::process::Stdio::from).unwrap_or(std::process::Stdio::null());
            let r = std::process::Command::new(&argv[0]).args(&argv[1..]).stdin(std::process::Stdio::null()).stdout(std::process::Stdio::piped()).stderr(err).spawn();
            match r {
                Ok(mut ch) => {
                    let pid = ch.id() as i32;
                    log(&format!("camera[{gen}]: started pid {pid}: {}", argv.join(" ")));
                    *child = Some(pid);
                    let more = app.cam.spawned(gen, pid);
                    app.cam_cmds.extend(more);
                    let out = ch.stdout.take();
                    netw.spawn(move |n| {
                        use std::io::BufRead;
                        if let Some(o) = out {
                            for line in std::io::BufReader::new(o).lines().map_while(Result::ok) {
                                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&line) {
                                    if v.get("event").is_some() {
                                        n.send(Msg::Camera(gen, v));
                                    } else {
                                        // the final report line
                                        log(&format!("camera[{gen}]: report {}", line.chars().take(400).collect::<String>()));
                                    }
                                }
                            }
                        }
                        let code = ch.wait().ok().and_then(|s| s.code()).unwrap_or(-1);
                        let tail = std::fs::read_to_string(format!("{}/campreview.err", camera::WORK)).unwrap_or_default();
                        let tail: String = tail.chars().rev().take(200).collect::<Vec<_>>().into_iter().rev().collect();
                        n.send(Msg::Camera(gen, serde_json::json!({"event": "exit", "code": code, "pid": pid, "tail": tail.trim()})));
                    });
                }
                Err(e) => {
                    log(&format!("camera[{gen}]: spawn {}: {e}", argv[0]));
                    app.camera_event(gen, serde_json::json!({"event": "spawn_failed", "error": e.to_string()}));
                }
            }
        }
        CamCmd::Signal { pid, sig } => {
            // exact PID of our own child only
            if *child == Some(pid) || sig == libc::SIGTERM {
                unsafe { libc::kill(pid, sig) };
            }
        }
        CamCmd::Develop { gen, argv, preview, jpeg, raw } => {
            netw.spawn(move |n| {
                let t = Instant::now();
                if let Some(d) = std::path::Path::new(&jpeg).parent() {
                    let _ = std::fs::create_dir_all(d);
                }
                use std::os::unix::process::CommandExt;
                let mut cmd = std::process::Command::new(&argv[0]);
                cmd.args(&argv[1..]).stdin(std::process::Stdio::null());
                // the X2 core (cpu 7): the develop is single-threaded and is the shutter's critical path
                unsafe {
                    cmd.pre_exec(|| {
                        let mut set: libc::cpu_set_t = std::mem::zeroed();
                        libc::CPU_SET(7, &mut set);
                        libc::sched_setaffinity(0, std::mem::size_of::<libc::cpu_set_t>(), &set);
                        Ok(())
                    });
                }
                let out = cmd.output();
                if let Ok(o) = &out {
                    if !o.status.success() {
                        log(&format!("camera: develop failed: {}", String::from_utf8_lossy(&o.stderr).chars().take(300).collect::<String>()));
                    }
                } else if let Err(e) = &out {
                    log(&format!("camera: develop spawn: {e}"));
                }
                let ms = t.elapsed().as_millis() as u64;
                let last: serde_json::Value = out.as_ref().ok().and_then(|o| String::from_utf8_lossy(&o.stdout).lines().last().and_then(|l| serde_json::from_str(l).ok())).unwrap_or(serde_json::Value::Null);
                let ok = last["ok"] == true;
                // raws are 6 MB each on a 256 MB tmpfs: delete them at once
                for f in [raw.clone(), format!("{raw}.json"), format!("{raw}.0"), format!("{raw}.1"), format!("{raw}.2"), format!("{raw}.3")] {
                    let _ = std::fs::remove_file(f);
                }
                if ok {
                    if let Some((px, w, h)) = ui::load_png(&preview, 170, 170) {
                        n.send(Msg::CamThumb(px, w, h));
                    }
                }
                log(&format!("camera: develop {ms} ms ok {ok} (s22-develop {} ms, stacked {})", last["ms"], last["stacked"]));
                n.send(Msg::Camera(gen, serde_json::json!({"event": "developed", "ok": ok, "jpeg": jpeg, "preview": preview, "ms": ms,
                    "error": if ok { serde_json::Value::Null } else { last["error"].clone() }})));
            });
        }
        CamCmd::Inhibit(on) => {
            if on {
                let _ = std::fs::create_dir_all("/run/s22-autosleep/inhibit.d");
                let _ = std::fs::write(camera::INHIBIT, "");
            } else {
                let _ = std::fs::remove_file(camera::INHIBIT);
            }
        }
    }
}

fn frame(app: &mut App, cv: &mut Canvas, p: &mut Presenter) -> std::io::Result<()> {
    let t0 = now_us();
    let (rects, copy) = app.prepare_frame(cv);
    if rects.is_empty() && copy.is_empty() && !p.need_modeset {
        return Ok(());
    }
    for r in &rects {
        cv.clip = *r;
        app.scene(cv);
    }
    cv.clip = Rect::new(0, 0, W, H);
    let t1 = now_us();
    let mut all: Vec<Rect> = rects.iter().chain(copy.iter()).copied().collect();
    let mut cp = all.clone();
    cp.extend(p.prev.iter().copied());
    gfx::merge_rects(&mut cp);
    let b = &p.bufs[p.back];
    match p.off {
        None => {
            copy_rects(cv, b.map, b.pitch as usize, &cp);
        }
        Some((ox, oy)) => {
            gfx::copy_rects_at(cv, b.map, b.pitch as usize, p.card.width as i32, p.card.height as i32, ox, oy, &cp);
        }
    }
    let t2 = now_us();
    gfx::merge_rects(&mut all);
    p.prev = all;
    let cam_frame = app.cam_drawn.take();
    let fb = p.bufs[p.back].fb_id;
    if p.need_modeset {
        p.card.set_crtc(fb)?;
        p.need_modeset = false;
        p.back ^= 1;
        p.pending = false;
        if let Some((t_in, kind)) = app.mark.take() {
            // a wake: the modeset is synchronous, so the frame is up now
            let ms = now_us().saturating_sub(t_in) as f64 / 1000.0;
            if ms < 2000.0 {
                app.stats.lat.push((kind, ms));
                app.lat_unlogged.push((kind, ms));
            }
        }
    } else {
        p.card.page_flip(fb, 1)?;
        p.pending = true;
        p.pending_mark = app.mark.take();
        p.pending_cam = cam_frame;
    }
    app.stats.frames += 1;
    app.stats.render.push((t1 - t0) as f64 / 1000.0);
    app.stats.copy.push((t2 - t1) as f64 / 1000.0);
    if app.stats.render.len() > 2000 {
        app.stats.render.drain(..500);
        app.stats.copy.drain(..500);
    }
    Ok(())
}

// ---------------------------------------------------------------- offscreen

fn demo_app() -> App {
    let mut a = App::new(None, "/nonexistent", "");
    a.st.model = Some("gpt-6-luna".into());
    a.st.health = Some("healthy".into());
    a.st.sessions = Some((3, 20));
    a.st.running = Some(0);
    a.st.battery = Some(100);
    a.st.bat_state = "Full".into();
    a.st.bat_temp = Some(29.4);
    a.st.thermal = Some(38.0);
    a.st.thermal_detail = "BIG 38 · MID 36 · LITTLE 35 · G3D 34".into();
    a.st.net = "usb · ts".into();
    a.st.radio = Some("5G");
    a.clock = "21:47".into();
    a.date = "Wednesday 21 October".into();
    a.chat = vec![
        (ui::Who::Agent, "Your agent, right here. What should we work on?".into()),
        (ui::Who::You, "Πώς είναι ο καιρός στην Αθήνα αύριο;".into()),
        (ui::Who::Agent, "Αύριο στην Αθήνα: ηλιοφάνεια με λίγα σύννεφα, 19–24 °C, ασθενείς βόρειοι άνεμοι. Ιδανικός για βόλτα.".into()),
        (ui::Who::You, "Summarise the fast shell spike in two sentences.".into()),
        (ui::Who::Agent, "The CPU path drew a highlight 12.5 ms after touch at 0.4 % idle CPU and 9.9 MB, while Samsung Vulkan worked zero-copy but was slower end to end. The real shell therefore uses a damage-tracked CPU renderer straight into KMS dumb buffers.".into()),
    ];
    a
}

/// PNG of a canvas pixel copy (screenshots), written to `path.tmp` then renamed.
fn save_png_px(px: &[u32], w: i32, h: i32, path: &str) -> std::io::Result<()> {
    if let Some(d) = std::path::Path::new(path).parent() {
        std::fs::create_dir_all(d)?;
    }
    let tmp = format!("{path}.tmp");
    let f = std::fs::File::create(&tmp)?;
    let mut e = png::Encoder::new(std::io::BufWriter::new(f), w as u32, h as u32);
    e.set_color(png::ColorType::Rgb);
    e.set_depth(png::BitDepth::Eight);
    e.set_compression(png::Compression::Fast);
    let mut wr = e.write_header().map_err(std::io::Error::other)?;
    let mut data = Vec::with_capacity(px.len() * 3);
    for p in px {
        data.extend_from_slice(&[(p >> 16) as u8, (p >> 8) as u8, *p as u8]);
    }
    wr.write_image_data(&data).map_err(std::io::Error::other)?;
    drop(wr);
    std::fs::rename(&tmp, path)
}

/// 2x2 box downscale of an XRGB canvas copy (half-size screenshots).
fn half(px: &[u32], w: i32, h: i32) -> Vec<u32> {
    let (w, h) = (w as usize, h as usize);
    let mut out = Vec::with_capacity(w / 2 * (h / 2));
    for y in 0..h / 2 {
        for x in 0..w / 2 {
            let p = [px[2 * y * w + 2 * x], px[2 * y * w + 2 * x + 1], px[(2 * y + 1) * w + 2 * x], px[(2 * y + 1) * w + 2 * x + 1]];
            let c = |s: u32| p.iter().map(|v| (v >> s) & 255).sum::<u32>() / 4;
            out.push(c(16) << 16 | c(8) << 8 | c(0));
        }
    }
    out
}

fn save_png(cv: &Canvas, path: &str) {
    let f = std::fs::File::create(path).expect("create png");
    let mut e = png::Encoder::new(std::io::BufWriter::new(f), cv.w as u32, cv.h as u32);
    e.set_color(png::ColorType::Rgb);
    e.set_depth(png::BitDepth::Eight);
    let mut w = e.write_header().expect("png header");
    let mut data = Vec::with_capacity(cv.px.len() * 3);
    for p in &cv.px {
        data.extend_from_slice(&[(p >> 16) as u8, (p >> 8) as u8, *p as u8]);
    }
    w.write_image_data(&data).expect("png data");
}

fn render_full(a: &mut App, cv: &mut Canvas) -> f64 {
    let t = now_us();
    a.layout();
    cv.clip = Rect::new(0, 0, W, H);
    a.scene(cv);
    a.damage.clear();
    (now_us() - t) as f64 / 1000.0
}

fn states() -> Vec<(&'static str, Box<dyn Fn(&mut App)>)> {
    vec![
        ("home", Box::new(|_a: &mut App| {})),
        ("home-card", Box::new(|a: &mut App| {
            a.add_card("Agent card", "Mission finished: the paper desk review is ready (3 trades, no action needed).", 30.0);
        })),
        ("home-agent-busy", Box::new(|a: &mut App| {
            let _ = a.card.press();
            a.card_event(false, 1, serde_json::json!({"transcript": "Is the paper desk review ready?"}));
            a.card_event(true, 2, serde_json::json!({"state": "tool", "name": "worklog_read"}));
            a.card_event(true, 2, serde_json::json!({"state": "reply", "text": "Yes. The review finished at 21:30: three trades, no action needed."}));
            let _ = a.card.press();
            a.card_event(false, 3, serde_json::json!({"transcript": "and the battery?"}));
            a.card_cmds.clear();
        })),
        ("chat", Box::new(|a: &mut App| {
            a.goto(Page::Chat);
            a.kb.visible = false;
        })),
        ("chat-kbd-en", Box::new(|a: &mut App| {
            a.goto(Page::Chat);
            a.input = "What's on my calendar".into();
        })),
        ("chat-kbd-el", Box::new(|a: &mut App| {
            a.goto(Page::Chat);
            a.kb.text_layout = kbd::Layout::El;
            a.kb.layout = kbd::Layout::El;
            a.input = "Καλησπέρα, τι κάνεις;".into();
        })),
        ("chat-streaming", Box::new(|a: &mut App| {
            a.goto(Page::Chat);
            a.kb.visible = false;
            a.input = "Check the Wi-Fi".into();
            a.send();
            a.on_msg(Msg::Chat(a_turn(a), net::ChatEvent::ToolStarted("wifi_status".into())));
            a.on_msg(Msg::Chat(a_turn(a), net::ChatEvent::Delta { token: "Wi-Fi is down; the phone is online over USB (ecm0) and Tailscale.".into(), replace: false }));
        })),
        ("chat-question", Box::new(|a: &mut App| {
            a.goto(Page::Chat);
            a.kb.visible = false;
            a.prompt = Some(net::Prompt { id: "p1".into(), header: "Model".into(), question: "Switch to the local model?".into(), options: vec!["Yes, switch".into(), "No, keep Luna".into()] });
        })),
        ("confirm", Box::new(|a: &mut App| {
            a.call(&ctl::Call { function: "confirm".into(), args: vec!["dab12".into(), "Send SMS to +30 69x xxx xxxx: \"On my way\"?".into(), "60".into()] });
        })),
        ("lock", Box::new(|a: &mut App| {
            a.add_card("Reminder", "Call your mother", 60.0);
            a.locked = true;
        })),
        ("settings", Box::new(|a: &mut App| {
            a.goto(Page::Settings);
            a.dev.pending.clear();
            a.dev.answer("device.status", 200, &serde_json::json!({"ok": true, "state": {"bluetooth": false, "vibration": true, "autosleep": true, "calls_enabled": true,
                "volume": 4, "brightness_pct": 30, "network": {"uplink": "mobile", "operator": "Operator", "radio": "5G",
                "wifi": {"enabled": true, "on": false, "connected": false}, "mobile_data": {"on": true, "state": "up"}}}}));
            a.dev.begin("bluetooth.set");
            a.dev.answer("wifi.set", 409, &serde_json::json!({"ok": false, "code": "would_strand"}));
        })),
        ("volume-osd", Box::new(|a: &mut App| {
            a.level = 5;
            a.button_actions(&[buttons::Action::Step(1)], 0);
        })),
        ("volume-osd-locked", Box::new(|a: &mut App| {
            a.locked = true;
            a.level = 1;
            a.button_actions(&[buttons::Action::Revert, buttons::Action::Mute], 0);
        })),
        ("switcher", Box::new(|a: &mut App| a.goto(Page::Switcher))),
        ("files", Box::new(|a: &mut App| {
            a.files_dir = "/".into();
            a.goto(Page::Files);
            a.load_dir();
        })),
        ("phone", Box::new(|a: &mut App| {
            a.goto(Page::Phone);
            a.phone = Some((
                serde_json::json!({"ok": true, "modem_state": "ONLINE", "sim": "PB_INIT_COMPLETE", "registration": {"reg_status": "home_3gpp", "act": "lte", "volte": 1}}),
                serde_json::json!({"messages": [{"from": "+3069xxxxxxxx", "ts": "2026-10-21 18:02", "text": "Θα αργήσω 10 λεπτά"}]}),
                serde_json::json!({"calls": []}),
            ));
        })),
        ("camera", Box::new(|a: &mut App| {
            a.goto(Page::Camera);
            a.cam.status = "starting the rear camera…".into();
            a.cam_thumb = Some((vec![0x506070; 170 * 128], 170, 128));
        })),
        ("camera-video", Box::new(|a: &mut App| {
            a.goto(Page::Camera);
            a.cam.mode = camera::Mode::Video;
            a.cam.recording = Some(Instant::now() - Duration::from_secs(12));
        })),
        ("chat-key-popup", Box::new(|a: &mut App| {
            a.goto(Page::Chat);
            a.input = "Hel".into();
            a.layout();
            let h = a.hits.iter().find(|h| matches!(h.act, ui::Act::Key(1, 8))).cloned();
            a.pressed = h;
        })),
    ]
}

fn a_turn(a: &App) -> u64 {
    // the demo app's current turn id: send() increments from 0
    let _ = a;
    1
}

fn dump(dir: &str) {
    let _ = std::fs::create_dir_all(dir);
    let mut cv = Canvas::new(W, H);
    for (name, f) in states() {
        let mut a = demo_app();
        f(&mut a);
        let ms = render_full(&mut a, &mut cv);
        save_png(&cv, &format!("{dir}/{name}.png"));
        println!("{name}: full frame {ms:.2} ms");
    }
}

fn bench(n: usize) {
    let mut cv = Canvas::new(W, H);
    let mut out = serde_json::Map::new();
    for (name, f) in states() {
        let mut a = demo_app();
        f(&mut a);
        render_full(&mut a, &mut cv); // warm the glyph cache
        let mut v: Vec<f64> = (0..n).map(|_| render_full(&mut a, &mut cv)).collect();
        v.sort_by(|x, y| x.partial_cmp(y).unwrap());
        out.insert(name.into(), serde_json::json!({"n": n, "p50_ms": v[n / 2], "max_ms": v[n - 1]}));
    }
    // a scroll step: blit + strip, chat page
    let mut a = demo_app();
    a.goto(Page::Chat);
    a.kb.visible = false;
    for _ in 0..6 {
        a.chat.extend(demo_app().chat);
    }
    render_full(&mut a, &mut cv);
    let _ = a.prepare_frame(&mut cv);
    let mut v = vec![];
    for i in 0..n {
        a.scroll_by(if i % 2 == 0 { -24.0 } else { 24.0 });
        let t = now_us();
        let (rects, _copy) = a.prepare_frame(&mut cv);
        for r in &rects {
            cv.clip = *r;
            a.scene(&mut cv);
        }
        v.push((now_us() - t) as f64 / 1000.0);
    }
    v.sort_by(|x, y| x.partial_cmp(y).unwrap());
    out.insert("chat-scroll-step-24px".into(), serde_json::json!({"n": n, "p50_ms": v[n / 2], "max_ms": v[n - 1]}));
    println!("{}", serde_json::Value::Object(out));
}
