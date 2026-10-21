//! s22-develop: turn a raw Bayer dump from `s22-camera` into a photo.
//!
//! The S22 cameras run without the Pablo ISP, so every frame is raw 10-bit
//! Bayer. This tool is the software ISP:
//!   black level -> defect-pixel fix -> lens-shading (radial) -> grey-world AWB
//!   -> Malvar-He-Cutler demosaic -> colour matrix (stock setfile) -> auto
//!   exposure -> chroma denoise -> sRGB gamma + mild S-curve -> JPEG / PNG.
//!
//! Usage:
//!   s22-develop develop RAW [--out X.jpg] [--png X.png] [--preview X.png]
//!       [--preview-max 1016x1400] [--before X.png] [--bayer GBRG] [--black 64]
//!       [--white 1023] [--wb grayworld|none|R,B] [--ccm stock|none|9 floats]
//!       [--lsc 0.35] [--ev 0] [--half] [--no-denoise] [--no-auto-black] [--rotate 0|90|180|270] [--quality 90] [--stack]
//!   s22-develop meter RAW --exposure-us E --gain G [--max-gain 16]
//!       [--max-exposure-us 30000] [--target 0.16]
//! Geometry comes from the `RAW.json` sidecar (width, height, stride, bayer,
//! stack_scale); flags override it. The last stdout line is one JSON object.

use serde_json::{json, Value};
use std::time::Instant;

mod pipeline;
use pipeline::*;

fn arg<'a>(a: &'a [String], k: &str) -> Option<&'a str> {
    a.iter().position(|x| x == k).and_then(|i| a.get(i + 1)).map(|s| s.as_str())
}
fn flag(a: &[String], k: &str) -> bool {
    a.iter().any(|x| x == k)
}

fn load_meta(raw: &str, a: &[String]) -> Result<RawMeta, String> {
    let side: Value = std::fs::read_to_string(format!("{raw}.json")).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or(Value::Null);
    let num = |k: &str, d: f64| -> f64 { arg(a, &format!("--{k}")).and_then(|s| s.parse().ok()).or_else(|| side[k].as_f64()).unwrap_or(d) };
    let width = num("width", 2040.0) as usize;
    let height = num("height", 1532.0) as usize;
    let stride = num("stride", 4096.0) as usize;
    let bayer = arg(a, "--bayer").map(str::to_string).or_else(|| side["bayer"].as_str().map(str::to_string)).unwrap_or_else(|| "GBRG".into());
    let scale = side["stack_scale"].as_f64().unwrap_or(1.0) as f32;
    let black = arg(a, "--black").and_then(|s| s.parse().ok()).unwrap_or(64.0) * scale;
    let white = arg(a, "--white").and_then(|s| s.parse().ok()).unwrap_or(1023.0) * scale;
    let pattern = Pattern::parse(&bayer).ok_or(format!("bad bayer order {bayer}"))?;
    if stride < width * 2 {
        return Err(format!("stride {stride} < 2*width {width}"));
    }
    Ok(RawMeta { width, height, stride, pattern, black, white, sensor: side["sensor"].as_str().unwrap_or("?").to_string() })
}

fn parse_dims(s: &str) -> Option<(usize, usize)> {
    let (w, h) = s.split_once('x')?;
    Some((w.parse().ok()?, h.parse().ok()?))
}

fn run(a: &[String]) -> Result<Value, String> {
    let cmd = a.first().map(String::as_str).unwrap_or("");
    let raw = a.get(1).ok_or("usage: s22-develop develop|meter RAW ...")?;
    let meta = load_meta(raw, a)?;
    let mut mosaic = load_raw(raw, &meta)?;
    // --stack: average the burst RAW.0, RAW.1, ... written by `s22-camera capture --raw-burst`
    let mut stacked = 1usize;
    if flag(a, "--stack") {
        let mut acc: Vec<f32> = vec![0.0; mosaic.v.len()];
        let mut n = 0usize;
        for i in 0..32 {
            let p = format!("{raw}.{i}");
            if !std::path::Path::new(&p).exists() {
                break;
            }
            let m = load_raw(&p, &meta)?;
            for (x, v) in acc.iter_mut().zip(m.v.iter()) {
                *x += v;
            }
            n += 1;
        }
        if n > 1 {
            mosaic.v = acc.into_iter().map(|x| x / n as f32).collect();
            stacked = n;
        }
    }
    match cmd {
        "meter" => {
            let exp: f32 = arg(a, "--exposure-us").and_then(|s| s.parse().ok()).ok_or("--exposure-us needed")?;
            let gain: f32 = arg(a, "--gain").and_then(|s| s.parse().ok()).ok_or("--gain needed")?;
            let max_gain: f32 = arg(a, "--max-gain").and_then(|s| s.parse().ok()).unwrap_or(16.0);
            let max_exp: f32 = arg(a, "--max-exposure-us").and_then(|s| s.parse().ok()).unwrap_or(30000.0);
            let target: f32 = arg(a, "--target").and_then(|s| s.parse().ok()).unwrap_or(0.16);
            let m = meter(&mosaic, &meta, exp, gain, max_exp, max_gain, target);
            Ok(json!({"ok": true, "mean": m.mean, "p99": m.p99, "clipped": m.clipped, "factor": m.factor,
                      "exposure_us": m.exposure_us.round() as i64, "gain": (m.gain * 100.0).round() / 100.0}))
        }
        "develop" => {
            let t0 = Instant::now();
            let mut o = Opts::default();
            if let Some(w) = arg(a, "--wb") {
                o.wb = match w {
                    "grayworld" => Wb::GrayWorld,
                    "none" => Wb::Fixed(1.0, 1.0),
                    s => {
                        let v: Vec<f32> = s.split(',').filter_map(|x| x.parse().ok()).collect();
                        if v.len() != 2 {
                            return Err("--wb R,B".into());
                        }
                        Wb::Fixed(v[0], v[1])
                    }
                };
            }
            if let Some(c) = arg(a, "--ccm") {
                o.ccm = match c {
                    "stock" => STOCK_CCM,
                    "none" => IDENTITY,
                    s => {
                        let v: Vec<f32> = s.split(',').filter_map(|x| x.parse().ok()).collect();
                        if v.len() != 9 {
                            return Err("--ccm needs 9 comma-separated floats".into());
                        }
                        [v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7], v[8]]
                    }
                };
            }
            o.lsc = arg(a, "--lsc").and_then(|s| s.parse().ok()).unwrap_or(o.lsc);
            o.ev = arg(a, "--ev").and_then(|s| s.parse().ok()).unwrap_or(0.0);
            o.half = flag(a, "--half");
            o.denoise = !flag(a, "--no-denoise");
            o.auto_black = !flag(a, "--no-auto-black");
            let rot: u32 = arg(a, "--rotate").and_then(|s| s.parse().ok()).unwrap_or(0);
            let (img, info) = develop(&mosaic, &meta, &o);
            let img = rotate(img, rot);
            let mut out = json!({"ok": true, "stacked": stacked, "sensor": meta.sensor, "width": img.w, "height": img.h, "info": info});
            if let Some(p) = arg(a, "--out") {
                let q: u8 = arg(a, "--quality").and_then(|s| s.parse().ok()).unwrap_or(90);
                write_jpeg(p, &img, q)?;
                out["out"] = json!(p);
            }
            if let Some(p) = arg(a, "--png") {
                write_png(p, &img)?;
                out["png"] = json!(p);
            }
            if let Some(p) = arg(a, "--preview") {
                let (mw, mh) = arg(a, "--preview-max").and_then(parse_dims).unwrap_or((1016, 1400));
                let small = downscale_fit(&img, mw, mh);
                write_png(p, &small)?;
                out["preview"] = json!({"path": p, "width": small.w, "height": small.h});
            }
            if let Some(p) = arg(a, "--before") {
                let b = rotate(naive(&mosaic, &meta), rot);
                write_png(p, &b)?;
                out["before"] = json!(p);
            }
            out["ms"] = json!(t0.elapsed().as_millis() as u64);
            Ok(out)
        }
        _ => Err("usage: s22-develop develop|meter RAW ...".into()),
    }
}

fn main() {
    let a: Vec<String> = std::env::args().skip(1).collect();
    match run(&a) {
        Ok(v) => println!("{v}"),
        Err(e) => {
            println!("{}", json!({"ok": false, "error": e}));
            std::process::exit(2);
        }
    }
}
