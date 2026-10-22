//! Preview develop: Bayer quad (2x2) or 4x4 block -> one XRGB8888 pixel.
//!
//! Black level, white balance, display gain and gamma are folded into three
//! lookup tables (rebuilt each frame from the previous frame's statistics), so
//! the per-pixel work is 4 (or 16) u16 loads, a few adds, 3 table lookups and
//! one u32 store. Rows are split across threads with std::thread::scope.

pub const BLACK: f32 = 64.0;
pub const WHITE: f32 = 1023.0;

#[derive(Clone, Copy, PartialEq)]
pub enum Cfa {
    Gbrg, // rear GN3: row0 G B, row1 R G
    Grbg, // front IMX374: row0 G R, row1 B G
}

pub struct Luts {
    /// index = sum of `n` samples of one colour (10-bit each)
    pub r: Vec<u8>,
    pub g: Vec<u8>,
    pub b: Vec<u8>,
}

#[derive(Clone, Copy, Default, Debug)]
pub struct Stats {
    pub r: u64,
    pub g: u64,
    pub b: u64,
    pub n: u64,
    pub clipped: u64,
}

impl Stats {
    pub fn add(&mut self, o: &Stats) {
        self.r += o.r;
        self.g += o.g;
        self.b += o.b;
        self.n += o.n;
        self.clipped += o.clipped;
    }
    /// Mean per colour sample minus black, 0..1 (one sample = one photosite).
    pub fn means(&self) -> (f32, f32, f32) {
        if self.n == 0 {
            return (0.0, 0.0, 0.0);
        }
        let n = self.n as f32;
        let span = WHITE - BLACK;
        (
            ((self.r as f32 / n) - BLACK).max(0.0) / span,
            ((self.g as f32 / (2.0 * n)) - BLACK).max(0.0) / span,
            ((self.b as f32 / n) - BLACK).max(0.0) / span,
        )
    }
}

/// Build LUTs for sums of `rn` red/blue samples and `gn` green samples.
pub fn build_luts(rn: usize, gn: usize, wb_r: f32, wb_b: f32, gain: f32) -> Luts {
    let make = |n: usize, k: f32| -> Vec<u8> {
        let span = WHITE - BLACK;
        (0..(1024 * n))
            .map(|s| {
                let v = ((s as f32 / n as f32) - BLACK).max(0.0) / span * k * gain;
                let v = v.min(1.0);
                // sRGB-like gamma 1/2.2
                (v.powf(1.0 / 2.2) * 255.0 + 0.5) as u8
            })
            .collect()
    };
    Luts { r: make(rn, wb_r), g: make(gn, 1.0), b: make(rn, wb_b) }
}

/// Convert one band of output rows. `src` is the whole frame (u16 LE, `stride` bytes/row).
#[allow(clippy::too_many_arguments)]
fn band(src: &[u8], stride: usize, cfa: Cfa, scale: usize, out: &mut [u32], ow: usize, y0: usize, y1: usize,
        luts: &Luts, stat_every: usize) -> Stats {
    let mut st = Stats::default();
    let row16 = |y: usize| -> &[u16] {
        let r = &src[y * stride..y * stride + stride];
        unsafe { std::slice::from_raw_parts(r.as_ptr() as *const u16, stride / 2) }
    };
    let (lr, lg, lb) = (&luts.r[..], &luts.g[..], &luts.b[..]);
    for oy in y0..y1 {
        let dst = &mut out[(oy - y0) * ow..(oy - y0 + 1) * ow];
        let collect = oy % stat_every == 0;
        if scale == 2 {
            let a = row16(2 * oy);
            let b = row16(2 * oy + 1);
            assert!(a.len() >= 2 * ow && b.len() >= 2 * ow && dst.len() >= ow);
            assert!(lr.len() >= 1024 && lb.len() >= 1024 && lg.len() >= 2048);
            let swap = cfa == Cfa::Grbg;
            if !collect {
                for ox in 0..ow {
                    let x = 2 * ox;
                    // SAFETY: bounds asserted above; values masked to the LUT sizes.
                    unsafe {
                        let (p00, p01, p10, p11) = (*a.get_unchecked(x) as usize, *a.get_unchecked(x + 1) as usize,
                                                    *b.get_unchecked(x) as usize, *b.get_unchecked(x + 1) as usize);
                        let (r, bl) = if swap { (p01, p10) } else { (p10, p01) };
                        let g = (p00 + p11) & 2047;
                        *dst.get_unchecked_mut(ox) = ((*lr.get_unchecked(r & 1023) as u32) << 16)
                            | ((*lg.get_unchecked(g) as u32) << 8) | *lb.get_unchecked(bl & 1023) as u32 | 0xFF00_0000;
                    }
                }
                continue;
            }
            for ox in 0..ow {
                let x = 2 * ox;
                let (p00, p01, p10, p11) = (a[x] as usize, a[x + 1] as usize, b[x] as usize, b[x + 1] as usize);
                let (r, g, bl) = match cfa {
                    Cfa::Gbrg => (p10, p00 + p11, p01),
                    Cfa::Grbg => (p01, p00 + p11, p10),
                };
                let (r, g, bl) = (r.min(1023), g.min(2047), bl.min(1023));
                dst[ox] = ((lr[r] as u32) << 16) | ((lg[g] as u32) << 8) | lb[bl] as u32 | 0xFF00_0000;
                {
                    st.r += r as u64;
                    st.g += g as u64;
                    st.b += bl as u64;
                    st.n += 1;
                    if r >= 1000 || bl >= 1000 || g >= 2000 {
                        st.clipped += 1;
                    }
                }
            }
        } else {
            // scale 4: average the 4 quads of a 4x4 block
            let rows = [row16(4 * oy), row16(4 * oy + 1), row16(4 * oy + 2), row16(4 * oy + 3)];
            for ox in 0..ow {
                let x = 4 * ox;
                let (mut r, mut g, mut bl) = (0usize, 0usize, 0usize);
                for qy in 0..2 {
                    let (a, b) = (rows[2 * qy], rows[2 * qy + 1]);
                    for qx in 0..2 {
                        let x2 = x + 2 * qx;
                        let (p00, p01, p10, p11) = (a[x2] as usize, a[x2 + 1] as usize, b[x2] as usize, b[x2 + 1] as usize);
                        match cfa {
                            Cfa::Gbrg => { r += p10; g += p00 + p11; bl += p01; }
                            Cfa::Grbg => { r += p01; g += p00 + p11; bl += p10; }
                        }
                    }
                }
                let (r, g, bl) = (r.min(4095), g.min(8191), bl.min(4095));
                dst[ox] = ((lr[r] as u32) << 16) | ((lg[g] as u32) << 8) | lb[bl] as u32 | 0xFF00_0000;
                if collect {
                    st.r += (r / 4) as u64;
                    st.g += (g / 4) as u64;
                    st.b += (bl / 4) as u64;
                    st.n += 1;
                }
            }
        }
    }
    st
}

/// Persistent worker threads, each pinned to one CPU. Short-lived scoped threads
/// do not spread on Exynos EMS (a new 3 ms task wakes on its parent's core), so
/// the bands go to long-lived pinned workers instead.
pub struct Pool {
    workers: Vec<(std::sync::mpsc::Sender<Job>, std::sync::mpsc::Receiver<Stats>)>,
}

struct Job {
    src: *const u8,
    src_len: usize,
    stride: usize,
    cfa: Cfa,
    scale: usize,
    out: *mut u32,
    ow: usize,
    y0: usize,
    y1: usize,
    luts: *const Luts,
}
// SAFETY: the caller of Pool::develop blocks until every job is done, so the
// borrowed frame, output and LUTs outlive the jobs, and bands do not overlap.
unsafe impl Send for Job {}

impl Pool {
    pub fn new(cpus: &[usize]) -> Pool {
        let workers = cpus.iter().map(|&cpu| {
            let (jtx, jrx) = std::sync::mpsc::channel::<Job>();
            let (rtx, rrx) = std::sync::mpsc::channel::<Stats>();
            std::thread::spawn(move || {
                let mut set: libc::cpu_set_t = unsafe { std::mem::zeroed() };
                unsafe {
                    libc::CPU_SET(cpu, &mut set);
                    libc::sched_setaffinity(0, std::mem::size_of::<libc::cpu_set_t>(), &set);
                }
                for j in jrx {
                    let st = unsafe {
                        let src = std::slice::from_raw_parts(j.src, j.src_len);
                        let out = std::slice::from_raw_parts_mut(j.out, (j.y1 - j.y0) * j.ow);
                        band(src, j.stride, j.cfa, j.scale, out, j.ow, j.y0, j.y1, &*j.luts, 4)
                    };
                    if rtx.send(st).is_err() {
                        break;
                    }
                }
            });
            (jtx, rrx)
        }).collect();
        Pool { workers }
    }
    #[allow(clippy::too_many_arguments)]
    pub fn develop(&self, src: &[u8], stride: usize, cfa: Cfa, scale: usize, out: &mut [u32], ow: usize, oh: usize,
                   luts: &Luts) -> Stats {
        let n = self.workers.len();
        let rows_per = oh.div_ceil(n);
        let mut sent = 0;
        let mut y = 0;
        for (tx, _) in &self.workers {
            if y >= oh {
                break;
            }
            let y1 = (y + rows_per).min(oh);
            let job = Job { src: src.as_ptr(), src_len: src.len(), stride, cfa, scale,
                            out: out[y * ow..y1 * ow].as_mut_ptr(), ow, y0: y, y1, luts };
            tx.send(job).expect("worker gone");
            sent += 1;
            y = y1;
        }
        let mut total = Stats::default();
        for (_, rx) in self.workers.iter().take(sent) {
            total.add(&rx.recv().expect("worker gone"));
        }
        total
    }
}

/// Develop a frame into `out` (ow*oh XRGB8888). Returns statistics.
#[allow(clippy::too_many_arguments)]
pub fn develop(src: &[u8], stride: usize, cfa: Cfa, scale: usize, out: &mut [u32], ow: usize, oh: usize,
               luts: &Luts, threads: usize) -> Stats {
    let stat_every = 4;
    if threads <= 1 {
        return band(src, stride, cfa, scale, out, ow, 0, oh, luts, stat_every);
    }
    let rows_per = oh.div_ceil(threads);
    let mut total = Stats::default();
    std::thread::scope(|s| {
        let mut handles = vec![];
        let mut rest = &mut out[..ow * oh];
        let mut y = 0;
        while y < oh {
            let y1 = (y + rows_per).min(oh);
            let (head, tail) = rest.split_at_mut((y1 - y) * ow);
            rest = tail;
            let y0 = y;
            handles.push(s.spawn(move || band(src, stride, cfa, scale, head, ow, y0, y1, luts, stat_every)));
            y = y1;
        }
        for h in handles {
            total.add(&h.join().unwrap());
        }
    });
    total
}

/// Auto white balance + display gain from the last frame's stats (EMA-smoothed).
pub struct AutoDisplay {
    pub wb_r: f32,
    pub wb_b: f32,
    pub gain: f32,
    pub target: f32,
}

impl AutoDisplay {
    pub fn new() -> Self {
        AutoDisplay { wb_r: 1.8, wb_b: 1.6, gain: 4.0, target: 0.18 }
    }
    pub fn update(&mut self, st: &Stats) {
        let (r, g, b) = st.means();
        if g > 0.002 && r > 0.0005 && b > 0.0005 {
            let wr = (g / r).clamp(0.4, 5.0);
            let wb = (g / b).clamp(0.4, 5.0);
            self.wb_r += 0.25 * (wr - self.wb_r);
            self.wb_b += 0.25 * (wb - self.wb_b);
        }
        // display gain: mean of the white-balanced green toward `target` (linear)
        let want = (self.target / g.max(1e-4)).clamp(1.0, 64.0);
        self.gain += 0.3 * (want - self.gain);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(w: usize, h: usize, cfa: Cfa, rgb: (u16, u16, u16)) -> (Vec<u8>, usize) {
        let stride = (w * 2).div_ceil(32) * 32;
        let mut v = vec![0u8; stride * h];
        for y in 0..h {
            for x in 0..w {
                let val = match (cfa, y % 2, x % 2) {
                    (_, 0, 0) | (_, 1, 1) => rgb.1,
                    (Cfa::Gbrg, 0, 1) | (Cfa::Grbg, 1, 0) => rgb.2,
                    _ => rgb.0,
                };
                v[y * stride + 2 * x..y * stride + 2 * x + 2].copy_from_slice(&val.to_le_bytes());
            }
        }
        (v, stride)
    }

    #[test]
    fn bin2_maps_colours_per_cfa() {
        for cfa in [Cfa::Gbrg, Cfa::Grbg] {
            let (src, stride) = frame(16, 8, cfa, (1023, 64, 64));
            let luts = build_luts(1, 2, 1.0, 1.0, 1.0);
            let mut out = vec![0u32; 8 * 4];
            develop(&src, stride, cfa, 2, &mut out, 8, 4, &luts, 1);
            assert_eq!(out[0] & 0xFFFFFF, 0xFF0000, "red only for {}", cfa == Cfa::Gbrg);
            let (src, stride) = frame(16, 8, cfa, (64, 64, 1023));
            develop(&src, stride, cfa, 2, &mut out, 8, 4, &luts, 1);
            assert_eq!(out[5] & 0xFFFFFF, 0x0000FF);
        }
    }

    #[test]
    fn threads_match_single() {
        let (src, stride) = frame(64, 32, Cfa::Gbrg, (300, 500, 200));
        let luts = build_luts(4, 8, 1.5, 1.2, 2.0);
        let mut a = vec![0u32; 16 * 8];
        let mut b = vec![0u32; 16 * 8];
        let sa = develop(&src, stride, Cfa::Gbrg, 4, &mut a, 16, 8, &luts, 1);
        let sb = develop(&src, stride, Cfa::Gbrg, 4, &mut b, 16, 8, &luts, 3);
        assert_eq!(a, b);
        assert_eq!(sa.n, sb.n);
        assert_eq!(sa.g, sb.g);
    }

    #[test]
    fn auto_display_converges_to_grey() {
        let mut ad = AutoDisplay::new();
        let st = Stats { r: 200 * 100, g: 2 * 400 * 100, b: 150 * 100, n: 100, clipped: 0 };
        for _ in 0..40 {
            ad.update(&st);
        }
        let (r, g, b) = st.means();
        assert!((r * ad.wb_r - g).abs() < 0.01 && (b * ad.wb_b - g).abs() < 0.01);
    }
}
