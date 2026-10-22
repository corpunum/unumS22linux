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
mod buttons;
mod ctl;
mod drm;
mod font;
mod gfx;
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
    need_modeset: bool,
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
    let chroot = env("FASTSHELL_CHROOT", "/mnt/omarchy-trial");
    // musl reads zoneinfo from /usr/share/zoneinfo, which the native root may lack:
    // use the chroot's copy by absolute path, else the POSIX rule for Athens.
    let tz = env("TZ", "Europe/Athens");
    if !tz.starts_with('/') && !tz.contains(',') && !std::path::Path::new(&format!("/usr/share/zoneinfo/{tz}")).exists() {
        let alt = format!("{chroot}/usr/share/zoneinfo/{tz}");
        std::env::set_var("TZ", if std::path::Path::new(&alt).exists() { alt } else { "EET-2EEST,M3.5.0/3,M10.5.0/4".into() });
    }
    unsafe { tzset() };
    let run_dir = env("S22_TOUCH_RUN", &format!("{chroot}/run/s22-touch"));
    let state_dir = env("FASTSHELL_STATE", "/srv/s22/state/fastshell");
    let _ = std::fs::create_dir_all(&state_dir);
    let _ = std::fs::write("/run/s22-fastshell.pid", format!("{}\n", std::process::id()));
    let mut pipe = [0 as RawFd; 2];
    unsafe { libc::pipe2(pipe.as_mut_ptr(), libc::O_CLOEXEC | libc::O_NONBLOCK) };
    let (tx, rx) = std::sync::mpsc::channel::<Msg>();
    let netw = Net {
        tx,
        wake_fd: pipe[1],
        api: env("S22_SHELL_API", "http://127.0.0.1:18880"),
        s22d: env("S22_SHELL_S22D", "http://127.0.0.1:8766"),
        phoned: env("S22_SHELL_PHONED", "http://127.0.0.1:8095"),
        session: env("S22_SHELL_SESSION", "touch"),
        touchd_sock: format!("{run_dir}/ctl.sock"),
        turn: Arc::new(AtomicU64::new(0)),
    };
    ctl::serve(format!("{run_dir}/shell.sock"), netw.clone())?;
    let mut app = App::new(Some(netw.clone()), &run_dir, &chroot);
    app.idle_path = env("FASTSHELL_IDLE_CFG", &format!("{state_dir}/screen-timeout.json"));
    app.load_idle();
    log(&format!("screen timeout: unlocked {} s, locked {} s (0 = never)", app.idle_cfg.unlocked_s, app.idle_cfg.locked_s));
    net::refresh_agent(&netw);
    net::fetch_history(&netw);

    let sway = Arc::new(Sway { chroot: chroot.clone() });
    let mut seat = Seat::connect(&format!("{chroot}/run/seatd.sock"))?;
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
                        if let Some((t_in, kind)) = p.pending_mark.take() {
                            let ms = tdone.saturating_sub(t_in) as f64 / 1000.0;
                            if ms < 2000.0 {
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
                            seat.open_device("/dev/dri/card1")?;
                            opened_device = true;
                        } else if let Some(p) = pres.as_mut() {
                            p.need_modeset = true;
                            p.pending = false;
                            p.prev = vec![Rect::new(0, 0, W, H)];
                            app.display_on = true;
                            write_state("/run/s22-display-state", "on");
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
                        log(&format!("card1 crtc {} conn {} mode {} {}x{}", card.crtc_id, card.connector_id, card.mode_name(), card.width, card.height));
                        let bufs = vec![card.create_dumb()?, card.create_dumb()?];
                        pres = Some(Presenter { card, bufs, back: 0, prev: vec![Rect::new(0, 0, W, H)], pending: false, pending_mark: None, need_modeset: true });
                        write_state("/run/s22-display-state", "on");
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
                        app.touch(e);
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
                other => app.on_msg(other),
            }
        }
        // ---- timers
        let now = Instant::now();
        if now >= next_tick {
            next_tick = now + Duration::from_secs(1);
            app.tick();
            if app.display_on && app.active {
                let say = idle::flock_held(&env("FASTSHELL_SAY_LOCK", "/run/s22-say.lock"));
                if app.idle_check(now, say) {
                    log(&format!("screen timeout: idle {} s, locked {} -> panel off", now.saturating_duration_since(app.last_touch).as_secs(), app.locked));
                    app.screen_timeout_off();
                }
            }
            if now >= next_sys {
                next_sys = now + Duration::from_secs(10);
                if app.display_on {
                    app.load_idle();
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
        // ---- effects
        for e in std::mem::take(&mut app.effects) {
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
                            write_state("/run/s22-display-state", "on");
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
                            write_state("/run/s22-display-state", "off");
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
                        let last = std::fs::read_to_string("/srv/s22/buttons/assistant.jsonl").ok().and_then(|s| s.lines().last().map(String::from)).unwrap_or_default();
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
                    let px = cv.px.clone();
                    let host = format!("{chroot}{rel}");
                    std::thread::spawn(move || {
                        let t = Instant::now();
                        let r = save_png_px(&px, W, H, &host);
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
    buttons::claim(false);
    let _ = seat.close_seat();
    let _ = std::fs::remove_file("/run/s22-fastshell.pid");
    Ok(())
}

extern "C" {
    fn tzset();
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
    copy_rects(cv, b.map, b.pitch as usize, &cp);
    let t2 = now_us();
    gfx::merge_rects(&mut all);
    p.prev = all;
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
        ("settings", Box::new(|a: &mut App| a.goto(Page::Settings))),
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
            a.camera_sensor = "front";
            if let Ok(p) = std::env::var("FS_DUMP_CAMERA") {
                a.camera_img = ui::load_png(&p, 1016, 1400);
            }
            a.camera_state = ui::camera_summary(&serde_json::json!({"rc": 0, "sensor": "front", "exposure_us": 30000, "gain": 8.0,
                "develop_ok": true, "stacked": 4, "jpeg": "/x/cam-1792620000-front.jpg"}));
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
