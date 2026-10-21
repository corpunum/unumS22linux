//! The software ISP stages. Everything works on f32 planes normalised so that
//! 0.0 = black level and 1.0 = sensor white.

use image::codecs::jpeg::JpegEncoder;
use serde_json::{json, Value};

/// Colour correction matrix (camera RGB -> linear sRGB), row-major, rows sum to 1.
/// From the stock Pablo setfiles: setfile_imx374.bin @0x17050 and the identical
/// table in setfile_gn3.bin @0x170e0, int16 Q8 = [332 -47 -29; -82 394 -56; -26 -105 387].
/// It is the least saturating of the four per-illuminant matrices found there
/// (the others: @0x16e68, @0x1732c, @0x17514; a generic 1.61/-0.31 saturation
/// matrix sits at @0x17608). Which illuminant each one belongs to is not decoded.
pub const STOCK_CCM: [f32; 9] = [332.0 / 256.0, -47.0 / 256.0, -29.0 / 256.0, -82.0 / 256.0, 394.0 / 256.0, -56.0 / 256.0, -26.0 / 256.0, -105.0 / 256.0, 387.0 / 256.0];
pub const IDENTITY: [f32; 9] = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0];

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Pattern(pub [u8; 4]); // colour (0=R,1=G,2=B) at (y&1)*2+(x&1)

impl Pattern {
    pub fn parse(s: &str) -> Option<Pattern> {
        let s = s.to_ascii_uppercase();
        if s.len() != 4 {
            return None;
        }
        let mut p = [0u8; 4];
        for (i, c) in s.chars().enumerate() {
            p[i] = match c {
                'R' => 0,
                'G' => 1,
                'B' => 2,
                _ => return None,
            };
        }
        let n = |c| p.iter().filter(|&&x| x == c).count();
        (n(0) == 1 && n(1) == 2 && n(2) == 1).then_some(Pattern(p))
    }
    #[inline]
    pub fn at(&self, y: usize, x: usize) -> u8 {
        self.0[(y & 1) * 2 + (x & 1)]
    }
}

pub struct RawMeta {
    pub width: usize,
    pub height: usize,
    pub stride: usize,
    pub pattern: Pattern,
    pub black: f32,
    pub white: f32,
    pub sensor: String,
}

/// Normalised mosaic, (v - black) / (white - black); may be slightly negative.
pub struct Mosaic {
    pub w: usize,
    pub h: usize,
    pub v: Vec<f32>,
}

pub struct Rgb {
    pub w: usize,
    pub h: usize,
    pub v: Vec<[f32; 3]>,
}

pub struct Img8 {
    pub w: usize,
    pub h: usize,
    pub v: Vec<u8>, // RGB
}

pub fn load_raw(path: &str, m: &RawMeta) -> Result<Mosaic, String> {
    let b = std::fs::read(path).map_err(|e| format!("{path}: {e}"))?;
    let need = m.stride * (m.height - 1) + m.width * 2;
    if b.len() < need {
        return Err(format!("raw too short: {} < {need} ({}x{} stride {})", b.len(), m.width, m.height, m.stride));
    }
    let range = (m.white - m.black).max(1.0);
    let mut v = Vec::with_capacity(m.width * m.height);
    for y in 0..m.height {
        let row = &b[y * m.stride..y * m.stride + m.width * 2];
        for x in 0..m.width {
            let c = u16::from_le_bytes([row[2 * x], row[2 * x + 1]]) as f32;
            v.push((c - m.black) / range);
        }
    }
    Ok(Mosaic { w: m.width, h: m.height, v })
}

/// Mirror an index into [0, n) keeping its parity (so Bayer colours line up).
#[inline]
fn mirror(i: isize, n: usize) -> usize {
    let n = n as isize;
    let j = if i < 0 { -i } else if i >= n { 2 * (n - 1) - i } else { i };
    j.clamp(0, n - 1) as usize
}

impl Mosaic {
    #[inline]
    fn g(&self, y: isize, x: isize) -> f32 {
        self.v[mirror(y, self.h) * self.w + mirror(x, self.w)]
    }
}

pub struct Meter {
    pub mean: f32,
    pub p99: f32,
    pub clipped: f32,
    pub factor: f32,
    pub exposure_us: f32,
    pub gain: f32,
}

/// Simple AE: mean of the central 60% (green sites) -> target. Returns the next
/// exposure/gain pair, exposure first (less noise), then analog gain.
pub fn meter(m: &Mosaic, meta: &RawMeta, exp: f32, gain: f32, max_exp: f32, max_gain: f32, target: f32) -> Meter {
    let (y0, y1, x0, x1) = (m.h / 5, m.h * 4 / 5, m.w / 5, m.w * 4 / 5);
    let mut vals = Vec::with_capacity((y1 - y0) * (x1 - x0) / 2);
    for y in (y0..y1).step_by(1) {
        for x in (x0..x1).step_by(2) {
            let x = if meta.pattern.at(y, x) == 1 { x } else { x + 1 };
            vals.push(m.v[y * m.w + x].max(0.0));
        }
    }
    let n = vals.len().max(1) as f32;
    let mean = vals.iter().sum::<f32>() / n;
    let clipped = vals.iter().filter(|&&v| v > 0.97).count() as f32 / n;
    vals.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let p99 = vals[((vals.len() as f32 * 0.99) as usize).min(vals.len() - 1)];
    // A clipped frame under-reports the mean: step down hard.
    let mut factor = if clipped > 0.02 { 0.25 } else { target / mean.max(1e-4) };
    // do not push the bright 1% into clipping by more than ~1 stop
    if p99 > 0.0 {
        factor = factor.min(1.8 / p99.max(1e-4));
    }
    factor = factor.clamp(1.0 / 64.0, 64.0);
    let total = exp * gain * factor;
    let e = total.clamp(100.0, max_exp);
    let g = (total / e).clamp(1.0, max_gain);
    Meter { mean, p99, clipped, factor, exposure_us: e, gain: g }
}

#[derive(Clone, Copy)]
pub enum Wb {
    GrayWorld,
    Fixed(f32, f32),
}

pub struct Opts {
    pub wb: Wb,
    pub ccm: [f32; 9],
    pub lsc: f32,
    pub ev: f32,
    pub half: bool,
    pub denoise: bool,
    pub auto_black: bool,
}

impl Default for Opts {
    fn default() -> Self {
        Opts { wb: Wb::GrayWorld, ccm: STOCK_CCM, lsc: 0.35, ev: 0.0, half: false, denoise: true, auto_black: true }
    }
}

/// Replace isolated hot/cold pixels with the median of their 4 same-colour neighbours.
pub fn fix_defects(m: &mut Mosaic) -> usize {
    let src = m.v.clone();
    let (w, h) = (m.w, m.h);
    let at = |y: isize, x: isize| src[mirror(y, h) * w + mirror(x, w)];
    let mut fixed = 0;
    for y in 0..h as isize {
        for x in 0..w as isize {
            let c = at(y, x);
            let mut n = [at(y - 2, x), at(y + 2, x), at(y, x - 2), at(y, x + 2)];
            n.sort_by(|a, b| a.partial_cmp(b).unwrap());
            let thr = 0.04f32.max(0.6 * n[3].abs());
            if c > n[3] + thr || c < n[0] - thr {
                m.v[y as usize * w + x as usize] = 0.5 * (n[1] + n[2]);
                fixed += 1;
            }
        }
    }
    fixed
}

/// Radial lens-shading correction: gain = 1 + k * (r / r_corner)^2.
pub fn lens_shading(m: &mut Mosaic, k: f32) {
    if k == 0.0 {
        return;
    }
    let (cx, cy) = ((m.w as f32 - 1.0) / 2.0, (m.h as f32 - 1.0) / 2.0);
    let r2max = cx * cx + cy * cy;
    for y in 0..m.h {
        for x in 0..m.w {
            let (dx, dy) = (x as f32 - cx, y as f32 - cy);
            m.v[y * m.w + x] *= 1.0 + k * (dx * dx + dy * dy) / r2max;
        }
    }
}

/// Grey-world on 2x2 quads that are neither clipped nor in the noise floor.
/// Returns (r_gain, b_gain) with green = 1.
pub fn grey_world(m: &Mosaic, p: Pattern) -> (f32, f32, usize) {
    let mut s = [0f64; 3];
    let mut used = 0usize;
    for y in (0..m.h - 1).step_by(2) {
        for x in (0..m.w - 1).step_by(2) {
            let mut q = [0f32; 3];
            let mut mx = 0f32;
            for (dy, dx) in [(0, 0), (0, 1), (1, 0), (1, 1)] {
                let v = m.v[(y + dy) * m.w + x + dx];
                let c = p.at(y + dy, x + dx) as usize;
                q[c] += if c == 1 { 0.5 * v } else { v };
                mx = mx.max(v);
            }
            let lum = q[0] + q[1] + q[2];
            if mx < 0.92 && lum > 0.01 {
                for c in 0..3 {
                    s[c] += q[c] as f64;
                }
                used += 1;
            }
        }
    }
    if used < 100 || s[0] <= 0.0 || s[2] <= 0.0 {
        return (1.0, 1.0, used);
    }
    (((s[1] / s[0]) as f32).clamp(0.4, 5.0), ((s[1] / s[2]) as f32).clamp(0.4, 5.0), used)
}

/// Malvar-He-Cutler gradient-corrected bilinear demosaic.
pub fn demosaic_mhc(m: &Mosaic, p: Pattern) -> Rgb {
    let (w, h) = (m.w, m.h);
    let mut out = vec![[0f32; 3]; w * h];
    for yy in 0..h {
        let y = yy as isize;
        for xx in 0..w {
            let x = xx as isize;
            let g = |dy: isize, dx: isize| m.g(y + dy, x + dx);
            let c = g(0, 0);
            let col = p.at(yy, xx);
            let mut px = [0f32; 3];
            px[col as usize] = c;
            // cross (N,S,E,W), axial at distance 2, diagonals
            let cross = g(-1, 0) + g(1, 0) + g(0, -1) + g(0, 1);
            let ax2 = g(-2, 0) + g(2, 0) + g(0, -2) + g(0, 2);
            let diag = g(-1, -1) + g(-1, 1) + g(1, -1) + g(1, 1);
            if col == 1 {
                // green site: which colour sits left/right?
                let hcol = p.at(yy, xx + 1) as usize;
                let vcol = 2 - hcol; // the other of R/B
                let h2 = g(0, -2) + g(0, 2);
                let v2 = g(-2, 0) + g(2, 0);
                let hv = (5.0 * c - h2 - diag + 0.5 * v2 + 4.0 * (g(0, -1) + g(0, 1))) / 8.0;
                let vv = (5.0 * c - v2 - diag + 0.5 * h2 + 4.0 * (g(-1, 0) + g(1, 0))) / 8.0;
                px[hcol] = hv;
                px[vcol] = vv;
            } else {
                px[1] = (4.0 * c + 2.0 * cross - ax2) / 8.0;
                let other = 2 - col as usize;
                px[other] = (6.0 * c + 2.0 * diag - 1.5 * ax2) / 8.0;
            }
            out[yy * w + xx] = px;
        }
    }
    Rgb { w, h, v: out }
}

/// 2x2 binning: one RGB pixel per Bayer quad (half resolution, ~2x less noise).
pub fn demosaic_half(m: &Mosaic, p: Pattern) -> Rgb {
    let (w, h) = (m.w / 2, m.h / 2);
    let mut out = vec![[0f32; 3]; w * h];
    for y in 0..h {
        for x in 0..w {
            let mut q = [0f32; 3];
            for (dy, dx) in [(0, 0), (0, 1), (1, 0), (1, 1)] {
                let (yy, xx) = (2 * y + dy, 2 * x + dx);
                let c = p.at(yy, xx) as usize;
                q[c] += if c == 1 { 0.5 } else { 1.0 } * m.v[yy * m.w + xx];
            }
            out[y * w + x] = q;
        }
    }
    Rgb { w, h, v: out }
}

fn luma(p: &[f32; 3]) -> f32 {
    0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2]
}

fn percentile(vals: &mut [f32], q: f32) -> f32 {
    if vals.is_empty() {
        return 0.0;
    }
    let k = ((vals.len() as f32 - 1.0) * q) as usize;
    let (_, v, _) = vals.select_nth_unstable_by(k, |a, b| a.partial_cmp(b).unwrap());
    *v
}

#[inline]
fn srgb(v: f32) -> f32 {
    let v = v.clamp(0.0, 1.0);
    if v <= 0.003_130_8 {
        12.92 * v
    } else {
        1.055 * v.powf(1.0 / 2.4) - 0.055
    }
}

/// Box blur of one channel, separable, radius r.
fn box_blur(src: &[f32], w: usize, h: usize, r: usize) -> Vec<f32> {
    let mut tmp = vec![0f32; w * h];
    let mut out = vec![0f32; w * h];
    let n = (2 * r + 1) as f32;
    for y in 0..h {
        let row = &src[y * w..(y + 1) * w];
        let mut acc: f32 = (0..=2 * r).map(|i| row[mirror(i as isize - r as isize, w)]).sum();
        for x in 0..w {
            tmp[y * w + x] = acc / n;
            acc += row[mirror(x as isize + r as isize + 1, w)] - row[mirror(x as isize - r as isize, w)];
        }
    }
    for x in 0..w {
        let mut acc: f32 = (0..=2 * r).map(|i| tmp[mirror(i as isize - r as isize, h) * w + x]).sum();
        for y in 0..h {
            out[y * w + x] = acc / n;
            acc += tmp[mirror(y as isize + r as isize + 1, h) * w + x] - tmp[mirror(y as isize - r as isize, h) * w + x];
        }
    }
    out
}

/// Per-colour dark offset: 1st percentile of 16x16 block means, capped at 35% of the colour mean.
pub fn dark_offsets(m: &Mosaic, p: Pattern) -> [f32; 3] {
    let mut blocks: [Vec<f32>; 3] = [vec![], vec![], vec![]];
    let mut tot = [0f64; 3];
    let mut cnt = [0usize; 3];
    for by in (0..m.h.saturating_sub(15)).step_by(16) {
        for bx in (0..m.w.saturating_sub(15)).step_by(16) {
            let mut s = [0f32; 3];
            let mut n = [0u32; 3];
            for y in by..by + 16 {
                for x in bx..bx + 16 {
                    let c = p.at(y, x) as usize;
                    s[c] += m.v[y * m.w + x];
                    n[c] += 1;
                }
            }
            for c in 0..3 {
                let v = s[c] / n[c].max(1) as f32;
                blocks[c].push(v);
                tot[c] += v as f64;
                cnt[c] += 1;
            }
        }
    }
    let mut out = [0f32; 3];
    for c in 0..3 {
        if blocks[c].is_empty() {
            continue;
        }
        let mean = (tot[c] / cnt[c] as f64) as f32;
        out[c] = percentile(&mut blocks[c], 0.01).clamp(0.0, 0.35 * mean.max(0.0));
    }
    out
}

pub fn develop(mosaic: &Mosaic, meta: &RawMeta, o: &Opts) -> (Img8, Value) {
    let mut m = Mosaic { w: mosaic.w, h: mosaic.h, v: mosaic.v.clone() };
    let p = meta.pattern;
    let defects = fix_defects(&mut m);
    lens_shading(&mut m, o.lsc);
    // flare / residual black offset, per colour, from 16x16 block means (noise-free):
    // each colour's darkest 1% of blocks goes to black, capped at 35% of that colour's
    // mean so low-key scenes keep their shadows. Per colour, so a black-level error
    // does not turn into tinted shadows after white balance.
    let fl = if o.auto_black { dark_offsets(&m, p) } else { [0.0; 3] };
    if fl.iter().any(|&f| f > 0.0) {
        for y in 0..m.h {
            for x in 0..m.w {
                m.v[y * m.w + x] -= fl[p.at(y, x) as usize];
            }
        }
    }
    let (rg, bg, quads) = match o.wb {
        Wb::GrayWorld => grey_world(&m, p),
        Wb::Fixed(r, b) => (r, b, 0),
    };
    for y in 0..m.h {
        for x in 0..m.w {
            let g = match p.at(y, x) {
                0 => rg,
                2 => bg,
                _ => 1.0,
            };
            m.v[y * m.w + x] *= g;
        }
    }
    let mut rgb = if o.half { demosaic_half(&m, p) } else { demosaic_mhc(&m, p) };
    let c = o.ccm;
    for px in rgb.v.iter_mut() {
        let [r, g, b] = *px;
        *px = [c[0] * r + c[1] * g + c[2] * b, c[3] * r + c[4] * g + c[5] * b, c[6] * r + c[7] * g + c[8] * b];
    }
    // linear-light chroma smoothing before any clipping: zero-mean sensor noise must not be
    // clipped at 0 per channel first, or high-gain channels (blue under warm light) turn
    // the shadows into a coloured cast
    if o.denoise {
        let (w, h) = (rgb.w, rgb.h);
        let l: Vec<f32> = rgb.v.iter().map(luma).collect();
        let rr: Vec<f32> = rgb.v.iter().zip(&l).map(|(p, y)| p[0] - y).collect();
        let bb: Vec<f32> = rgb.v.iter().zip(&l).map(|(p, y)| p[2] - y).collect();
        let r = if o.half { 2 } else { 4 };
        let (rr, bb) = (box_blur(&rr, w, h, r), box_blur(&bb, w, h, r));
        for i in 0..w * h {
            let (rv, bv) = (l[i] + rr[i], l[i] + bb[i]);
            let gv = (l[i] - 0.2126 * rv - 0.0722 * bv) / 0.7152;
            rgb.v[i] = [rv, gv, bv];
        }
    }
    // auto exposure on luminance: bright 0.5% -> ~0.95, mean capped at ~0.28 linear
    let mut ys: Vec<f32> = rgb.v.iter().step_by(7).map(luma).collect();
    let mean = ys.iter().sum::<f32>() / ys.len().max(1) as f32;
    let p995 = percentile(&mut ys, 0.995);
    let flare = 0.2126 * fl[0] + 0.7152 * fl[1] + 0.0722 * fl[2];
    let mut gain = if p995 > 1e-4 { 0.95 / p995 } else { 1.0 };
    if mean > 1e-5 {
        gain = gain.min(0.28 / mean);
    }
    gain = gain.clamp(0.5, 64.0) * 2f32.powf(o.ev);
    // to sRGB with a gentle S-curve; keep linear values above 1 from hue-shifting by
    // scaling the whole pixel down when its max channel exceeds 1
    let (w, h) = (rgb.w, rgb.h);
    let mut yc = vec![0f32; w * h];
    let mut cb = vec![0f32; w * h];
    let mut cr = vec![0f32; w * h];
    for (i, px) in rgb.v.iter().enumerate() {
        let mut q = [px[0].max(0.0) * gain, px[1].max(0.0) * gain, px[2].max(0.0) * gain];
        let mx = q[0].max(q[1]).max(q[2]);
        if mx > 1.0 {
            // soft highlight roll-off toward white
            let t = ((mx - 1.0) / mx).min(1.0);
            for v in q.iter_mut() {
                *v = (*v / mx) * (1.0 - t) + t;
            }
        }
        let s: Vec<f32> = q
            .iter()
            .map(|&v| {
                let e = srgb(v);
                let sc = e * e * (3.0 - 2.0 * e); // smoothstep
                0.8 * e + 0.2 * sc
            })
            .collect();
        yc[i] = 0.299 * s[0] + 0.587 * s[1] + 0.114 * s[2];
        cb[i] = s[2] - yc[i];
        cr[i] = s[0] - yc[i];
    }
    if o.denoise {
        // stronger chroma smoothing when the picture needed a big digital push (dim scene)
        let r = match gain {
            g if g < 4.0 => 3,
            g if g < 12.0 => 5,
            _ => 8,
        };
        let r = if o.half { (r + 1) / 2 } else { r };
        cb = box_blur(&cb, w, h, r);
        cr = box_blur(&cr, w, h, r);
    }
    let mut v = Vec::with_capacity(w * h * 3);
    for i in 0..w * h {
        let (y, b, r) = (yc[i], cb[i], cr[i]);
        let rr = y + r;
        let bb = y + b;
        let gg = (y - 0.299 * rr - 0.114 * bb) / 0.587;
        for c in [rr, gg, bb] {
            v.push((c.clamp(0.0, 1.0) * 255.0 + 0.5) as u8);
        }
    }
    let info = json!({
        "pattern": format!("{:?}", p.0), "black": meta.black, "white": meta.white,
        "defects_fixed": defects, "lsc_k": o.lsc,
        "wb": {"r_gain": rg, "b_gain": bg, "quads": quads},
        "ccm": o.ccm, "exposure_gain": gain, "flare_black": flare, "flare_rgb": fl, "mean_linear": mean, "p995_linear": p995,
        "demosaic": if o.half { "half" } else { "mhc" }, "chroma_denoise": o.denoise,
    });
    (Img8 { w, h, v }, info)
}

/// The "before" picture: the mosaic as grey, black-subtracted and linearly
/// stretched (p99.5 -> white), nothing else.
pub fn naive(m: &Mosaic, _meta: &RawMeta) -> Img8 {
    let mut s: Vec<f32> = m.v.iter().step_by(7).copied().collect();
    let hi = percentile(&mut s, 0.995).max(1e-4);
    let v = m.v.iter().flat_map(|&x| {
        let g = ((x / hi).clamp(0.0, 1.0) * 255.0) as u8;
        [g, g, g]
    });
    Img8 { w: m.w, h: m.h, v: v.collect() }
}

pub fn rotate(img: Img8, deg: u32) -> Img8 {
    let (w, h) = (img.w, img.h);
    let px = |x: usize, y: usize| -> [u8; 3] {
        let o = (y * w + x) * 3;
        [img.v[o], img.v[o + 1], img.v[o + 2]]
    };
    let (nw, nh) = match deg % 360 {
        90 | 270 => (h, w),
        180 => (w, h),
        _ => return img,
    };
    let mut v = Vec::with_capacity(w * h * 3);
    for y in 0..nh {
        for x in 0..nw {
            let p = match deg % 360 {
                90 => px(y, h - 1 - x),
                180 => px(w - 1 - x, h - 1 - y),
                _ => px(w - 1 - y, x),
            };
            v.extend_from_slice(&p);
        }
    }
    Img8 { w: nw, h: nh, v }
}

/// Area-average downscale to fit (max_w, max_h); never upscales.
pub fn downscale_fit(img: &Img8, max_w: usize, max_h: usize) -> Img8 {
    let s = (max_w as f32 / img.w as f32).min(max_h as f32 / img.h as f32).min(1.0);
    let (nw, nh) = (((img.w as f32 * s) as usize).max(1), ((img.h as f32 * s) as usize).max(1));
    let mut v = Vec::with_capacity(nw * nh * 3);
    for y in 0..nh {
        let (y0, y1) = (y * img.h / nh, ((y + 1) * img.h / nh).max(y * img.h / nh + 1));
        for x in 0..nw {
            let (x0, x1) = (x * img.w / nw, ((x + 1) * img.w / nw).max(x * img.w / nw + 1));
            let mut acc = [0u32; 3];
            for yy in y0..y1 {
                for xx in x0..x1 {
                    let o = (yy * img.w + xx) * 3;
                    for c in 0..3 {
                        acc[c] += img.v[o + c] as u32;
                    }
                }
            }
            let n = ((y1 - y0) * (x1 - x0)) as u32;
            for a in acc {
                v.push(((a + n / 2) / n) as u8);
            }
        }
    }
    Img8 { w: nw, h: nh, v }
}

pub fn write_png(path: &str, img: &Img8) -> Result<(), String> {
    let tmp = format!("{path}.tmp");
    let f = std::fs::File::create(&tmp).map_err(|e| format!("{tmp}: {e}"))?;
    let mut enc = png::Encoder::new(std::io::BufWriter::new(f), img.w as u32, img.h as u32);
    enc.set_color(png::ColorType::Rgb);
    enc.set_depth(png::BitDepth::Eight);
    enc.set_compression(png::Compression::Fast);
    let mut wr = enc.write_header().map_err(|e| e.to_string())?;
    wr.write_image_data(&img.v).map_err(|e| e.to_string())?;
    wr.finish().map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, path).map_err(|e| format!("{path}: {e}"))
}

pub fn write_jpeg(path: &str, img: &Img8, q: u8) -> Result<(), String> {
    let tmp = format!("{path}.tmp");
    let f = std::fs::File::create(&tmp).map_err(|e| format!("{tmp}: {e}"))?;
    let mut enc = JpegEncoder::new_with_quality(std::io::BufWriter::new(f), q.clamp(30, 100));
    enc.encode(&img.v, img.w as u32, img.h as u32, image::ExtendedColorType::Rgb8).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, path).map_err(|e| format!("{path}: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn meta(w: usize, h: usize, pat: &str) -> RawMeta {
        RawMeta { width: w, height: h, stride: w * 2, pattern: Pattern::parse(pat).unwrap(), black: 64.0, white: 1023.0, sensor: "t".into() }
    }

    /// A flat-colour scene mosaicked with the given pattern.
    fn flat(w: usize, h: usize, pat: Pattern, rgb: [f32; 3]) -> Mosaic {
        let mut v = vec![0f32; w * h];
        for y in 0..h {
            for x in 0..w {
                v[y * w + x] = rgb[pat.at(y, x) as usize];
            }
        }
        Mosaic { w, h, v }
    }

    #[test]
    fn pattern_parse() {
        assert_eq!(Pattern::parse("GBRG").unwrap().0, [1, 2, 0, 1]);
        assert_eq!(Pattern::parse("grbg").unwrap().0, [1, 0, 2, 1]);
        assert!(Pattern::parse("GGRB").is_some());
        assert!(Pattern::parse("RRGB").is_none());
        assert!(Pattern::parse("GBR").is_none());
    }

    #[test]
    fn stock_ccm_rows_sum_to_one() {
        for r in 0..3 {
            let s: f32 = STOCK_CCM[r * 3..r * 3 + 3].iter().sum();
            assert!((s - 1.0).abs() < 1e-6);
        }
    }

    #[test]
    fn mhc_reconstructs_flat_colour_all_patterns() {
        for pat in ["GBRG", "GRBG", "RGGB", "BGGR"] {
            let p = Pattern::parse(pat).unwrap();
            let m = flat(32, 24, p, [0.3, 0.5, 0.2]);
            let rgb = demosaic_mhc(&m, p);
            for px in &rgb.v {
                assert!((px[0] - 0.3).abs() < 1e-5 && (px[1] - 0.5).abs() < 1e-5 && (px[2] - 0.2).abs() < 1e-5, "{pat} {px:?}");
            }
        }
    }

    #[test]
    fn grey_world_neutralises_a_tint() {
        let p = Pattern::parse("GBRG").unwrap();
        let m = flat(64, 64, p, [0.1, 0.2, 0.4]);
        let (r, b, used) = grey_world(&m, p);
        assert!(used > 100);
        assert!((r - 2.0).abs() < 1e-3 && (b - 0.5).abs() < 1e-3);
    }

    #[test]
    fn defects_are_removed() {
        let p = Pattern::parse("GBRG").unwrap();
        let mut m = flat(40, 40, p, [0.2, 0.2, 0.2]);
        m.v[20 * 40 + 20] = 1.0;
        m.v[10 * 40 + 11] = -0.5;
        assert_eq!(fix_defects(&mut m), 2);
        assert!((m.v[20 * 40 + 20] - 0.2).abs() < 1e-6);
    }

    #[test]
    fn develop_grey_scene_is_neutral() {
        let mt = meta(64, 48, "GRBG");
        let m = flat(64, 48, mt.pattern, [0.05, 0.1, 0.07]); // tinted grey
        let o = Opts { lsc: 0.0, ..Opts::default() };
        let (img, info) = develop(&m, &mt, &o);
        assert_eq!((img.w, img.h), (64, 48));
        let c = &img.v[(24 * 64 + 32) * 3..(24 * 64 + 32) * 3 + 3];
        assert!((c[0] as i32 - c[1] as i32).abs() <= 2 && (c[2] as i32 - c[1] as i32).abs() <= 2, "{c:?} {info}");
        assert!(c[1] > 60, "auto exposure should lift a dim scene: {c:?}");
    }

    #[test]
    fn meter_pushes_dark_up_and_bright_down() {
        let mt = meta(64, 48, "GBRG");
        let dark = flat(64, 48, mt.pattern, [0.01, 0.01, 0.01]);
        let r = meter(&dark, &mt, 10000.0, 2.0, 30000.0, 16.0, 0.16);
        assert_eq!(r.exposure_us, 30000.0);
        assert!(r.gain > 2.0 && r.gain <= 16.0);
        let bright = flat(64, 48, mt.pattern, [1.0, 1.0, 1.0]);
        let r = meter(&bright, &mt, 10000.0, 2.0, 30000.0, 16.0, 0.16);
        assert!(r.exposure_us * r.gain < 10000.0 * 2.0 * 0.5);
    }

    #[test]
    fn rotate_and_downscale() {
        let img = Img8 { w: 3, h: 2, v: (0..18).collect() };
        let r = rotate(Img8 { w: 3, h: 2, v: img.v.clone() }, 90);
        assert_eq!((r.w, r.h), (2, 3));
        assert_eq!(&r.v[0..3], &img.v[9..12]); // bottom-left becomes top-left
        let r = rotate(rotate(r, 90), 180);
        assert_eq!(r.v, img.v);
        let d = downscale_fit(&Img8 { w: 100, h: 50, v: vec![7; 100 * 50 * 3] }, 10, 10);
        assert_eq!((d.w, d.h), (10, 5));
        assert!(d.v.iter().all(|&x| x == 7));
    }

    #[test]
    fn load_raw_honours_stride_and_black() {
        let dir = std::env::temp_dir().join(format!("s22dev-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("t.raw");
        let mut b = vec![0u8; 8 * 2];
        for (i, v) in [64u16, 1023, 543, 0].iter().enumerate() {
            b[2 * i..2 * i + 2].copy_from_slice(&v.to_le_bytes());
        }
        b[8..10].copy_from_slice(&64u16.to_le_bytes());
        std::fs::write(&p, &b).unwrap();
        let mut mt = meta(2, 2, "GBRG");
        mt.stride = 8;
        let m = load_raw(p.to_str().unwrap(), &mt).unwrap();
        assert_eq!(m.v.len(), 4);
        assert!(m.v[0].abs() < 1e-6 && (m.v[1] - 1.0).abs() < 1e-6 && (m.v[2] - 0.0).abs() < 1e-6);
        std::fs::remove_dir_all(dir).ok();
    }
}
