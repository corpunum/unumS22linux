//! Non-grabbing evdev multitouch reader (protocol B) for sec_touchscreen.
use std::io;
use std::os::fd::RawFd;

const EV_SYN: u16 = 0;
const EV_KEY: u16 = 1;
const EV_ABS: u16 = 3;
const BTN_TOUCH: u16 = 0x14a;
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

#[derive(Debug, Clone, Copy)]
pub enum Touch {
    /// finger down / move: x, y in panel pixels, event time µs (CLOCK_MONOTONIC)
    Down(i32, i32, u64),
    Move(i32, i32, u64),
    Up(u64),
}

pub struct TouchDev {
    pub fd: RawFd,
    xr: (i32, i32),
    yr: (i32, i32),
    w: i32,
    h: i32,
    slot: i32,
    x: i32,
    y: i32,
    down: bool,
    was_down: bool,
    moved: bool,
}

impl TouchDev {
    pub fn open(path: &str, w: u32, h: u32) -> io::Result<TouchDev> {
        let c = std::ffi::CString::new(path).unwrap();
        let fd = unsafe { libc::open(c.as_ptr(), libc::O_RDONLY | libc::O_NONBLOCK | libc::O_CLOEXEC) };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        // EVIOCSCLOCKID: timestamps on CLOCK_MONOTONIC so they compare with DRM flip events.
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
        Ok(TouchDev { fd, xr, yr, w: w as i32, h: h as i32, slot: 0, x: 0, y: 0, down: false, was_down: false, moved: false })
    }

    pub fn ranges(&self) -> ((i32, i32), (i32, i32)) {
        (self.xr, self.yr)
    }

    /// Drain pending events; returns touch transitions of slot 0 (first finger), one per SYN_REPORT.
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
                    (EV_ABS, ABS_MT_TRACKING_ID) if self.slot == 0 => self.down = val >= 0,
                    (EV_ABS, ABS_MT_POSITION_X) if self.slot == 0 => {
                        self.x = ((val - self.xr.0) as i64 * self.w as i64 / (self.xr.1 - self.xr.0 + 1) as i64) as i32;
                        self.moved = true;
                    }
                    (EV_ABS, ABS_MT_POSITION_Y) if self.slot == 0 => {
                        self.y = ((val - self.yr.0) as i64 * self.h as i64 / (self.yr.1 - self.yr.0 + 1) as i64) as i32;
                        self.moved = true;
                    }
                    (EV_KEY, BTN_TOUCH) => {
                        if val == 0 {
                            self.down = false;
                        }
                    }
                    (EV_SYN, 0) => {
                        if self.down && !self.was_down {
                            out.push(Touch::Down(self.x, self.y, t));
                        } else if self.down && self.moved {
                            out.push(Touch::Move(self.x, self.y, t));
                        } else if !self.down && self.was_down {
                            out.push(Touch::Up(t));
                        }
                        self.was_down = self.down;
                        self.moved = false;
                    }
                    _ => {}
                }
            }
        }
    }
}
