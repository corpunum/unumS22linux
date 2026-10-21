//! XRGB8888 CPU canvas: clipped fills, anti-aliased rounded rectangles,
//! alpha-mask blits (glyphs), image blits and in-place region scrolling.
//! Every operation is clipped to `clip`, which the renderer sets to one damage
//! rectangle at a time.
#![allow(dead_code)]

#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub struct Rect {
    pub x: i32,
    pub y: i32,
    pub w: i32,
    pub h: i32,
}

impl Rect {
    pub const fn new(x: i32, y: i32, w: i32, h: i32) -> Rect {
        Rect { x, y, w, h }
    }
    pub fn intersect(&self, o: &Rect) -> Option<Rect> {
        let x0 = self.x.max(o.x);
        let y0 = self.y.max(o.y);
        let x1 = (self.x + self.w).min(o.x + o.w);
        let y1 = (self.y + self.h).min(o.y + o.h);
        if x1 > x0 && y1 > y0 { Some(Rect::new(x0, y0, x1 - x0, y1 - y0)) } else { None }
    }
    pub fn hits(&self, o: &Rect) -> bool {
        self.intersect(o).is_some()
    }
    pub fn contains(&self, x: i32, y: i32) -> bool {
        x >= self.x && y >= self.y && x < self.x + self.w && y < self.y + self.h
    }
    pub fn union(&self, o: &Rect) -> Rect {
        if self.w <= 0 || self.h <= 0 {
            return *o;
        }
        let x0 = self.x.min(o.x);
        let y0 = self.y.min(o.y);
        let x1 = (self.x + self.w).max(o.x + o.w);
        let y1 = (self.y + self.h).max(o.y + o.h);
        Rect::new(x0, y0, x1 - x0, y1 - y0)
    }
    pub fn area(&self) -> i64 {
        self.w.max(0) as i64 * self.h.max(0) as i64
    }
    pub fn inset(&self, d: i32) -> Rect {
        Rect::new(self.x + d, self.y + d, self.w - 2 * d, self.h - 2 * d)
    }
    pub fn bottom(&self) -> i32 {
        self.y + self.h
    }
    pub fn right(&self) -> i32 {
        self.x + self.w
    }
}

pub struct Canvas {
    pub w: i32,
    pub h: i32,
    pub px: Vec<u32>,
    pub clip: Rect,
}

#[inline]
pub fn blend(dst: u32, src: u32, a: u32) -> u32 {
    if a >= 255 {
        return src;
    }
    if a == 0 {
        return dst;
    }
    let ia = 255 - a;
    let rb = ((src & 0xff00ff) * a + (dst & 0xff00ff) * ia + 0x800080) >> 8 & 0xff00ff;
    let g = ((src & 0x00ff00) * a + (dst & 0x00ff00) * ia + 0x008000) >> 8 & 0x00ff00;
    rb | g
}

/// Mix two colours (t = 0..255 towards b).
pub fn mix(a: u32, b: u32, t: u32) -> u32 {
    blend(a, b, t)
}

impl Canvas {
    pub fn new(w: i32, h: i32) -> Canvas {
        Canvas { w, h, px: vec![0; (w * h) as usize], clip: Rect::new(0, 0, w, h) }
    }

    pub fn fill(&mut self, r: &Rect, color: u32) {
        if let Some(i) = r.intersect(&self.clip) {
            for y in i.y..i.y + i.h {
                let row = (y * self.w) as usize;
                self.px[row + i.x as usize..row + (i.x + i.w) as usize].fill(color);
            }
        }
    }

    pub fn fill_alpha(&mut self, r: &Rect, color: u32, a: u32) {
        if let Some(i) = r.intersect(&self.clip) {
            for y in i.y..i.y + i.h {
                let row = (y * self.w) as usize;
                for p in &mut self.px[row + i.x as usize..row + (i.x + i.w) as usize] {
                    *p = blend(*p, color, a);
                }
            }
        }
    }

    /// Anti-aliased rounded rectangle (coverage from the distance to the corner arc).
    pub fn rrect(&mut self, r: &Rect, rad: i32, color: u32) {
        let rad = rad.min(r.w / 2).min(r.h / 2).max(0);
        if r.intersect(&self.clip).is_none() {
            return;
        }
        if rad == 0 {
            self.fill(r, color);
            return;
        }
        // middle band and the side bands between the corners
        self.fill(&Rect::new(r.x, r.y + rad, r.w, r.h - 2 * rad), color);
        self.fill(&Rect::new(r.x + rad, r.y, r.w - 2 * rad, rad), color);
        self.fill(&Rect::new(r.x + rad, r.y + r.h - rad, r.w - 2 * rad, rad), color);
        let rf = rad as f32;
        for (cx, cy, sx, sy) in [
            (r.x + rad, r.y + rad, r.x, r.y),
            (r.x + r.w - rad, r.y + rad, r.x + r.w - rad, r.y),
            (r.x + rad, r.y + r.h - rad, r.x, r.y + r.h - rad),
            (r.x + r.w - rad, r.y + r.h - rad, r.x + r.w - rad, r.y + r.h - rad),
        ] {
            let corner = Rect::new(sx, sy, rad, rad);
            if let Some(i) = corner.intersect(&self.clip) {
                for y in i.y..i.y + i.h {
                    for x in i.x..i.x + i.w {
                        let dx = x as f32 + 0.5 - cx as f32;
                        let dy = y as f32 + 0.5 - cy as f32;
                        let d = (dx * dx + dy * dy).sqrt();
                        let cov = (rf - d + 0.5).clamp(0.0, 1.0);
                        if cov > 0.0 {
                            let p = &mut self.px[(y * self.w + x) as usize];
                            *p = blend(*p, color, (cov * 255.0) as u32);
                        }
                    }
                }
            }
        }
    }

    /// Rounded rectangle outline of width `bw` (outer AA, drawn as ring over `inner` fill colour).
    pub fn rrect_border(&mut self, r: &Rect, rad: i32, bw: i32, border: u32, inner: u32) {
        self.rrect(r, rad, border);
        self.rrect(&r.inset(bw), (rad - bw).max(0), inner);
    }

    pub fn circle(&mut self, cx: i32, cy: i32, rad: i32, color: u32) {
        self.rrect(&Rect::new(cx - rad, cy - rad, rad * 2, rad * 2), rad, color);
    }

    /// Blend an 8-bit coverage mask at (x, y).
    pub fn mask(&mut self, x: i32, y: i32, mw: i32, mh: i32, mask: &[u8], color: u32) {
        let r = Rect::new(x, y, mw, mh);
        if let Some(i) = r.intersect(&self.clip) {
            for yy in i.y..i.y + i.h {
                let mrow = ((yy - y) * mw) as usize;
                let prow = (yy * self.w) as usize;
                for xx in i.x..i.x + i.w {
                    let a = mask[mrow + (xx - x) as usize] as u32;
                    if a != 0 {
                        let p = &mut self.px[prow + xx as usize];
                        *p = blend(*p, color, a);
                    }
                }
            }
        }
    }

    /// Nearest-neighbour blit of an XRGB image scaled into `dst`.
    pub fn image(&mut self, dst: &Rect, img: &[u32], iw: i32, ih: i32) {
        if iw <= 0 || ih <= 0 {
            return;
        }
        if let Some(i) = dst.intersect(&self.clip) {
            for y in i.y..i.y + i.h {
                let sy = ((y - dst.y) as i64 * ih as i64 / dst.h as i64) as i32;
                let srow = (sy.min(ih - 1) * iw) as usize;
                let prow = (y * self.w) as usize;
                for x in i.x..i.x + i.w {
                    let sx = ((x - dst.x) as i64 * iw as i64 / dst.w as i64) as i32;
                    self.px[prow + x as usize] = img[srow + sx.min(iw - 1) as usize];
                }
            }
        }
    }

    /// Move the pixels inside `r` up by `dy` (down when negative). The band
    /// that becomes stale is returned so the caller can redraw it.
    pub fn scroll(&mut self, r: &Rect, dy: i32) -> Option<Rect> {
        if dy == 0 {
            return None;
        }
        if dy.abs() >= r.h {
            return Some(*r);
        }
        let w = self.w as usize;
        let (x0, x1) = (r.x as usize, (r.x + r.w) as usize);
        if dy > 0 {
            for y in r.y..r.y + r.h - dy {
                let (dst, src) = (y as usize * w, (y + dy) as usize * w);
                self.px.copy_within(src + x0..src + x1, dst + x0);
            }
            Some(Rect::new(r.x, r.y + r.h - dy, r.w, dy))
        } else {
            let d = -dy;
            for y in (r.y + d..r.y + r.h).rev() {
                let (dst, src) = (y as usize * w, (y - d) as usize * w);
                self.px.copy_within(src + x0..src + x1, dst + x0);
            }
            Some(Rect::new(r.x, r.y, r.w, d))
        }
    }
}

/// Copy rectangles from the shadow canvas into a mapped XRGB8888 scanout buffer.
pub fn copy_rects(c: &Canvas, dst: *mut u8, pitch: usize, rects: &[Rect]) -> usize {
    let mut bytes = 0usize;
    let full = Rect::new(0, 0, c.w, c.h);
    for r in rects {
        let Some(r) = r.intersect(&full) else { continue };
        for y in r.y..r.y + r.h {
            let src = &c.px[(y * c.w + r.x) as usize..(y * c.w + r.x + r.w) as usize];
            unsafe {
                let d = dst.add(y as usize * pitch + r.x as usize * 4) as *mut u32;
                std::ptr::copy_nonoverlapping(src.as_ptr(), d, r.w as usize);
            }
            bytes += r.w as usize * 4;
        }
    }
    bytes
}

/// Merge overlapping or near rectangles so a frame has few, non-overlapping-ish copies.
pub fn merge_rects(v: &mut Vec<Rect>) {
    let mut changed = true;
    while changed {
        changed = false;
        'outer: for i in 0..v.len() {
            for j in i + 1..v.len() {
                let (a, b) = (v[i], v[j]);
                let u = a.union(&b);
                let grown = Rect::new(a.x - 8, a.y - 8, a.w + 16, a.h + 16);
                if grown.hits(&b) || u.area() <= a.area() + b.area() + 20_000 {
                    v[i] = u;
                    v.remove(j);
                    changed = true;
                    break 'outer;
                }
            }
        }
    }
}
