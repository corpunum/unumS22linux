//! Byte-exact V4L2 / dma-heap structures and the Pablo sensor-group session.
//!
//! This is a port of `s22-camera.py` `Session` (the reviewed capture path):
//! the same ioctls, in the same order, with the same struct bytes. The byte
//! builders are pinned against the Python builders by `test_campreview.py`
//! (`s22-campreview dump-structs`).

use std::ffi::CString;
use std::io;

pub const IOC_WRITE: u64 = 1;
pub const IOC_READ: u64 = 2;

pub const fn ioc(dir: u64, typ: u8, nr: u64, size: u64) -> u64 {
    (dir << 30) | (size << 16) | ((typ as u64) << 8) | nr
}
pub const fn iowr(typ: u8, nr: u64, size: u64) -> u64 {
    ioc(IOC_READ | IOC_WRITE, typ, nr, size)
}

pub const VIDIOC_S_FMT: u64 = iowr(b'V', 5, 208);
pub const VIDIOC_REQBUFS: u64 = iowr(b'V', 8, 20);
pub const VIDIOC_QBUF: u64 = iowr(b'V', 15, 88);
pub const VIDIOC_DQBUF: u64 = iowr(b'V', 17, 88);
pub const VIDIOC_STREAMON: u64 = ioc(IOC_WRITE, b'V', 18, 4);
pub const VIDIOC_STREAMOFF: u64 = ioc(IOC_WRITE, b'V', 19, 4);
pub const VIDIOC_S_PARM: u64 = iowr(b'V', 22, 204);
pub const VIDIOC_G_CTRL: u64 = iowr(b'V', 27, 8);
pub const VIDIOC_S_CTRL: u64 = iowr(b'V', 28, 8);
pub const VIDIOC_S_INPUT: u64 = iowr(b'V', 39, 4);
pub const VIDIOC_S_EXT_CTRLS: u64 = iowr(b'V', 72, 32);
pub const DMA_HEAP_IOCTL_ALLOC: u64 = iowr(b'H', 0, 24);
pub const DMA_BUF_IOCTL_SYNC: u64 = ioc(IOC_WRITE, b'b', 0, 8);
pub const DMA_BUF_SYNC_READ: u64 = 1;
pub const DMA_BUF_SYNC_WRITE: u64 = 2;
pub const DMA_BUF_SYNC_END: u64 = 4;

pub const BUF_TYPE_CAPTURE_MPLANE: u32 = 9;
pub const BUF_TYPE_OUTPUT_MPLANE: u32 = 10;
pub const MEMORY_DMABUF: u32 = 4;
pub const BUF_FLAG_ERROR: u32 = 0x40;

pub const fn fourcc(s: &[u8; 4]) -> u32 {
    (s[0] as u32) | ((s[1] as u32) << 8) | ((s[2] as u32) << 16) | ((s[3] as u32) << 24)
}
pub const PIX_SRGB36P_SP: u32 = fourcc(b"SRP6");
pub const PIX_SBGGR16: u32 = fourcc(b"BYR2");

pub const CID_IS_S_STREAM: u32 = 10096654;
pub const CID_IS_G_STREAM: u32 = 10096655;
pub const CID_IS_S_SENSOR_SIZE: u32 = 10096709;
pub const CID_SENSOR_SET_EXTENDED_MODE: u32 = 10104853;
pub const CTRL_CLASS_CAMERA: u32 = 0x009A_0000;
pub const CID_SENSOR_BASE: u32 = CTRL_CLASS_CAMERA | 0x3000;
pub const CID_SENSOR_SET_AE_TARGET: u32 = CID_SENSOR_BASE + 1;
pub const CID_SENSOR_SET_ANALOG_GAIN: u32 = CID_SENSOR_BASE + 18;
pub const CID_SENSOR_SET_DIGITAL_GAIN: u32 = CID_SENSOR_BASE + 19;
pub const SENSOR_SCENARIO_VISION: u32 = 1;

pub const SIZE_OF_META_PLANE: usize = 57344;
pub const SHOT_MAGIC_NUMBER: u32 = 0x5678_9234;
pub const OFF_SHOT_MAGIC: usize = 37936;
pub const OFF_NODE_GROUP: usize = 88;
pub const NODE_SIZE: usize = 200;
pub const VC0_GUARD_BYTES: usize = 1 << 20;
pub const IS_VIDEO_SS0_NUM: u32 = 1;
pub const IS_VIDEO_SS0VC0_NUM: u32 = 110;
pub const DMA_HEAP: &str = "/dev/dma_heap/system";

pub const fn align(v: usize, to: usize) -> usize {
    v.div_ceil(to) * to
}
pub const fn page_round(v: usize) -> usize {
    align(v, 4096)
}
pub fn vc0_stride(width: u32) -> usize {
    align(width as usize * 2, 32)
}
pub fn leader_image_size(w: u32, h: u32) -> usize {
    align(w as usize * h as usize * 12 / 8, 16) * 3
}

fn put32(b: &mut [u8], off: usize, v: u32) {
    b[off..off + 4].copy_from_slice(&v.to_le_bytes());
}
fn put64(b: &mut [u8], off: usize, v: u64) {
    b[off..off + 8].copy_from_slice(&v.to_le_bytes());
}
pub fn get32(b: &[u8], off: usize) -> u32 {
    u32::from_le_bytes(b[off..off + 4].try_into().unwrap())
}
pub fn get64(b: &[u8], off: usize) -> u64 {
    u64::from_le_bytes(b[off..off + 8].try_into().unwrap())
}

pub fn s_input_value(scenario: u32, position: u32, vindex: u32, leader: u32) -> u32 {
    ((scenario & 0x3F) << 26) | ((position & 0xFF) << 16) | ((vindex & 0xFF) << 8) | (leader & 0xF)
}
pub fn sensor_size_value(w: u32, h: u32) -> i32 {
    (((w & 0xFFFF) << 16) | (h & 0xFFFF)) as i32
}

pub fn make_format_mplane(buf_type: u32, w: u32, h: u32, pix: u32, planes: u8, bpl0: u32) -> Vec<u8> {
    let mut b = vec![0u8; 208];
    put32(&mut b, 0, buf_type);
    put32(&mut b, 8, w);
    put32(&mut b, 12, h);
    put32(&mut b, 16, pix);
    put32(&mut b, 28, 0); // plane_fmt[0].sizeimage
    put32(&mut b, 32, bpl0); // plane_fmt[0].bytesperline
    b[188] = planes;
    b[189] = 0;
    b
}
pub fn make_reqbufs(count: u32, buf_type: u32) -> Vec<u8> {
    let mut b = vec![0u8; 20];
    put32(&mut b, 0, count);
    put32(&mut b, 4, buf_type);
    put32(&mut b, 8, MEMORY_DMABUF);
    b
}
pub fn make_streamparm_capture(fps: u32) -> Vec<u8> {
    let mut b = vec![0u8; 204];
    put32(&mut b, 0, BUF_TYPE_CAPTURE_MPLANE);
    put32(&mut b, 12, 1);
    put32(&mut b, 16, fps);
    b
}
pub fn make_control(cid: u32, value: i32) -> Vec<u8> {
    let mut b = vec![0u8; 8];
    put32(&mut b, 0, cid);
    put32(&mut b, 4, value as u32);
    b
}
/// struct v4l2_plane[2] for one QBUF/DQBUF.
pub fn make_planes(p: &[(i32, u32, u32)]) -> Vec<u8> {
    let mut b = vec![0u8; 64 * 2];
    for (i, &(fd, length, used)) in p.iter().enumerate() {
        put32(&mut b, i * 64, used);
        put32(&mut b, i * 64 + 4, length);
        put64(&mut b, i * 64 + 8, fd as u32 as u64);
    }
    b
}
pub fn make_buffer(index: u32, buf_type: u32, planes_addr: u64, nplanes: u32) -> Vec<u8> {
    let mut b = vec![0u8; 88];
    put32(&mut b, 0, index);
    put32(&mut b, 4, buf_type);
    put32(&mut b, 60, MEMORY_DMABUF);
    put64(&mut b, 64, planes_addr);
    put32(&mut b, 72, nplanes);
    b
}
/// camera2_shot_ext for the sensor-group leader meta plane (same bytes as build_shot()).
pub fn build_shot(w: u32, h: u32, cap_pix: u32, device: u32) -> Vec<u8> {
    let mut m = vec![0u8; SIZE_OF_META_PLANE];
    put32(&mut m, OFF_SHOT_MAGIC, SHOT_MAGIC_NUMBER);
    let leader = OFF_NODE_GROUP;
    put32(&mut m, leader, IS_VIDEO_SS0_NUM + device);
    put32(&mut m, leader + 4, 0);
    let cap = OFF_NODE_GROUP + NODE_SIZE;
    put32(&mut m, cap, IS_VIDEO_SS0VC0_NUM + 4 * device);
    put32(&mut m, cap + 4, 1);
    for (i, v) in [0, 0, w, h].iter().enumerate() {
        put32(&mut m, cap + 8 + 4 * i, *v);
        put32(&mut m, cap + 24 + 4 * i, *v);
    }
    put32(&mut m, cap + 44, cap_pix);
    put32(&mut m, cap + 56, w);
    put32(&mut m, cap + 60, h);
    m
}
/// struct v4l2_ext_control[n] (20 bytes each, packed) + struct v4l2_ext_controls (32 bytes).
pub fn make_ext_controls(ctrls: &[(u32, i64)], array_addr: u64) -> (Vec<u8>, Vec<u8>) {
    let mut arr = vec![0u8; 20 * ctrls.len()];
    for (i, &(cid, v)) in ctrls.iter().enumerate() {
        put32(&mut arr, i * 20, cid);
        put64(&mut arr, i * 20 + 12, v as u64);
    }
    let mut hdr = vec![0u8; 32];
    put32(&mut hdr, 0, CTRL_CLASS_CAMERA);
    put32(&mut hdr, 4, ctrls.len() as u32);
    put64(&mut hdr, 24, array_addr);
    (arr, hdr)
}

pub fn ioctl(fd: i32, req: u64, buf: &mut [u8]) -> io::Result<()> {
    let r = unsafe { libc::ioctl(fd, req as _, buf.as_mut_ptr()) };
    if r < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

pub fn open_rw(path: &str) -> io::Result<i32> {
    let c = CString::new(path).unwrap();
    let fd = unsafe { libc::open(c.as_ptr(), libc::O_RDWR | libc::O_NONBLOCK | libc::O_CLOEXEC) };
    if fd < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(fd)
    }
}

pub struct DmaBuf {
    pub fd: i32,
    pub size: usize,
    pub ptr: *mut u8,
}

impl DmaBuf {
    pub fn alloc(size: usize) -> io::Result<DmaBuf> {
        let heap = open_rw(DMA_HEAP)?;
        let mut data = vec![0u8; 24];
        put64(&mut data, 0, size as u64);
        put32(&mut data, 12, (libc::O_RDWR | libc::O_CLOEXEC) as u32);
        let r = ioctl(heap, DMA_HEAP_IOCTL_ALLOC, &mut data);
        unsafe { libc::close(heap) };
        r?;
        let fd = get32(&data, 8) as i32;
        let ptr = unsafe {
            libc::mmap(std::ptr::null_mut(), size, libc::PROT_READ | libc::PROT_WRITE, libc::MAP_SHARED, fd, 0)
        };
        if ptr == libc::MAP_FAILED {
            let e = io::Error::last_os_error();
            unsafe { libc::close(fd) };
            return Err(e);
        }
        Ok(DmaBuf { fd, size, ptr: ptr as *mut u8 })
    }
    pub fn sync(&self, flags: u64) {
        let mut b = flags.to_le_bytes();
        let _ = ioctl(self.fd, DMA_BUF_IOCTL_SYNC, &mut b);
    }
    pub fn slice(&self, len: usize) -> &[u8] {
        unsafe { std::slice::from_raw_parts(self.ptr, len.min(self.size)) }
    }
    pub fn write(&self, data: &[u8]) {
        self.sync(DMA_BUF_SYNC_WRITE);
        unsafe { std::ptr::copy_nonoverlapping(data.as_ptr(), self.ptr, data.len().min(self.size)) };
        self.sync(DMA_BUF_SYNC_WRITE | DMA_BUF_SYNC_END);
    }
}

impl Drop for DmaBuf {
    fn drop(&mut self) {
        unsafe {
            libc::munmap(self.ptr as *mut libc::c_void, self.size);
            libc::close(self.fd);
        }
    }
}

pub struct Preset {
    pub name: &'static str,
    pub position: u32,
    pub device: u32,
    pub width: u32,
    pub height: u32,
    pub fps: u32,
    pub bayer: &'static str,
    pub sensor: &'static str,
    pub max_again: f64,
}

pub fn preset(name: &str) -> Option<Preset> {
    match name {
        "rear" => Some(Preset { name: "rear", position: 0, device: 0, width: 2040, height: 1532, fps: 30,
                               bayer: "GBRG", sensor: "S5KGN3", max_again: 16.0 }),
        "front" => Some(Preset { name: "front", position: 1, device: 1, width: 1824, height: 1368, fps: 30,
                                bayer: "GRBG", sensor: "IMX374", max_again: 8.0 }),
        _ => None,
    }
}

pub struct Session {
    pub w: u32,
    pub h: u32,
    pub fps: u32,
    pub device: u32,
    pub position: u32,
    pub stride: usize,
    pub image_len: usize,
    pub lfd: i32,
    pub vfd: i32,
    pub lbufs: Vec<(DmaBuf, DmaBuf)>,
    pub vbufs: Vec<(DmaBuf, DmaBuf)>,
    n_leader: u32,
    n_vc: u32,
    planes: Vec<u8>,
    st_leader_reqbufs: bool,
    st_vc_reqbufs: bool,
    st_vc_on: bool,
    st_leader_on: bool,
    st_front: bool,
    pub teardown_errors: Vec<String>,
    shot: Vec<u8>,
    pub stopped: bool,
}

fn err(what: &str, e: io::Error) -> String {
    format!("{what} failed: {e}")
}

impl Session {
    pub fn new(p: &Preset, n_leader: u32, n_vc: u32) -> Session {
        let stride = vc0_stride(p.width);
        Session {
            w: p.width, h: p.height, fps: p.fps, device: p.device, position: p.position,
            stride, image_len: stride * p.height as usize, lfd: -1, vfd: -1,
            lbufs: vec![], vbufs: vec![], n_leader, n_vc, planes: vec![0u8; 128],
            st_leader_reqbufs: false, st_vc_reqbufs: false, st_vc_on: false, st_leader_on: false,
            st_front: false, teardown_errors: vec![],
            shot: build_shot(p.width, p.height, PIX_SBGGR16, p.device), stopped: false,
        }
    }

    pub fn leader_path(&self) -> String {
        format!("/dev/video{}", 100 + IS_VIDEO_SS0_NUM + self.device)
    }
    pub fn vc0_path(&self) -> String {
        format!("/dev/video{}", 100 + IS_VIDEO_SS0VC0_NUM + 4 * self.device)
    }

    fn qbuf(&mut self, leader: bool, index: usize) -> Result<(), String> {
        let (fd, t, img, meta, output) = if leader {
            let (i, m) = &self.lbufs[index];
            (self.lfd, BUF_TYPE_OUTPUT_MPLANE, (i.fd, i.size), (m.fd, m.size), true)
        } else {
            let (i, m) = &self.vbufs[index];
            (self.vfd, BUF_TYPE_CAPTURE_MPLANE, (i.fd, i.size), (m.fd, m.size), false)
        };
        let used = |s: usize| if output { s as u32 } else { 0 };
        self.planes = make_planes(&[(img.0, img.1 as u32, used(img.1)), (meta.0, meta.1 as u32, used(meta.1))]);
        let mut b = make_buffer(index as u32, t, self.planes.as_ptr() as u64, 2);
        ioctl(fd, VIDIOC_QBUF, &mut b).map_err(|e| err(if leader { "QBUF leader" } else { "QBUF vc0" }, e))
    }

    pub fn queue_leader(&mut self, index: usize) -> Result<(), String> {
        self.lbufs[index].1.write(&self.shot);
        self.qbuf(true, index)
    }
    pub fn queue_vc(&mut self, index: usize) -> Result<(), String> {
        self.qbuf(false, index)
    }

    /// DQBUF; Ok(None) on EAGAIN. Returns (index, flags).
    pub fn dqbuf(&mut self, leader: bool) -> Result<Option<(usize, u32)>, String> {
        let (fd, t) = if leader { (self.lfd, BUF_TYPE_OUTPUT_MPLANE) } else { (self.vfd, BUF_TYPE_CAPTURE_MPLANE) };
        self.planes = vec![0u8; 128];
        let mut b = make_buffer(0, t, self.planes.as_ptr() as u64, 2);
        match ioctl(fd, VIDIOC_DQBUF, &mut b) {
            Ok(()) => Ok(Some((get32(&b, 0) as usize, get32(&b, 12)))),
            Err(e) if e.raw_os_error() == Some(libc::EAGAIN) => Ok(None),
            Err(e) => Err(err("DQBUF", e)),
        }
    }

    pub fn start(&mut self) -> Result<(), String> {
        let (out_t, cap_t) = (BUF_TYPE_OUTPUT_MPLANE, BUF_TYPE_CAPTURE_MPLANE);
        self.lfd = open_rw(&self.leader_path()).map_err(|e| err("open leader", e))?;
        let mut v = s_input_value(SENSOR_SCENARIO_VISION, self.position, IS_VIDEO_SS0_NUM + self.device, 1)
            .to_le_bytes();
        ioctl(self.lfd, VIDIOC_S_INPUT, &mut v).map_err(|e| err("S_INPUT", e))?;
        ioctl(self.lfd, VIDIOC_S_PARM, &mut make_streamparm_capture(self.fps)).map_err(|e| err("S_PARM", e))?;
        ioctl(self.lfd, VIDIOC_S_CTRL, &mut make_control(CID_IS_S_SENSOR_SIZE, sensor_size_value(self.w, self.h)))
            .map_err(|e| err("S_CTRL sensor_size", e))?;
        ioctl(self.lfd, VIDIOC_S_CTRL, &mut make_control(CID_SENSOR_SET_EXTENDED_MODE, 0))
            .map_err(|e| err("S_CTRL ex_mode", e))?;
        ioctl(self.lfd, VIDIOC_S_FMT, &mut make_format_mplane(out_t, self.w, self.h, PIX_SRGB36P_SP, 2, 0))
            .map_err(|e| err("S_FMT leader", e))?;
        self.vfd = open_rw(&self.vc0_path()).map_err(|e| err("open vc0", e))?;
        ioctl(self.vfd, VIDIOC_S_FMT, &mut make_format_mplane(cap_t, self.w, self.h, PIX_SBGGR16, 2, self.stride as u32))
            .map_err(|e| err("S_FMT vc0", e))?;
        ioctl(self.lfd, VIDIOC_REQBUFS, &mut make_reqbufs(self.n_leader, out_t)).map_err(|e| err("REQBUFS leader", e))?;
        self.st_leader_reqbufs = true;
        ioctl(self.vfd, VIDIOC_REQBUFS, &mut make_reqbufs(self.n_vc, cap_t)).map_err(|e| err("REQBUFS vc0", e))?;
        self.st_vc_reqbufs = true;
        let limg = page_round(leader_image_size(self.w, self.h));
        for _ in 0..self.n_leader {
            self.lbufs.push((
                DmaBuf::alloc(limg).map_err(|e| err("dma alloc", e))?,
                DmaBuf::alloc(SIZE_OF_META_PLANE).map_err(|e| err("dma alloc", e))?,
            ));
        }
        for _ in 0..self.n_vc {
            self.vbufs.push((
                DmaBuf::alloc(page_round(self.image_len) + VC0_GUARD_BYTES).map_err(|e| err("dma alloc", e))?,
                DmaBuf::alloc(SIZE_OF_META_PLANE).map_err(|e| err("dma alloc", e))?,
            ));
        }
        for i in 0..self.n_vc as usize {
            self.queue_vc(i)?;
        }
        // VC0 must start before the leader (is_subdev_start refuses once the leader runs).
        ioctl(self.vfd, VIDIOC_STREAMON, &mut cap_t.to_le_bytes()).map_err(|e| err("STREAMON vc0", e))?;
        self.st_vc_on = true;
        for i in 0..self.n_leader as usize {
            self.queue_leader(i)?;
        }
        ioctl(self.lfd, VIDIOC_STREAMON, &mut out_t.to_le_bytes()).map_err(|e| err("STREAMON leader", e))?;
        self.st_leader_on = true;
        self.st_front = true;
        // BLOCKING start (noblock=0), as in the reviewed client (review 2026-10-08).
        ioctl(self.lfd, VIDIOC_S_CTRL, &mut make_control(CID_IS_S_STREAM, 1)).map_err(|e| err("IS_S_STREAM on", e))?;
        Ok(())
    }

    pub fn g_ctrl(&self, cid: u32) -> Result<i32, String> {
        let mut c = make_control(cid, 0);
        ioctl(self.lfd, VIDIOC_G_CTRL, &mut c).map_err(|e| err("G_CTRL", e))?;
        Ok(get32(&c, 4) as i32)
    }

    /// One S_EXT_CTRLS on the leader, only while IS_G_STREAM reads 1 (same gate as the client).
    pub fn set_cis(&self, ctrls: &[(u32, i64)]) -> Result<(), String> {
        if ctrls.is_empty() {
            return Ok(());
        }
        let s = self.g_ctrl(CID_IS_G_STREAM)?;
        if s != 1 {
            return Err(format!("sensor not streaming (IS_G_STREAM={s})"));
        }
        let (arr, _) = make_ext_controls(ctrls, 0);
        let (_, mut hdr) = make_ext_controls(ctrls, arr.as_ptr() as u64);
        ioctl(self.lfd, VIDIOC_S_EXT_CTRLS, &mut hdr).map_err(|e| err("S_EXT_CTRLS", e))
    }

    pub fn stop(&mut self) {
        if self.stopped {
            return;
        }
        self.stopped = true;
        let (out_t, cap_t) = (BUF_TYPE_OUTPUT_MPLANE, BUF_TYPE_CAPTURE_MPLANE);
        let step = |what: &str, r: io::Result<()>, errs: &mut Vec<String>| {
            if let Err(e) = r {
                errs.push(format!("{what}: {e}"));
            }
        };
        let mut errs = vec![];
        if self.lfd >= 0 && self.st_front {
            step("IS_S_STREAM off", ioctl(self.lfd, VIDIOC_S_CTRL, &mut make_control(CID_IS_S_STREAM, 0)), &mut errs);
        }
        if self.lfd >= 0 && self.st_leader_on {
            step("STREAMOFF leader", ioctl(self.lfd, VIDIOC_STREAMOFF, &mut out_t.to_le_bytes()), &mut errs);
        }
        if self.vfd >= 0 && self.st_vc_on {
            step("STREAMOFF vc0", ioctl(self.vfd, VIDIOC_STREAMOFF, &mut cap_t.to_le_bytes()), &mut errs);
        }
        if self.vfd >= 0 && self.st_vc_reqbufs {
            step("REQBUFS(0) vc0", ioctl(self.vfd, VIDIOC_REQBUFS, &mut make_reqbufs(0, cap_t)), &mut errs);
        }
        if self.lfd >= 0 && self.st_leader_reqbufs {
            step("REQBUFS(0) leader", ioctl(self.lfd, VIDIOC_REQBUFS, &mut make_reqbufs(0, out_t)), &mut errs);
        }
        if self.vfd >= 0 {
            unsafe { libc::close(self.vfd) };
            self.vfd = -1;
        }
        if self.lfd >= 0 {
            unsafe { libc::close(self.lfd) };
            self.lfd = -1;
        }
        self.vbufs.clear();
        self.lbufs.clear();
        self.teardown_errors.extend(errs);
    }
}

impl Drop for Session {
    fn drop(&mut self) {
        self.stop();
    }
}
