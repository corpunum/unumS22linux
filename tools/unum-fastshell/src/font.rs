//! Anti-aliased text: Noto Sans (Latin + Greek subset, vendored, OFL-1.1)
//! rasterised with ab_glyph into a glyph cache of 8-bit coverage masks.
//! Glyphs are placed on whole pixels (no subpixel positioning).
#![allow(dead_code)]
use crate::gfx::Canvas;
use ab_glyph::{Font, FontRef, GlyphId, PxScale, ScaleFont};
use std::collections::HashMap;

static REGULAR: &[u8] = include_bytes!("../fonts/NotoSans-Regular-LatinGreek.ttf");
static BOLD: &[u8] = include_bytes!("../fonts/NotoSans-Bold-LatinGreek.ttf");

#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub enum Face {
    Regular,
    Bold,
}

struct Glyph {
    left: i32,
    top: i32,
    w: i32,
    h: i32,
    mask: Vec<u8>,
}

pub struct Fonts {
    regular: FontRef<'static>,
    bold: FontRef<'static>,
    glyphs: HashMap<(Face, u16, u16), Option<Glyph>>,
    adv: HashMap<(Face, char, u16), f32>,
}

impl Fonts {
    pub fn new() -> Fonts {
        Fonts {
            regular: FontRef::try_from_slice(REGULAR).expect("regular font"),
            bold: FontRef::try_from_slice(BOLD).expect("bold font"),
            glyphs: HashMap::new(),
            adv: HashMap::new(),
        }
    }

    fn font(&self, f: Face) -> &FontRef<'static> {
        match f {
            Face::Regular => &self.regular,
            Face::Bold => &self.bold,
        }
    }

    pub fn ascent(&self, f: Face, px: u16) -> i32 {
        self.font(f).as_scaled(PxScale::from(px as f32)).ascent().round() as i32
    }

    pub fn line_h(&self, f: Face, px: u16) -> i32 {
        let s = self.font(f).as_scaled(PxScale::from(px as f32));
        (s.height() + s.line_gap()).round() as i32
    }

    fn gid(&self, f: Face, c: char) -> GlyphId {
        let font = self.font(f);
        let g = font.glyph_id(c);
        if g.0 == 0 && c != ' ' {
            // fall back: strip accents we do not have, else a middle dot
            return font.glyph_id('\u{00b7}');
        }
        g
    }

    pub fn advance(&mut self, f: Face, px: u16, c: char) -> f32 {
        if let Some(a) = self.adv.get(&(f, c, px)) {
            return *a;
        }
        let g = self.gid(f, c);
        let a = self.font(f).as_scaled(PxScale::from(px as f32)).h_advance(g);
        self.adv.insert((f, c, px), a);
        a
    }

    pub fn measure(&mut self, f: Face, px: u16, s: &str) -> i32 {
        let mut x = 0.0;
        for c in s.chars() {
            x += self.advance(f, px, c);
        }
        x.round() as i32
    }

    fn ensure(&mut self, f: Face, px: u16, g: GlyphId) {
        let key = (f, g.0, px);
        if self.glyphs.contains_key(&key) {
            return;
        }
        let font = self.font(f);
        let glyph = g.with_scale_and_position(PxScale::from(px as f32), ab_glyph::point(0.0, 0.0));
        let out = font.outline_glyph(glyph).map(|o| {
            let b = o.px_bounds();
            let (w, h) = (b.width() as i32, b.height() as i32);
            let mut mask = vec![0u8; (w.max(0) * h.max(0)) as usize];
            o.draw(|x, y, c| {
                let i = (y as i32 * w + x as i32) as usize;
                if i < mask.len() {
                    mask[i] = (c.clamp(0.0, 1.0) * 255.0 + 0.5) as u8;
                }
            });
            Glyph { left: b.min.x as i32, top: b.min.y as i32, w, h, mask }
        });
        self.glyphs.insert(key, out);
    }

    /// Draw `s` with its baseline at `y`; returns the x after the last glyph.
    pub fn draw(&mut self, cv: &mut Canvas, f: Face, px: u16, x: i32, y: i32, s: &str, color: u32) -> i32 {
        let mut pen = x as f32;
        let clip = cv.clip;
        // quick reject: whole line outside the clip vertically
        if y + px as i32 / 2 < clip.y || y - px as i32 * 3 / 2 > clip.y + clip.h {
            return x + self.measure(f, px, s);
        }
        for c in s.chars() {
            let g = self.gid(f, c);
            let adv = self.advance(f, px, c);
            if !c.is_whitespace() {
                let gx = pen.round() as i32;
                if gx < clip.x + clip.w && gx + px as i32 * 2 > clip.x {
                    self.ensure(f, px, g);
                    if let Some(Some(gl)) = self.glyphs.get(&(f, g.0, px)) {
                        cv.mask(gx + gl.left, y + gl.top, gl.w, gl.h, &gl.mask, color);
                    }
                }
            }
            pen += adv;
        }
        pen.round() as i32
    }

    /// Greedy word wrap to `max_w` pixels. Explicit newlines are kept; very long
    /// words are broken by character.
    pub fn wrap(&mut self, f: Face, px: u16, text: &str, max_w: i32) -> Vec<String> {
        let mut lines = Vec::new();
        for para in text.split('\n') {
            let mut line = String::new();
            let mut lw = 0.0f32;
            for word in para.split_inclusive(' ') {
                let ww: f32 = word.chars().map(|c| self.advance(f, px, c)).sum();
                let trimmed_w: f32 = word.trim_end().chars().map(|c| self.advance(f, px, c)).sum();
                if lw + trimmed_w <= max_w as f32 || line.is_empty() && trimmed_w <= max_w as f32 {
                    line.push_str(word);
                    lw += ww;
                    continue;
                }
                if !line.is_empty() {
                    lines.push(line.trim_end().to_string());
                    line = String::new();
                    lw = 0.0;
                }
                if trimmed_w <= max_w as f32 {
                    line.push_str(word);
                    lw = ww;
                } else {
                    for c in word.chars() {
                        let a = self.advance(f, px, c);
                        if lw + a > max_w as f32 && !line.is_empty() {
                            lines.push(std::mem::take(&mut line));
                            lw = 0.0;
                        }
                        line.push(c);
                        lw += a;
                    }
                }
            }
            lines.push(line.trim_end().to_string());
        }
        while lines.len() > 1 && lines.last().is_some_and(|l| l.is_empty()) {
            lines.pop();
        }
        lines
    }

    /// Cut `s` to fit `max_w`, ending in an ellipsis.
    pub fn ellipsize(&mut self, f: Face, px: u16, s: &str, max_w: i32) -> String {
        if self.measure(f, px, s) <= max_w {
            return s.to_string();
        }
        let ell = self.advance(f, px, '\u{2026}');
        let mut w = 0.0;
        let mut out = String::new();
        for c in s.chars() {
            let a = self.advance(f, px, c);
            if w + a + ell > max_w as f32 {
                break;
            }
            out.push(c);
            w += a;
        }
        out.push('\u{2026}');
        out
    }

    /// The tail of `s` that fits `max_w` (for a text field showing its end).
    pub fn tail(&mut self, f: Face, px: u16, s: &str, max_w: i32) -> String {
        let mut w = 0.0;
        let mut v: Vec<char> = Vec::new();
        for c in s.chars().rev() {
            let a = self.advance(f, px, c);
            if w + a > max_w as f32 {
                break;
            }
            v.push(c);
            w += a;
        }
        v.into_iter().rev().collect()
    }
}
