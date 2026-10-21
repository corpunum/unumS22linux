//! Hand-rolled XRGB8888 software renderer with damage clipping: rounded tiles,
//! a 5x7 bitmap font, and a status bar. Everything draws into a cached shadow
//! buffer; only damaged rectangles are copied to the scanout buffer.

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Rect {
    pub x: i32,
    pub y: i32,
    pub w: i32,
    pub h: i32,
}

impl Rect {
    pub fn new(x: i32, y: i32, w: i32, h: i32) -> Rect {
        Rect { x, y, w, h }
    }
    pub fn intersect(&self, o: &Rect) -> Option<Rect> {
        let x0 = self.x.max(o.x);
        let y0 = self.y.max(o.y);
        let x1 = (self.x + self.w).min(o.x + o.w);
        let y1 = (self.y + self.h).min(o.y + o.h);
        if x1 > x0 && y1 > y0 { Some(Rect::new(x0, y0, x1 - x0, y1 - y0)) } else { None }
    }
    pub fn contains(&self, x: i32, y: i32) -> bool {
        x >= self.x && y >= self.y && x < self.x + self.w && y < self.y + self.h
    }
    pub fn area(&self) -> i64 {
        self.w as i64 * self.h as i64
    }
}

pub struct Canvas {
    pub w: i32,
    pub h: i32,
    pub px: Vec<u32>,
}

pub const LABELS: [&str; 8] = ["CHAT", "PHONE", "CAMERA", "FILES", "TERMINAL", "SETTINGS", "SWITCHER", "AGENT"];
const BG: u32 = 0x0b0e13;
const TILE: u32 = 0x1a2029;
const TILE_HI: u32 = 0x2f6fd8;
const BORDER_HI: u32 = 0x9cc3ff;
const TEXT: u32 = 0xf2f4f7;
const DIM: u32 = 0x8a94a3;
const ACCENTS: [u32; 8] = [0x4a9df8, 0x3fbf6f, 0xe0b030, 0x9b6cf0, 0xa0a8b4, 0xe08a30, 0x30c0c0, 0xe05a7a];

pub const STATUS_H: i32 = 110;

pub fn tile_rects(w: i32, h: i32) -> Vec<Rect> {
    let m = 28;
    let cols = 2;
    let rows = 4;
    let top = STATUS_H + m;
    let tw = (w - m * (cols + 1)) / cols;
    let th = (h - top - m * (rows + 1)) / rows;
    let mut v = Vec::new();
    for r in 0..rows {
        for c in 0..cols {
            v.push(Rect::new(m + c * (tw + m), top + m + r * (th + m), tw, th));
        }
    }
    v
}

impl Canvas {
    pub fn new(w: i32, h: i32) -> Canvas {
        Canvas { w, h, px: vec![BG; (w * h) as usize] }
    }

    fn fill(&mut self, r: &Rect, clip: &Rect, color: u32) {
        if let Some(i) = r.intersect(clip) {
            for y in i.y..i.y + i.h {
                let row = (y * self.w) as usize;
                self.px[row + i.x as usize..row + (i.x + i.w) as usize].fill(color);
            }
        }
    }

    /// Rounded rectangle: fill, then cut the four corners back to `under`.
    fn rrect(&mut self, r: &Rect, rad: i32, clip: &Rect, color: u32, under: u32) {
        self.fill(r, clip, color);
        for (cx, cy, sx, sy) in [
            (r.x + rad, r.y + rad, r.x, r.y),
            (r.x + r.w - rad - 1, r.y + rad, r.x + r.w - rad, r.y),
            (r.x + rad, r.y + r.h - rad - 1, r.x, r.y + r.h - rad),
            (r.x + r.w - rad - 1, r.y + r.h - rad - 1, r.x + r.w - rad, r.y + r.h - rad),
        ] {
            let corner = Rect::new(sx, sy, rad, rad);
            if let Some(i) = corner.intersect(clip) {
                for y in i.y..i.y + i.h {
                    for x in i.x..i.x + i.w {
                        let dx = x - cx;
                        let dy = y - cy;
                        if dx * dx + dy * dy > rad * rad {
                            self.px[(y * self.w + x) as usize] = under;
                        }
                    }
                }
            }
        }
    }

    pub fn text(&mut self, s: &str, x: i32, y: i32, scale: i32, clip: &Rect, color: u32) {
        let mut cx = x;
        for ch in s.chars() {
            let g = glyph(ch);
            for (row, bits) in g.iter().enumerate() {
                for col in 0..5 {
                    if bits & (0x10 >> col) != 0 {
                        let r = Rect::new(cx + col * scale, y + row as i32 * scale, scale, scale);
                        self.fill(&r, clip, color);
                    }
                }
            }
            cx += 6 * scale;
        }
    }

    /// Redraw the whole scene, clipped to `clip`.
    pub fn draw(&mut self, clip: &Rect, highlight: Option<usize>, status: &str) {
        let full = Rect::new(0, 0, self.w, self.h);
        let clip = match clip.intersect(&full) {
            Some(c) => c,
            None => return,
        };
        self.fill(&full, &clip, BG);
        // status bar
        let bar = Rect::new(0, 0, self.w, STATUS_H);
        if bar.intersect(&clip).is_some() {
            self.fill(&bar, &clip, 0x12161d);
            self.text("FASTUI", 30, 30, 6, &clip, TEXT);
            self.text(status, 300, 44, 4, &clip, DIM);
        }
        for (i, t) in tile_rects(self.w, self.h).iter().enumerate() {
            if t.intersect(&clip).is_none() {
                continue;
            }
            let hi = highlight == Some(i);
            if hi {
                self.rrect(t, 36, &clip, BORDER_HI, BG);
                let inner = Rect::new(t.x + 6, t.y + 6, t.w - 12, t.h - 12);
                self.rrect(&inner, 30, &clip, TILE_HI, BORDER_HI);
            } else {
                self.rrect(t, 36, &clip, TILE, BG);
            }
            let icon = Rect::new(t.x + 44, t.y + 60, 70, 70);
            self.rrect(&icon, 16, &clip, if hi { TEXT } else { ACCENTS[i] }, if hi { TILE_HI } else { TILE });
            self.text(LABELS[i], t.x + 44, t.y + t.h - 120, 7, &clip, TEXT);
        }
    }
}

/// Copy damaged rectangles from the shadow canvas into a mapped XRGB8888 buffer.
pub fn copy_rects(c: &Canvas, dst: *mut u8, pitch: usize, rects: &[Rect]) -> usize {
    let mut bytes = 0usize;
    for r in rects {
        let full = Rect::new(0, 0, c.w, c.h);
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

fn glyph(c: char) -> [u8; 7] {
    match c.to_ascii_uppercase() {
        'A' => [0x0e, 0x11, 0x11, 0x1f, 0x11, 0x11, 0x11],
        'B' => [0x1e, 0x11, 0x11, 0x1e, 0x11, 0x11, 0x1e],
        'C' => [0x0e, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0e],
        'D' => [0x1e, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1e],
        'E' => [0x1f, 0x10, 0x10, 0x1e, 0x10, 0x10, 0x1f],
        'F' => [0x1f, 0x10, 0x10, 0x1e, 0x10, 0x10, 0x10],
        'G' => [0x0e, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0f],
        'H' => [0x11, 0x11, 0x11, 0x1f, 0x11, 0x11, 0x11],
        'I' => [0x0e, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0e],
        'J' => [0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0c],
        'K' => [0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11],
        'L' => [0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1f],
        'M' => [0x11, 0x1b, 0x15, 0x15, 0x11, 0x11, 0x11],
        'N' => [0x11, 0x11, 0x19, 0x15, 0x13, 0x11, 0x11],
        'O' => [0x0e, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0e],
        'P' => [0x1e, 0x11, 0x11, 0x1e, 0x10, 0x10, 0x10],
        'Q' => [0x0e, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0d],
        'R' => [0x1e, 0x11, 0x11, 0x1e, 0x14, 0x12, 0x11],
        'S' => [0x0f, 0x10, 0x10, 0x0e, 0x01, 0x01, 0x1e],
        'T' => [0x1f, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04],
        'U' => [0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0e],
        'V' => [0x11, 0x11, 0x11, 0x11, 0x11, 0x0a, 0x04],
        'W' => [0x11, 0x11, 0x11, 0x15, 0x15, 0x15, 0x0a],
        'X' => [0x11, 0x11, 0x0a, 0x04, 0x0a, 0x11, 0x11],
        'Y' => [0x11, 0x11, 0x11, 0x0a, 0x04, 0x04, 0x04],
        'Z' => [0x1f, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1f],
        '0' => [0x0e, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0e],
        '1' => [0x04, 0x0c, 0x04, 0x04, 0x04, 0x04, 0x0e],
        '2' => [0x0e, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1f],
        '3' => [0x1f, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0e],
        '4' => [0x02, 0x06, 0x0a, 0x12, 0x1f, 0x02, 0x02],
        '5' => [0x1f, 0x10, 0x1e, 0x01, 0x01, 0x11, 0x0e],
        '6' => [0x06, 0x08, 0x10, 0x1e, 0x11, 0x11, 0x0e],
        '7' => [0x1f, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08],
        '8' => [0x0e, 0x11, 0x11, 0x0e, 0x11, 0x11, 0x0e],
        '9' => [0x0e, 0x11, 0x11, 0x0f, 0x01, 0x02, 0x0c],
        '.' => [0, 0, 0, 0, 0, 0x0c, 0x0c],
        ':' => [0, 0x0c, 0x0c, 0, 0x0c, 0x0c, 0],
        '-' => [0, 0, 0, 0x1f, 0, 0, 0],
        '>' => [0x08, 0x04, 0x02, 0x01, 0x02, 0x04, 0x08],
        '/' => [0x01, 0x01, 0x02, 0x04, 0x08, 0x10, 0x10],
        '%' => [0x18, 0x19, 0x02, 0x04, 0x08, 0x13, 0x03],
        '=' => [0, 0, 0x1f, 0, 0x1f, 0, 0],
        _ => [0; 7],
    }
}
