//! Minimal MJPEG-in-AVI writer (RIFF AVI 1.0 with an idx1 index), for video
//! recording from the live stream when no H.264 encoder exists on the phone.
//! Frame rate and frame count are patched into the headers when the file is
//! finished, so the file plays at the rate that was really recorded.

use std::io::{Seek, SeekFrom, Write};

pub struct AviWriter<W: Write + Seek> {
    out: W,
    w: u32,
    h: u32,
    frames: u32,
    max_frame: u32,
    index: Vec<(u32, u32)>, // (offset from 'movi' fourcc, size)
    movi_start: u64,        // file offset of the 'movi' fourcc
    pos: u64,
}

// header offsets patched at the end
const RIFF_SIZE: u64 = 4;
const AVIH_USEC: u64 = 32;
const AVIH_MAXBPS: u64 = 36;
const AVIH_FRAMES: u64 = 48;
const AVIH_SUGBUF: u64 = 60;
const STRH_SCALE: u64 = 128;
const STRH_RATE: u64 = 132;
const STRH_LENGTH: u64 = 140;
const STRH_SUGBUF: u64 = 144;
const MOVI_SIZE: u64 = 216;
pub const HEADER_LEN: u64 = 224;

fn le(v: u32) -> [u8; 4] {
    v.to_le_bytes()
}

/// The fixed 224-byte header (RIFF, hdrl with avih + one strl, LIST movi start).
pub fn header(w: u32, h: u32, fps_num: u32, fps_den: u32) -> Vec<u8> {
    let mut b: Vec<u8> = Vec::with_capacity(HEADER_LEN as usize);
    b.extend(b"RIFF");
    b.extend(le(0)); // riff size, patched
    b.extend(b"AVI ");
    b.extend(b"LIST");
    b.extend(le(192)); // hdrl list size: 4 + (8+56) + (8+116)
    b.extend(b"hdrl");
    b.extend(b"avih");
    b.extend(le(56));
    let usec = (1_000_000u64 * fps_den as u64 / fps_num.max(1) as u64) as u32;
    b.extend(le(usec)); // dwMicroSecPerFrame @32
    b.extend(le(0)); // dwMaxBytesPerSec @36
    b.extend(le(0)); // padding granularity
    b.extend(le(0x10)); // AVIF_HASINDEX
    b.extend(le(0)); // total frames @48
    b.extend(le(0)); // initial frames
    b.extend(le(1)); // streams
    b.extend(le(0)); // suggested buffer @60
    b.extend(le(w));
    b.extend(le(h));
    b.extend([0u8; 16]);
    b.extend(b"LIST");
    b.extend(le(116)); // strl: 4 + (8+56) + (8+40)
    b.extend(b"strl");
    b.extend(b"strh");
    b.extend(le(56));
    b.extend(b"vids");
    b.extend(b"MJPG");
    b.extend(le(0)); // flags
    b.extend(le(0)); // priority + language
    b.extend(le(0)); // initial frames
    b.extend(le(fps_den)); // scale @128
    b.extend(le(fps_num)); // rate @132
    b.extend(le(0)); // start
    b.extend(le(0)); // length @140
    b.extend(le(0)); // suggested buffer @144
    b.extend(le(u32::MAX)); // quality
    b.extend(le(0)); // sample size
    b.extend(0u16.to_le_bytes());
    b.extend(0u16.to_le_bytes());
    b.extend((w as u16).to_le_bytes());
    b.extend((h as u16).to_le_bytes());
    b.extend(b"strf");
    b.extend(le(40));
    b.extend(le(40)); // biSize
    b.extend(le(w));
    b.extend(le(h));
    b.extend(1u16.to_le_bytes()); // planes
    b.extend(24u16.to_le_bytes()); // bit count
    b.extend(b"MJPG");
    b.extend(le(w * h * 3));
    b.extend([0u8; 16]);
    b.extend(b"LIST");
    b.extend(le(0)); // movi size @216, patched
    b.extend(b"movi");
    debug_assert_eq!(b.len() as u64, HEADER_LEN);
    b
}

impl<W: Write + Seek> AviWriter<W> {
    pub fn new(mut out: W, w: u32, h: u32) -> std::io::Result<Self> {
        out.write_all(&header(w, h, 30, 1))?;
        Ok(AviWriter { out, w, h, frames: 0, max_frame: 0, index: vec![], movi_start: HEADER_LEN - 4, pos: HEADER_LEN })
    }

    pub fn frame(&mut self, jpeg: &[u8]) -> std::io::Result<()> {
        let off = (self.pos - self.movi_start) as u32;
        self.out.write_all(b"00dc")?;
        self.out.write_all(&le(jpeg.len() as u32))?;
        self.out.write_all(jpeg)?;
        let mut n = 8 + jpeg.len() as u64;
        if jpeg.len() % 2 == 1 {
            self.out.write_all(&[0])?;
            n += 1;
        }
        self.index.push((off, jpeg.len() as u32));
        self.pos += n;
        self.frames += 1;
        self.max_frame = self.max_frame.max(jpeg.len() as u32);
        Ok(())
    }

    /// Write the index and patch the headers; `seconds` is the recorded span (sets the frame rate).
    pub fn finish(mut self, seconds: f64) -> std::io::Result<W> {
        let movi_size = (self.pos - self.movi_start) as u32;
        self.out.write_all(b"idx1")?;
        self.out.write_all(&le(self.index.len() as u32 * 16))?;
        for (off, size) in &self.index {
            self.out.write_all(b"00dc")?;
            self.out.write_all(&le(0x10))?; // AVIIF_KEYFRAME
            self.out.write_all(&le(*off))?;
            self.out.write_all(&le(*size))?;
        }
        let end = self.pos + 8 + self.index.len() as u64 * 16;
        // rate = frames / seconds, as rate/scale with scale 1000
        let fps = if seconds > 0.1 && self.frames > 1 { self.frames as f64 / seconds } else { 30.0 };
        let (rate, scale) = ((fps * 1000.0).round().max(1.0) as u32, 1000u32);
        let patch = |o: &mut W, at: u64, v: u32| -> std::io::Result<()> {
            o.seek(SeekFrom::Start(at))?;
            o.write_all(&le(v))
        };
        patch(&mut self.out, RIFF_SIZE, (end - 8) as u32)?;
        patch(&mut self.out, AVIH_USEC, (1e6 / fps).round() as u32)?;
        patch(&mut self.out, AVIH_MAXBPS, (self.max_frame as f64 * fps) as u32)?;
        patch(&mut self.out, AVIH_FRAMES, self.frames)?;
        patch(&mut self.out, AVIH_SUGBUF, self.max_frame + 8)?;
        patch(&mut self.out, STRH_SCALE, scale)?;
        patch(&mut self.out, STRH_RATE, rate)?;
        patch(&mut self.out, STRH_LENGTH, self.frames)?;
        patch(&mut self.out, STRH_SUGBUF, self.max_frame + 8)?;
        patch(&mut self.out, MOVI_SIZE, movi_size)?;
        self.out.seek(SeekFrom::Start(end))?;
        self.out.flush()?;
        let _ = (self.w, self.h);
        Ok(self.out)
    }

    pub fn frames(&self) -> u32 {
        self.frames
    }
}

/// XRGB (w x h) -> rotated/mirrored RGB24 at half size (2x2 box average).
/// `rot` is clockwise degrees (0/90/180/270); the output is (ow, oh).
pub fn half_rgb(src: &[u32], w: usize, h: usize, rot: u32, mirror: bool) -> (Vec<u8>, usize, usize) {
    let (hw, hh) = (w / 2 & !1, h / 2 & !1);
    let (ow, oh) = if rot % 180 == 90 { (hh, hw) } else { (hw, hh) };
    let mut out = vec![0u8; ow * oh * 3];
    for y in 0..hh {
        for x in 0..hw {
            let (a, b) = (src[2 * y * w + 2 * x], src[2 * y * w + 2 * x + 1]);
            let (c, d) = (src[(2 * y + 1) * w + 2 * x], src[(2 * y + 1) * w + 2 * x + 1]);
            let ch = |s: u32| -> u8 {
                (((a >> s) & 255) + ((b >> s) & 255) + ((c >> s) & 255) + ((d >> s) & 255)).div_ceil(4).min(255) as u8
            };
            // source (x, y) in the half image -> output position
            let (mut ox, oy) = match rot % 360 {
                90 => (hh - 1 - y, x),
                180 => (hw - 1 - x, hh - 1 - y),
                270 => (y, hw - 1 - x),
                _ => (x, y),
            };
            if mirror {
                ox = ow - 1 - ox;
            }
            let o = (oy * ow + ox) * 3;
            out[o] = ch(16);
            out[o + 1] = ch(8);
            out[o + 2] = ch(0);
        }
    }
    (out, ow, oh)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn avi_layout_and_patched_headers() {
        let h = header(382, 510, 30, 1);
        assert_eq!(h.len() as u64, HEADER_LEN);
        assert_eq!(&h[0..4], b"RIFF");
        assert_eq!(&h[212..216], b"LIST");
        assert_eq!(&h[220..224], b"movi");
        assert_eq!(&h[108..112], b"vids");
        assert_eq!(&h[112..116], b"MJPG");
        let mut w = AviWriter::new(Cursor::new(Vec::new()), 382, 510).unwrap();
        w.frame(&[0xff, 0xd8, 1, 0xff, 0xd9]).unwrap(); // odd: padded
        w.frame(&[0xff, 0xd8, 0xff, 0xd9]).unwrap();
        let v = w.finish(2.0 / 30.0 * 1.0 + 0.0).unwrap().into_inner();
        let u = |o: usize| u32::from_le_bytes(v[o..o + 4].try_into().unwrap());
        assert_eq!(u(4) as usize, v.len() - 8, "RIFF size");
        assert_eq!(u(48), 2, "avih frames");
        assert_eq!(u(140), 2, "strh length");
        let movi = u(216) as usize;
        assert_eq!(&v[224 + movi - 4..224 + movi], b"idx1", "movi size ends at idx1");
        assert_eq!(&v[224..228], b"00dc");
        // the index points at the chunks relative to 'movi'
        let idx = 224 + movi - 4;
        assert_eq!(u(idx + 4), 32);
        assert_eq!(u(idx + 8 + 8), 4, "first frame offset from 'movi'");
        assert_eq!(u(idx + 8 + 12), 5);
        assert_eq!(u(idx + 24 + 8), 4 + 8 + 6, "second frame after the padded first");
    }

    #[test]
    fn half_rgb_rotation_and_mirror() {
        // 4x2 XRGB: left half red, right half blue -> half 2x1: [red, blue]
        let r = 0x00ff0000u32;
        let b = 0x000000ffu32;
        let src = vec![r, r, b, b, r, r, b, b];
        let (o, w, h) = half_rgb(&src, 4, 2, 0, false);
        // 2x1 rounds height to 0 (even sizes only); use 8x4 instead
        assert_eq!((w, h), (2, 0));
        let _ = o;
        let mut src = vec![0u32; 8 * 4];
        for y in 0..4 {
            for x in 0..8 {
                src[y * 8 + x] = if x < 4 { if y < 2 { r } else { 0x0000ff00 } } else { b };
            }
        }
        // half: 4x2, top-left red, bottom-left green, right blue
        let (o, w, h) = half_rgb(&src, 8, 4, 0, false);
        assert_eq!((w, h), (4, 2));
        assert_eq!(&o[0..3], &[255, 0, 0]);
        assert_eq!(&o[(4 + 0) * 3..(4 + 0) * 3 + 3], &[0, 255, 0]);
        // 90 clockwise: output 2x4; the source's top-left (red) goes to the top-right
        let (o, w, h) = half_rgb(&src, 8, 4, 90, false);
        assert_eq!((w, h), (2, 4));
        assert_eq!(&o[1 * 3..1 * 3 + 3], &[255, 0, 0], "top-right red");
        assert_eq!(&o[0..3], &[0, 255, 0], "top-left green");
        assert_eq!(&o[(3 * 2) * 3..(3 * 2) * 3 + 3], &[0, 0, 255], "bottom blue");
        // mirrored
        let (o, _, _) = half_rgb(&src, 8, 4, 90, true);
        assert_eq!(&o[0..3], &[255, 0, 0]);
    }
}
