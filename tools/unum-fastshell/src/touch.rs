//! Non-grabbing evdev multitouch reader (protocol B) for sec_touchscreen.
//! Reports the first finger only; a second finger turns into `Cancel`
//! (multi-touch is not used by the shell). Timestamps are CLOCK_MONOTONIC µs,
//! comparable with DRM flip events. From the Phase 1 spike (tools/fastui).
use std::io;
use std::os::fd::RawFd;

const EV_SYN: u16 = 0;
const EV_ABS: u16 = 3;
const ABS_MT_SLOT: u16 = 0x2f;
const ABS_MT_POSITION_X: u16 = 0x35;
const ABS_MT_POSITION_Y: u16 = 0x36;
const ABS_MT_TRACKING_ID: u16 = 0x39;

#[repr(C)]
#[derive(Default, Clone, Copy)]
struct AbsInfo {
    value: i32,
    minimum: i32,
    maximum: i32,
    fuzz: i32,
    flat: i32,
    resolution: i32,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Touch {
    Down(i32, i32, u64),
    Move(i32, i32, u64),
    Up(i32, i32, u64),
    /// A second finger landed: abandon the current gesture.
    Cancel(u64),
}

pub struct TouchDev {
    pub fd: RawFd,
    xr: (i32, i32),
    yr: (i32, i32),
    w: i32,
    h: i32,
    slot: i32,
    ids: [i32; 10],
    x: i32,
    y: i32,
    was_down: bool,
    moved: bool,
    cancelled: bool,
}

pub fn find_touchscreen() -> Option<String> {
    let mut v: Vec<_> = std::fs::read_dir("/sys/class/input").ok()?.flatten().map(|e| e.file_name().to_string_lossy().to_string()).filter(|n| n.starts_with("event")).collect();
    v.sort();
    for n in v {
        if std::fs::read_to_string(format!("/sys/class/input/{n}/device/name")).map(|s| s.trim() == "sec_touchscreen").unwrap_or(false) {
            return Some(format!("/dev/input/{n}"));
        }
    }
    None
}

impl TouchDev {
    pub fn open(path: &str, w: u32, h: u32) -> io::Result<TouchDev> {
        let c = std::ffi::CString::new(path).unwrap();
        let fd = unsafe { libc::open(c.as_ptr(), libc::O_RDONLY | libc::O_NONBLOCK | libc::O_CLOEXEC) };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        let clk: libc::c_int = libc::CLOCK_MONOTONIC;
        let req = (1u64 << 30) | (4u64 << 16) | ((b'E' as u64) << 8) | 0xa0;
        unsafe { libc::ioctl(fd, req as _, &clk) };
        let gabs = |code: u16| -> (i32, i32) {
            let mut a = AbsInfo::default();
            let req = (2u64 << 30) | ((std::mem::size_of::<AbsInfo>() as u64) << 16) | ((b'E' as u64) << 8) | (0x40 + code as u64);
            let r = unsafe { libc::ioctl(fd, req as _, &mut a) };
            if r == 0 && a.maximum > a.minimum { (a.minimum, a.maximum) } else { (0, 4095) }
        };
        let xr = gabs(ABS_MT_POSITION_X);
        let yr = gabs(ABS_MT_POSITION_Y);
        Ok(TouchDev { fd, xr, yr, w: w as i32, h: h as i32, slot: 0, ids: [-1; 10], x: 0, y: 0, was_down: false, moved: false, cancelled: false })
    }

    /// Drain pending events into `out`.
    pub fn read(&mut self, out: &mut Vec<Touch>) {
        let mut buf = [0u8; 24 * 64];
        loop {
            let n = unsafe { libc::read(self.fd, buf.as_mut_ptr() as *mut _, buf.len()) };
            if n <= 0 {
                return;
            }
            for ev in buf[..n as usize].chunks_exact(24) {
                let sec = i64::from_ne_bytes(ev[0..8].try_into().unwrap());
                let usec = i64::from_ne_bytes(ev[8..16].try_into().unwrap());
                let typ = u16::from_ne_bytes(ev[16..18].try_into().unwrap());
                let code = u16::from_ne_bytes(ev[18..20].try_into().unwrap());
                let val = i32::from_ne_bytes(ev[20..24].try_into().unwrap());
                let t = (sec * 1_000_000 + usec) as u64;
                match (typ, code) {
                    (EV_ABS, ABS_MT_SLOT) => self.slot = val,
                    (EV_ABS, ABS_MT_TRACKING_ID) => {
                        if (0..10).contains(&self.slot) {
                            self.ids[self.slot as usize] = val;
                        }
                    }
                    (EV_ABS, ABS_MT_POSITION_X) if self.slot == 0 => {
                        self.x = ((val - self.xr.0) as i64 * self.w as i64 / (self.xr.1 - self.xr.0 + 1) as i64) as i32;
                        self.moved = true;
                    }
                    (EV_ABS, ABS_MT_POSITION_Y) if self.slot == 0 => {
                        self.y = ((val - self.yr.0) as i64 * self.h as i64 / (self.yr.1 - self.yr.0 + 1) as i64) as i32;
                        self.moved = true;
                    }
                    (EV_SYN, 0) => {
                        let down = self.ids[0] >= 0;
                        let others = self.ids[1..].iter().any(|&i| i >= 0);
                        if down && others && !self.cancelled && self.was_down {
                            self.cancelled = true;
                            out.push(Touch::Cancel(t));
                        }
                        if down && !self.was_down {
                            self.cancelled = others;
                            if !others {
                                out.push(Touch::Down(self.x, self.y, t));
                            }
                        } else if down && self.moved && !self.cancelled {
                            out.push(Touch::Move(self.x, self.y, t));
                        } else if !down && self.was_down {
                            if !self.cancelled {
                                out.push(Touch::Up(self.x, self.y, t));
                            }
                            self.cancelled = false;
                        }
                        self.was_down = down;
                        self.moved = false;
                    }
                    _ => {}
                }
            }
        }
    }
}
