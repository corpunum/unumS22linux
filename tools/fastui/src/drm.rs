//! Minimal raw DRM/KMS ioctls (legacy API): enough to find the active CRTC,
//! create dumb buffers, import dma-bufs (PRIME), add framebuffers and page-flip.
use std::io;
use std::os::fd::RawFd;

const fn ioc(dir: u64, nr: u64, size: u64) -> u64 {
    (dir << 30) | (size << 16) | ((b'd' as u64) << 8) | nr
}
const IOWR: u64 = 3;
const IOW: u64 = 1;

#[repr(C)]
#[derive(Default)]
struct CardRes {
    fb_id_ptr: u64,
    crtc_id_ptr: u64,
    connector_id_ptr: u64,
    encoder_id_ptr: u64,
    count_fbs: u32,
    count_crtcs: u32,
    count_connectors: u32,
    count_encoders: u32,
    min_width: u32,
    max_width: u32,
    min_height: u32,
    max_height: u32,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct ModeInfo {
    pub clock: u32,
    pub hdisplay: u16,
    pub hsync_start: u16,
    pub hsync_end: u16,
    pub htotal: u16,
    pub hskew: u16,
    pub vdisplay: u16,
    pub vsync_start: u16,
    pub vsync_end: u16,
    pub vtotal: u16,
    pub vscan: u16,
    pub vrefresh: u32,
    pub flags: u32,
    pub type_: u32,
    pub name: [u8; 32],
}

#[repr(C)]
#[derive(Clone, Copy)]
struct Crtc {
    set_connectors_ptr: u64,
    count_connectors: u32,
    crtc_id: u32,
    fb_id: u32,
    x: u32,
    y: u32,
    gamma_size: u32,
    mode_valid: u32,
    mode: ModeInfo,
}

#[repr(C)]
#[derive(Default)]
struct GetConnector {
    encoders_ptr: u64,
    modes_ptr: u64,
    props_ptr: u64,
    prop_values_ptr: u64,
    count_modes: u32,
    count_props: u32,
    count_encoders: u32,
    encoder_id: u32,
    connector_id: u32,
    connector_type: u32,
    connector_type_id: u32,
    connection: u32,
    mm_width: u32,
    mm_height: u32,
    subpixel: u32,
    pad: u32,
}

#[repr(C)]
#[derive(Default)]
struct GetEncoder {
    encoder_id: u32,
    encoder_type: u32,
    crtc_id: u32,
    possible_crtcs: u32,
    possible_clones: u32,
}

#[repr(C)]
#[derive(Default)]
struct FbCmd2 {
    fb_id: u32,
    width: u32,
    height: u32,
    pixel_format: u32,
    flags: u32,
    handles: [u32; 4],
    pitches: [u32; 4],
    offsets: [u32; 4],
    _pad: u32,
    modifier: [u64; 4],
}

#[repr(C)]
#[derive(Default)]
struct PageFlip {
    crtc_id: u32,
    fb_id: u32,
    flags: u32,
    reserved: u32,
    user_data: u64,
}

#[repr(C)]
#[derive(Default)]
struct CreateDumb {
    height: u32,
    width: u32,
    bpp: u32,
    flags: u32,
    handle: u32,
    pitch: u32,
    size: u64,
}

#[repr(C)]
#[derive(Default)]
struct MapDumb {
    handle: u32,
    pad: u32,
    offset: u64,
}

#[repr(C)]
#[derive(Default)]
struct PrimeHandle {
    handle: u32,
    flags: u32,
    fd: i32,
}

#[repr(C)]
#[derive(Default)]
struct FbDirty {
    fb_id: u32,
    flags: u32,
    color: u32,
    num_clips: u32,
    clips_ptr: u64,
}

const GETRESOURCES: u64 = ioc(IOWR, 0xA0, std::mem::size_of::<CardRes>() as u64);
const GETCRTC: u64 = ioc(IOWR, 0xA1, std::mem::size_of::<Crtc>() as u64);
const SETCRTC: u64 = ioc(IOWR, 0xA2, std::mem::size_of::<Crtc>() as u64);
const GETENCODER: u64 = ioc(IOWR, 0xA6, std::mem::size_of::<GetEncoder>() as u64);
const GETCONNECTOR: u64 = ioc(IOWR, 0xA7, std::mem::size_of::<GetConnector>() as u64);
const RMFB: u64 = ioc(IOWR, 0xAF, 4);
const PAGE_FLIP: u64 = ioc(IOWR, 0xB0, std::mem::size_of::<PageFlip>() as u64);
const DIRTYFB: u64 = ioc(IOWR, 0xB1, std::mem::size_of::<FbDirty>() as u64);
const CREATE_DUMB: u64 = ioc(IOWR, 0xB2, std::mem::size_of::<CreateDumb>() as u64);
const MAP_DUMB: u64 = ioc(IOWR, 0xB3, std::mem::size_of::<MapDumb>() as u64);
const DESTROY_DUMB: u64 = ioc(IOWR, 0xB4, 4);
const ADDFB2: u64 = ioc(IOWR, 0xB8, std::mem::size_of::<FbCmd2>() as u64);
const PRIME_HANDLE_TO_FD: u64 = ioc(IOWR, 0x2D, std::mem::size_of::<PrimeHandle>() as u64);
const PRIME_FD_TO_HANDLE: u64 = ioc(IOWR, 0x2E, std::mem::size_of::<PrimeHandle>() as u64);
const GEM_CLOSE: u64 = ioc(IOW, 0x09, 8);
const SET_MASTER: u64 = (b'd' as u64) << 8 | 0x1e;

pub const FMT_XRGB8888: u32 = u32::from_le_bytes(*b"XR24");

fn ioctl<T>(fd: RawFd, req: u64, arg: &mut T) -> io::Result<()> {
    loop {
        let r = unsafe { libc::ioctl(fd, req as _, arg as *mut T) };
        if r == 0 {
            return Ok(());
        }
        let e = io::Error::last_os_error();
        if e.raw_os_error() == Some(libc::EINTR) || e.raw_os_error() == Some(libc::EAGAIN) {
            continue;
        }
        return Err(e);
    }
}

pub struct Card {
    pub fd: RawFd,
    pub crtc_id: u32,
    pub connector_id: u32,
    pub mode: ModeInfo,
    pub width: u32,
    pub height: u32,
}

pub struct Buffer {
    pub handle: u32,
    pub fb_id: u32,
    pub pitch: u32,
    pub size: usize,
    pub map: *mut u8,
    pub dumb: bool,
}

impl Card {
    pub fn open(path: &str) -> io::Result<Card> {
        let c = std::ffi::CString::new(path).unwrap();
        let fd = unsafe { libc::open(c.as_ptr(), libc::O_RDWR | libc::O_CLOEXEC) };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        // Root opening a card with no master becomes master automatically; be explicit.
        let _ = unsafe { libc::ioctl(fd, SET_MASTER as _, 0) };
        let mut res = CardRes::default();
        ioctl(fd, GETRESOURCES, &mut res)?;
        let mut crtcs = vec![0u32; res.count_crtcs as usize];
        let mut conns = vec![0u32; res.count_connectors as usize];
        let mut encs = vec![0u32; res.count_encoders as usize];
        let mut fbs = vec![0u32; res.count_fbs as usize];
        res.crtc_id_ptr = crtcs.as_mut_ptr() as u64;
        res.connector_id_ptr = conns.as_mut_ptr() as u64;
        res.encoder_id_ptr = encs.as_mut_ptr() as u64;
        res.fb_id_ptr = fbs.as_mut_ptr() as u64;
        ioctl(fd, GETRESOURCES, &mut res)?;
        for &cid in &conns {
            let mut gc = GetConnector { connector_id: cid, ..Default::default() };
            ioctl(fd, GETCONNECTOR, &mut gc)?;
            if gc.connection != 1 || gc.count_modes == 0 {
                continue;
            }
            let mut modes: Vec<ModeInfo> = vec![unsafe { std::mem::zeroed() }; gc.count_modes as usize];
            let mut cencs = vec![0u32; gc.count_encoders as usize];
            let mut props = vec![0u32; gc.count_props as usize];
            let mut pvals = vec![0u64; gc.count_props as usize];
            gc.modes_ptr = modes.as_mut_ptr() as u64;
            gc.encoders_ptr = cencs.as_mut_ptr() as u64;
            gc.props_ptr = props.as_mut_ptr() as u64;
            gc.prop_values_ptr = pvals.as_mut_ptr() as u64;
            ioctl(fd, GETCONNECTOR, &mut gc)?;
            // Prefer the CRTC this connector's encoder currently drives.
            let mut crtc_id = 0;
            if gc.encoder_id != 0 {
                let mut ge = GetEncoder { encoder_id: gc.encoder_id, ..Default::default() };
                if ioctl(fd, GETENCODER, &mut ge).is_ok() {
                    crtc_id = ge.crtc_id;
                }
            }
            if crtc_id == 0 {
                for &eid in &cencs {
                    let mut ge = GetEncoder { encoder_id: eid, ..Default::default() };
                    if ioctl(fd, GETENCODER, &mut ge).is_ok() {
                        for (i, &c) in crtcs.iter().enumerate() {
                            if ge.possible_crtcs & (1 << i) != 0 {
                                crtc_id = c;
                                break;
                            }
                        }
                    }
                    if crtc_id != 0 {
                        break;
                    }
                }
            }
            // Current mode on that CRTC if valid, else the connector's first (preferred) mode.
            let mut cur: Crtc = unsafe { std::mem::zeroed() };
            cur.crtc_id = crtc_id;
            let mode = if ioctl(fd, GETCRTC, &mut cur).is_ok() && cur.mode_valid != 0 { cur.mode } else { modes[0] };
            return Ok(Card {
                fd,
                crtc_id,
                connector_id: cid,
                width: mode.hdisplay as u32,
                height: mode.vdisplay as u32,
                mode,
            });
        }
        Err(io::Error::new(io::ErrorKind::NotFound, "no connected connector"))
    }

    pub fn mode_name(&self) -> String {
        let n = self.mode.name.iter().position(|&b| b == 0).unwrap_or(32);
        String::from_utf8_lossy(&self.mode.name[..n]).into_owned()
    }

    fn add_fb(&self, handle: u32, pitch: u32, w: u32, h: u32) -> io::Result<u32> {
        let mut f = FbCmd2 { width: w, height: h, pixel_format: FMT_XRGB8888, ..Default::default() };
        f.handles[0] = handle;
        f.pitches[0] = pitch;
        ioctl(self.fd, ADDFB2, &mut f)?;
        Ok(f.fb_id)
    }

    pub fn create_dumb(&self) -> io::Result<Buffer> {
        let mut cd = CreateDumb { width: self.width, height: self.height, bpp: 32, ..Default::default() };
        ioctl(self.fd, CREATE_DUMB, &mut cd)?;
        let fb_id = self.add_fb(cd.handle, cd.pitch, self.width, self.height)?;
        let mut md = MapDumb { handle: cd.handle, ..Default::default() };
        ioctl(self.fd, MAP_DUMB, &mut md)?;
        let map = unsafe {
            libc::mmap(std::ptr::null_mut(), cd.size as usize, libc::PROT_READ | libc::PROT_WRITE, libc::MAP_SHARED, self.fd, md.offset as libc::off_t)
        };
        if map == libc::MAP_FAILED {
            return Err(io::Error::last_os_error());
        }
        Ok(Buffer { handle: cd.handle, fb_id, pitch: cd.pitch, size: cd.size as usize, map: map as *mut u8, dumb: true })
    }

    /// Export a GEM handle as a dma-buf fd (read/write).
    pub fn export_fd(&self, handle: u32) -> io::Result<RawFd> {
        let mut p = PrimeHandle { handle, flags: (libc::O_CLOEXEC | libc::O_RDWR) as u32, fd: -1 };
        ioctl(self.fd, PRIME_HANDLE_TO_FD, &mut p)?;
        Ok(p.fd)
    }

    /// Import a foreign dma-buf (e.g. Samsung Vulkan memory) as a scanout framebuffer.
    pub fn import_fd(&self, fd: RawFd, pitch: u32, size: usize) -> io::Result<Buffer> {
        let mut p = PrimeHandle { fd, ..Default::default() };
        ioctl(self.fd, PRIME_FD_TO_HANDLE, &mut p)?;
        let fb_id = self.add_fb(p.handle, pitch, self.width, self.height)?;
        Ok(Buffer { handle: p.handle, fb_id, pitch, size, map: std::ptr::null_mut(), dumb: false })
    }

    pub fn set_crtc(&self, fb_id: u32) -> io::Result<()> {
        let mut conn = self.connector_id;
        let mut c = Crtc {
            set_connectors_ptr: &mut conn as *mut u32 as u64,
            count_connectors: 1,
            crtc_id: self.crtc_id,
            fb_id,
            x: 0,
            y: 0,
            gamma_size: 0,
            mode_valid: 1,
            mode: self.mode,
        };
        ioctl(self.fd, SETCRTC, &mut c)
    }

    pub fn page_flip(&self, fb_id: u32, user: u64) -> io::Result<()> {
        let mut p = PageFlip { crtc_id: self.crtc_id, fb_id, flags: 1 /* EVENT */, reserved: 0, user_data: user };
        ioctl(self.fd, PAGE_FLIP, &mut p)
    }

    #[allow(dead_code)]
    pub fn dirty(&self, fb_id: u32) -> io::Result<()> {
        let mut d = FbDirty { fb_id, ..Default::default() };
        ioctl(self.fd, DIRTYFB, &mut d)
    }

    /// Wait for one page-flip-complete event; returns its timestamp in µs (CLOCK_MONOTONIC).
    pub fn wait_flip(&self, timeout_ms: i32) -> io::Result<Option<u64>> {
        let mut pfd = libc::pollfd { fd: self.fd, events: libc::POLLIN, revents: 0 };
        let r = unsafe { libc::poll(&mut pfd, 1, timeout_ms) };
        if r <= 0 {
            return Ok(None);
        }
        self.read_events()
    }

    pub fn read_events(&self) -> io::Result<Option<u64>> {
        let mut buf = [0u8; 1024];
        let n = unsafe { libc::read(self.fd, buf.as_mut_ptr() as *mut _, buf.len()) };
        if n < 0 {
            return Err(io::Error::last_os_error());
        }
        let mut off = 0usize;
        let mut ts = None;
        while off + 8 <= n as usize {
            let typ = u32::from_ne_bytes(buf[off..off + 4].try_into().unwrap());
            let len = u32::from_ne_bytes(buf[off + 4..off + 8].try_into().unwrap()) as usize;
            if typ == 2 && len >= 32 {
                let sec = u32::from_ne_bytes(buf[off + 16..off + 20].try_into().unwrap()) as u64;
                let usec = u32::from_ne_bytes(buf[off + 20..off + 24].try_into().unwrap()) as u64;
                ts = Some(sec * 1_000_000 + usec);
            }
            if len == 0 {
                break;
            }
            off += len;
        }
        Ok(ts)
    }

    pub fn destroy(&self, b: &Buffer) {
        unsafe {
            let mut id = b.fb_id;
            let _ = ioctl(self.fd, RMFB, &mut id);
            if !b.map.is_null() {
                libc::munmap(b.map as *mut _, b.size);
            }
            if b.dumb {
                let mut h = b.handle;
                let _ = ioctl(self.fd, DESTROY_DUMB, &mut h);
            } else {
                let mut gc = [b.handle, 0u32];
                let _ = ioctl(self.fd, GEM_CLOSE, &mut gc);
            }
        }
    }
}

pub fn now_us() -> u64 {
    let mut t = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut t) };
    t.tv_sec as u64 * 1_000_000 + t.tv_nsec as u64 / 1000
}
