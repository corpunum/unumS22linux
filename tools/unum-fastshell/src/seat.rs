//! Minimal seatd client (the libseat "seatd" backend wire protocol, seatd 0.9).
//!
//! The S22 desktop runs seatd with a seat that is not VT-bound, so every client
//! gets a session number and the active client can switch to another one.
//! sway (session 1) switches to us when it receives the XF86Switch_VT_<n>
//! keysym (wtype); we switch back with CLIENT_SWITCH_SESSION. seatd drops DRM
//! master on the fds it opened for the client that goes inactive and gives it
//! back on re-activation, so the card is opened through seatd.
//!
//! Wire format: header {u16 opcode, u16 size} + payload (size bytes), host
//! endian. Device fds arrive as SCM_RIGHTS.
use std::io;
use std::os::fd::RawFd;

const CLIENT_OPEN_SEAT: u16 = 1;
const CLIENT_CLOSE_SEAT: u16 = 2;
const CLIENT_OPEN_DEVICE: u16 = 3;
const CLIENT_DISABLE_SEAT: u16 = 5;
const CLIENT_SWITCH_SESSION: u16 = 6;
const SERVER: u16 = 1 << 15;
const SERVER_SEAT_OPENED: u16 = SERVER + 1;
const SERVER_DEVICE_OPENED: u16 = SERVER + 3;
const SERVER_DISABLE_SEAT: u16 = SERVER + 5;
const SERVER_ENABLE_SEAT: u16 = SERVER + 6;
const SERVER_ERROR: u16 = SERVER + 0x7FFF;

#[derive(Debug, Clone, PartialEq)]
pub enum SeatEvent {
    Enable,
    Disable,
    Opened(String),
    Device(i32, RawFd),
    Error(i32),
    Closed,
}

pub struct Seat {
    pub fd: RawFd,
    buf: Vec<u8>,
    fds: Vec<RawFd>,
}

impl Seat {
    pub fn connect(path: &str) -> io::Result<Seat> {
        let fd = unsafe { libc::socket(libc::AF_UNIX, libc::SOCK_STREAM | libc::SOCK_CLOEXEC, 0) };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        let mut sa: libc::sockaddr_un = unsafe { std::mem::zeroed() };
        sa.sun_family = libc::AF_UNIX as _;
        for (i, b) in path.bytes().enumerate().take(107) {
            sa.sun_path[i] = b as _;
        }
        let r = unsafe { libc::connect(fd, &sa as *const _ as *const libc::sockaddr, std::mem::size_of::<libc::sockaddr_un>() as u32) };
        if r != 0 {
            let e = io::Error::last_os_error();
            unsafe { libc::close(fd) };
            return Err(e);
        }
        Ok(Seat { fd, buf: Vec::new(), fds: Vec::new() })
    }

    fn send(&self, opcode: u16, payload: &[u8]) -> io::Result<()> {
        let mut m = Vec::with_capacity(4 + payload.len());
        m.extend_from_slice(&opcode.to_ne_bytes());
        m.extend_from_slice(&(payload.len() as u16).to_ne_bytes());
        m.extend_from_slice(payload);
        let n = unsafe { libc::send(self.fd, m.as_ptr() as *const _, m.len(), libc::MSG_NOSIGNAL) };
        if n != m.len() as isize {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }

    pub fn open_seat(&self) -> io::Result<()> {
        self.send(CLIENT_OPEN_SEAT, &[])
    }

    pub fn close_seat(&self) -> io::Result<()> {
        self.send(CLIENT_CLOSE_SEAT, &[])
    }

    pub fn ack_disable(&self) -> io::Result<()> {
        self.send(CLIENT_DISABLE_SEAT, &[])
    }

    pub fn switch_session(&self, session: i32) -> io::Result<()> {
        self.send(CLIENT_SWITCH_SESSION, &session.to_ne_bytes())
    }

    pub fn open_device(&self, path: &str) -> io::Result<()> {
        let mut p = Vec::new();
        p.extend_from_slice(&((path.len() + 1) as u16).to_ne_bytes());
        p.extend_from_slice(path.as_bytes());
        p.push(0);
        self.send(CLIENT_OPEN_DEVICE, &p)
    }

    /// Read what is available (non-blocking callers should poll first) and decode whole messages.
    pub fn read(&mut self, out: &mut Vec<SeatEvent>) -> io::Result<()> {
        let mut data = [0u8; 4096];
        let mut cbuf = [0u8; 256];
        let mut iov = libc::iovec { iov_base: data.as_mut_ptr() as *mut _, iov_len: data.len() };
        let mut mh: libc::msghdr = unsafe { std::mem::zeroed() };
        mh.msg_iov = &mut iov;
        mh.msg_iovlen = 1;
        mh.msg_control = cbuf.as_mut_ptr() as *mut _;
        mh.msg_controllen = cbuf.len() as _;
        let n = unsafe { libc::recvmsg(self.fd, &mut mh, libc::MSG_DONTWAIT | libc::MSG_CMSG_CLOEXEC) };
        if n == 0 {
            out.push(SeatEvent::Closed);
            return Ok(());
        }
        if n < 0 {
            let e = io::Error::last_os_error();
            if e.kind() == io::ErrorKind::WouldBlock {
                return Ok(());
            }
            return Err(e);
        }
        unsafe {
            let mut c = libc::CMSG_FIRSTHDR(&mh);
            while !c.is_null() {
                if (*c).cmsg_level == libc::SOL_SOCKET && (*c).cmsg_type == libc::SCM_RIGHTS {
                    let k = ((*c).cmsg_len as usize - libc::CMSG_LEN(0) as usize) / 4;
                    let p = libc::CMSG_DATA(c) as *const RawFd;
                    for i in 0..k {
                        self.fds.push(*p.add(i));
                    }
                }
                c = libc::CMSG_NXTHDR(&mh, c);
            }
        }
        self.buf.extend_from_slice(&data[..n as usize]);
        while self.buf.len() >= 4 {
            let op = u16::from_ne_bytes([self.buf[0], self.buf[1]]);
            let size = u16::from_ne_bytes([self.buf[2], self.buf[3]]) as usize;
            if self.buf.len() < 4 + size {
                break;
            }
            let pl: Vec<u8> = self.buf[4..4 + size].to_vec();
            self.buf.drain(..4 + size);
            let i32at = |o: usize| if pl.len() >= o + 4 { i32::from_ne_bytes([pl[o], pl[o + 1], pl[o + 2], pl[o + 3]]) } else { -1 };
            out.push(match op {
                SERVER_ENABLE_SEAT => SeatEvent::Enable,
                SERVER_DISABLE_SEAT => SeatEvent::Disable,
                SERVER_SEAT_OPENED => {
                    let name = if pl.len() > 2 { String::from_utf8_lossy(&pl[2..]).trim_end_matches('\0').to_string() } else { String::new() };
                    SeatEvent::Opened(name)
                }
                SERVER_DEVICE_OPENED => {
                    let fd = if self.fds.is_empty() { -1 } else { self.fds.remove(0) };
                    SeatEvent::Device(i32at(0), fd)
                }
                SERVER_ERROR => SeatEvent::Error(i32at(0)),
                _ => continue,
            });
        }
        Ok(())
    }
}

impl Drop for Seat {
    fn drop(&mut self) {
        unsafe { libc::close(self.fd) };
    }
}
