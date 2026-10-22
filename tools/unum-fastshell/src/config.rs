//! Device configuration and hardware auto-detection, so the shell can run on
//! phones other than the Galaxy S22.
//!
//! Precedence for every setting: the existing environment variable (if the
//! setting has one) > the JSON config file > the built-in default. The built-in
//! defaults are the S22 values, and every "auto" detector tries the S22 value
//! first, so an S22 without a config file behaves exactly as before.
//!
//! Config file: `$FASTSHELL_CONFIG`, else the first of
//! `/srv/s22/state/fastshell/config.json`, `/etc/unum-fastshell.json` that
//! exists. Unknown keys are ignored; missing keys keep their default. See
//! PORTING.md for the reference.
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

#[derive(Debug, Clone, PartialEq)]
pub struct Config {
    /// DRM card node, or "auto" (first card with a connected connector).
    pub drm_card: String,
    /// evdev touchscreen node, or "auto" (by name, then by capabilities).
    pub touch_device: String,
    /// Preferred touchscreen name for "auto" (the S22's is sec_touchscreen).
    pub touch_name: String,
    pub touch_swap_xy: bool,
    pub touch_invert_x: bool,
    pub touch_invert_y: bool,
    /// power_supply directory, or "auto" (power_supply/battery, then type=Battery).
    pub battery: String,
    /// backlight directory, or "auto" (backlight/panel, then the first one).
    pub backlight: String,
    /// thermal zone `type`s shown in the status line; if none exists, zones
    /// whose type mentions cpu/gpu/soc/big/little are used instead.
    pub thermal_types: Vec<String>,
    pub net_wifi: Vec<String>,
    pub net_usb: Vec<String>,
    pub net_cell: Vec<String>,
    pub net_vpn: String,
    /// Root of the sway/desktop chroot; "" = sway (if any) runs on the host.
    pub chroot: String,
    /// seatd socket; "" = `<chroot>/run/seatd.sock`.
    pub seatd_socket: String,
    /// s22-touchd run dir (shell.sock, ctl.sock, confirm/); "" = `<chroot>/run/s22-touch`.
    pub run_dir: String,
    pub state_dir: String,
    pub pid_file: String,
    pub display_state_file: String,
    pub say_lock: String,
    pub api: String,
    pub s22d: String,
    pub phoned: String,
    pub session: String,
    pub assistant: String,
    pub assistant_log: String,
    pub ui_backend_file: String,
    pub files_dir: String,
    pub tz: String,
    /// POSIX TZ rule used when zoneinfo for `tz` is nowhere to be found.
    pub tz_fallback: String,
}

impl Default for Config {
    fn default() -> Self {
        let s = |v: &[&str]| v.iter().map(|x| x.to_string()).collect();
        Config {
            drm_card: "auto".into(),
            touch_device: "auto".into(),
            touch_name: "sec_touchscreen".into(),
            touch_swap_xy: false,
            touch_invert_x: false,
            touch_invert_y: false,
            battery: "auto".into(),
            backlight: "auto".into(),
            thermal_types: s(&["BIG", "MID", "LITTLE", "G3D"]),
            net_wifi: s(&["wlan0"]),
            net_usb: s(&["ecm0"]),
            net_cell: s(&["rmnet0"]),
            net_vpn: "tailscale0".into(),
            chroot: "/mnt/omarchy-trial".into(),
            seatd_socket: String::new(),
            run_dir: String::new(),
            state_dir: "/srv/s22/state/fastshell".into(),
            pid_file: "/run/s22-fastshell.pid".into(),
            display_state_file: "/run/s22-display-state".into(),
            say_lock: "/run/s22-say.lock".into(),
            api: "http://127.0.0.1:18880".into(),
            s22d: "http://127.0.0.1:8766".into(),
            phoned: "http://127.0.0.1:8095".into(),
            session: "touch".into(),
            assistant: "/srv/s22/hardware/bin/s22-assistant".into(),
            assistant_log: "/srv/s22/buttons/assistant.jsonl".into(),
            ui_backend_file: "/srv/s22/state/touchui/ui-backend".into(),
            files_dir: "/root".into(),
            tz: "Europe/Athens".into(),
            tz_fallback: "EET-2EEST,M3.5.0/3,M10.5.0/4".into(),
        }
    }
}

const DEFAULT_PATHS: [&str; 2] = ["/srv/s22/state/fastshell/config.json", "/etc/unum-fastshell.json"];

impl Config {
    /// Overlay a JSON object on the defaults.
    pub fn from_json(v: &Value) -> Config {
        let mut c = Config::default();
        let st = |k: &str, d: &mut String| {
            if let Some(x) = v.get(k).and_then(Value::as_str) {
                *d = x.to_string();
            }
        };
        let bl = |k: &str, d: &mut bool| {
            if let Some(x) = v.get(k).and_then(Value::as_bool) {
                *d = x;
            }
        };
        let ls = |k: &str, d: &mut Vec<String>| match v.get(k) {
            Some(Value::Array(a)) => *d = a.iter().filter_map(Value::as_str).map(String::from).collect(),
            Some(Value::String(x)) => *d = vec![x.clone()],
            _ => {}
        };
        st("drm_card", &mut c.drm_card);
        st("touch_device", &mut c.touch_device);
        st("touch_name", &mut c.touch_name);
        bl("touch_swap_xy", &mut c.touch_swap_xy);
        bl("touch_invert_x", &mut c.touch_invert_x);
        bl("touch_invert_y", &mut c.touch_invert_y);
        st("battery", &mut c.battery);
        st("backlight", &mut c.backlight);
        ls("thermal_types", &mut c.thermal_types);
        ls("net_wifi", &mut c.net_wifi);
        ls("net_usb", &mut c.net_usb);
        ls("net_cell", &mut c.net_cell);
        st("net_vpn", &mut c.net_vpn);
        st("chroot", &mut c.chroot);
        st("seatd_socket", &mut c.seatd_socket);
        st("run_dir", &mut c.run_dir);
        st("state_dir", &mut c.state_dir);
        st("pid_file", &mut c.pid_file);
        st("display_state_file", &mut c.display_state_file);
        st("say_lock", &mut c.say_lock);
        st("api", &mut c.api);
        st("s22d", &mut c.s22d);
        st("phoned", &mut c.phoned);
        st("session", &mut c.session);
        st("assistant", &mut c.assistant);
        st("assistant_log", &mut c.assistant_log);
        st("ui_backend_file", &mut c.ui_backend_file);
        st("files_dir", &mut c.files_dir);
        st("tz", &mut c.tz);
        st("tz_fallback", &mut c.tz_fallback);
        c
    }

    /// The pre-existing environment variables keep precedence over the file.
    fn apply_env(&mut self) {
        let ov = |k: &str, d: &mut String| {
            if let Ok(x) = std::env::var(k) {
                *d = x;
            }
        };
        ov("FASTSHELL_CHROOT", &mut self.chroot);
        ov("S22_TOUCH_RUN", &mut self.run_dir);
        ov("FASTSHELL_STATE", &mut self.state_dir);
        ov("FASTSHELL_SAY_LOCK", &mut self.say_lock);
        ov("FASTSHELL_ASSISTANT", &mut self.assistant);
        ov("S22_SHELL_API", &mut self.api);
        ov("S22_SHELL_S22D", &mut self.s22d);
        ov("S22_SHELL_PHONED", &mut self.phoned);
        ov("S22_SHELL_SESSION", &mut self.session);
        ov("TZ", &mut self.tz);
        ov("FASTSHELL_DRM_CARD", &mut self.drm_card);
        ov("FASTSHELL_TOUCH", &mut self.touch_device);
    }

    /// Load the config file (if any) and the environment. Returns the config
    /// and the file it came from, or a parse error message.
    pub fn load() -> (Config, Option<String>, Option<String>) {
        let path = std::env::var("FASTSHELL_CONFIG").ok().or_else(|| DEFAULT_PATHS.iter().find(|p| Path::new(p).exists()).map(|p| p.to_string()));
        let (mut c, err) = match &path {
            Some(p) => match std::fs::read_to_string(p).map_err(|e| e.to_string()).and_then(|s| serde_json::from_str::<Value>(&s).map_err(|e| e.to_string())) {
                Ok(v) => (Config::from_json(&v), None),
                Err(e) => (Config::default(), Some(format!("{p}: {e} (using defaults)"))),
            },
            None => (Config::default(), None),
        };
        c.apply_env();
        (c, path, err)
    }

    pub fn seatd_socket(&self) -> String {
        if self.seatd_socket.is_empty() {
            format!("{}/run/seatd.sock", self.chroot)
        } else {
            self.seatd_socket.clone()
        }
    }

    pub fn run_dir(&self) -> String {
        if self.run_dir.is_empty() {
            format!("{}/run/s22-touch", self.chroot)
        } else {
            self.run_dir.clone()
        }
    }

    pub fn touch_map(&self) -> TouchMap {
        TouchMap { swap_xy: self.touch_swap_xy, invert_x: self.touch_invert_x, invert_y: self.touch_invert_y, ..TouchMap::default() }
    }
}

static CFG: OnceLock<Config> = OnceLock::new();

/// Install the runtime config (once, at start-up).
pub fn init(c: Config) {
    let _ = CFG.set(c);
}

/// The runtime config; the S22 defaults if `init` was never called (tests, dump, bench).
pub fn get() -> &'static Config {
    CFG.get_or_init(Config::default)
}

// ---------------------------------------------------------------- detection

fn rd(p: &Path) -> String {
    std::fs::read_to_string(p).map(|s| s.trim().to_string()).unwrap_or_default()
}

fn entries(dir: &Path) -> Vec<String> {
    let mut v: Vec<String> = std::fs::read_dir(dir).map(|r| r.flatten().map(|e| e.file_name().to_string_lossy().to_string()).collect()).unwrap_or_default();
    v.sort();
    v
}

/// Natural order: "event2" before "event10".
fn sorted_entries(dir: &Path) -> Vec<String> {
    let mut v = entries(dir);
    v.sort_by(|a, b| natural_key(a).cmp(&natural_key(b)));
    v
}

/// "event10" after "event9", "card1" before "card10".
fn natural_key(s: &str) -> (String, u64, String) {
    let i = s.find(|c: char| c.is_ascii_digit()).unwrap_or(s.len());
    let j = s[i..].find(|c: char| !c.is_ascii_digit()).map(|j| i + j).unwrap_or(s.len());
    (s[..i].to_string(), s[i..j].parse().unwrap_or(0), s[j..].to_string())
}

/// DRM card: the configured node if it is not "auto"; else the first
/// `cardN` (in sysfs order) that has a connector reporting "connected"
/// (S22: card0 is the sgpu GPU with no connectors, card1-DSI-1 is the panel).
pub fn detect_drm_card(sys: &Path, dev: &Path, want: &str) -> Option<String> {
    if want != "auto" && !want.is_empty() {
        return Some(want.to_string());
    }
    let ents = sorted_entries(sys);
    let cards: Vec<&String> = ents.iter().filter(|n| n.starts_with("card") && n[4..].chars().all(|c| c.is_ascii_digit()) && n.len() > 4).collect();
    for c in &cards {
        let prefix = format!("{c}-");
        if ents.iter().any(|n| n.starts_with(&prefix) && rd(&sys.join(n).join("status")) == "connected") {
            return Some(dev.join(c.as_str()).to_string_lossy().into_owned());
        }
    }
    None
}

const ABS_MT_POSITION_X: usize = 0x35;
const ABS_MT_POSITION_Y: usize = 0x36;
const BTN_TOUCH: usize = 0x14a;
const INPUT_PROP_DIRECT: usize = 0x01;

/// Parse a sysfs capability bitmap ("abs", "key", "properties"): space
/// separated hex words, most significant first, each `long` wide.
fn has_bit(bitmap: &str, bit: usize, word_bits: usize) -> bool {
    let words: Vec<&str> = bitmap.split_whitespace().collect();
    let idx = bit / word_bits;
    if idx >= words.len() {
        return false;
    }
    let w = words[words.len() - 1 - idx];
    u128::from_str_radix(w, 16).map(|v| v >> (bit % word_bits) & 1 == 1).unwrap_or(false)
}

/// Does this input device look like a touchscreen? Needs ABS_MT_POSITION_X/Y;
/// INPUT_PROP_DIRECT (or BTN_TOUCH when properties are absent) rules out touchpads.
fn is_touchscreen(dev: &Path, word_bits: usize) -> bool {
    let abs = rd(&dev.join("capabilities/abs"));
    if !(has_bit(&abs, ABS_MT_POSITION_X, word_bits) && has_bit(&abs, ABS_MT_POSITION_Y, word_bits)) {
        return false;
    }
    let props = rd(&dev.join("properties"));
    if !props.is_empty() {
        return has_bit(&props, INPUT_PROP_DIRECT, word_bits);
    }
    has_bit(&rd(&dev.join("capabilities/key")), BTN_TOUCH, word_bits)
}

/// Touchscreen evdev node: the configured node if not "auto"; else the first
/// `eventN` whose device name is `name` (the S22 path, as before); else the
/// first `eventN` with multitouch position axes and INPUT_PROP_DIRECT.
pub fn detect_touchscreen(sys_input: &Path, dev_input: &Path, want: &str, name: &str) -> Option<String> {
    if want != "auto" && !want.is_empty() {
        return Some(want.to_string());
    }
    let node = |n: &str| dev_input.join(n).to_string_lossy().into_owned();
    if !name.is_empty() {
        // plain string order, as the S22-only code did
        let evs: Vec<String> = entries(sys_input).into_iter().filter(|n| n.starts_with("event")).collect();
        if let Some(n) = evs.iter().find(|n| rd(&sys_input.join(n).join("device/name")) == name) {
            return Some(node(n));
        }
    }
    let evs: Vec<String> = sorted_entries(sys_input).into_iter().filter(|n| n.starts_with("event")).collect();
    let wb = usize::BITS as usize;
    evs.iter().find(|n| is_touchscreen(&sys_input.join(n).join("device"), wb)).map(|n| node(n))
}

/// Battery power_supply directory: configured, else `battery` (S22), else the
/// first supply with `type` = Battery.
pub fn detect_battery(sys_ps: &Path, want: &str) -> Option<PathBuf> {
    if want != "auto" && !want.is_empty() {
        return Some(PathBuf::from(want));
    }
    let b = sys_ps.join("battery");
    if b.join("capacity").exists() {
        return Some(b);
    }
    sorted_entries(sys_ps).into_iter().map(|n| sys_ps.join(n)).find(|p| rd(&p.join("type")) == "Battery" && p.join("capacity").exists())
}

/// Backlight directory: configured, else `panel` (S22), else the first one.
pub fn detect_backlight(sys_bl: &Path, want: &str) -> Option<PathBuf> {
    if want != "auto" && !want.is_empty() {
        return Some(PathBuf::from(want));
    }
    let p = sys_bl.join("panel");
    if p.join("brightness").exists() {
        return Some(p);
    }
    sorted_entries(sys_bl).into_iter().map(|n| sys_bl.join(n)).find(|p| p.join("max_brightness").exists())
}

/// Thermal zones to show, as (type, temp file): the zones whose type is in
/// `types` (S22: BIG/MID/LITTLE/G3D) in zone order; if none matches, up to
/// four zones whose type names a CPU/GPU/SoC sensor. Plain string order of
/// the zone directories, as the S22-only code did.
pub fn detect_thermal(sys_th: &Path, types: &[String]) -> Vec<(String, PathBuf)> {
    let zones: Vec<(String, PathBuf)> = entries(sys_th)
        .into_iter()
        .filter(|n| n.starts_with("thermal_zone"))
        .map(|n| (rd(&sys_th.join(&n).join("type")), sys_th.join(n).join("temp")))
        .collect();
    let picked: Vec<_> = zones.iter().filter(|(t, _)| types.iter().any(|x| x == t)).cloned().collect();
    if !picked.is_empty() {
        return picked;
    }
    let hint = |t: &str| {
        let l = t.to_ascii_lowercase();
        ["cpu", "gpu", "soc", "big", "little", "cluster"].iter().any(|h| l.contains(h))
    };
    zones.into_iter().filter(|(t, _)| hint(t)).take(4).collect()
}

/// Detected sysfs sources, resolved once at start-up.
#[derive(Debug, Clone, Default)]
pub struct Sys {
    pub battery: Option<PathBuf>,
    pub backlight: Option<PathBuf>,
    pub thermal: Vec<(String, PathBuf)>,
}

static SYS: OnceLock<Sys> = OnceLock::new();

pub fn sys() -> &'static Sys {
    SYS.get_or_init(|| {
        let c = get();
        let th = detect_thermal(Path::new("/sys/class/thermal"), &c.thermal_types);
        Sys {
            battery: detect_battery(Path::new("/sys/class/power_supply"), &c.battery),
            backlight: detect_backlight(Path::new("/sys/class/backlight"), &c.backlight),
            thermal: th,
        }
    })
}

// ---------------------------------------------------------------- touch map

/// Optional transform between the touch digitiser and the shell canvas:
/// rotation/inversion from the config, and an offset/scale when the panel is
/// not 1080x2340 (the canvas is centred on it). Identity on the S22.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TouchMap {
    pub swap_xy: bool,
    pub invert_x: bool,
    pub invert_y: bool,
    /// canvas size the digitiser is scaled to (TouchDev::open w/h)
    pub cw: i32,
    pub ch: i32,
    /// panel size and canvas offset on it
    pub pw: i32,
    pub ph: i32,
    pub ox: i32,
    pub oy: i32,
}

impl Default for TouchMap {
    fn default() -> Self {
        let (w, h) = (crate::ui::W, crate::ui::H);
        TouchMap { swap_xy: false, invert_x: false, invert_y: false, cw: w, ch: h, pw: w, ph: h, ox: 0, oy: 0 }
    }
}

impl TouchMap {
    pub fn is_identity(&self) -> bool {
        !self.swap_xy && !self.invert_x && !self.invert_y && self.pw == self.cw && self.ph == self.ch && self.ox == 0 && self.oy == 0
    }

    /// Canvas offset of a W x H canvas centred on a pw x ph panel (negative = cropped).
    pub fn with_panel(mut self, pw: i32, ph: i32) -> TouchMap {
        self.pw = pw;
        self.ph = ph;
        self.ox = (pw - self.cw) / 2;
        self.oy = (ph - self.ch) / 2;
        self
    }

    pub fn point(&self, x: i32, y: i32) -> (i32, i32) {
        let (mut x, mut y) = (x, y);
        if self.swap_xy {
            // digitiser axes were scaled to (cw, ch); re-scale the swapped pair
            let nx = (y as i64 * self.cw as i64 / self.ch.max(1) as i64) as i32;
            let ny = (x as i64 * self.ch as i64 / self.cw.max(1) as i64) as i32;
            x = nx;
            y = ny;
        }
        if self.invert_x {
            x = self.cw - 1 - x;
        }
        if self.invert_y {
            y = self.ch - 1 - y;
        }
        if self.pw != self.cw || self.ph != self.ch {
            x = (x as i64 * self.pw as i64 / self.cw as i64) as i32 - self.ox;
            y = (y as i64 * self.ph as i64 / self.ch as i64) as i32 - self.oy;
        }
        (x, y)
    }

    pub fn apply(&self, e: crate::touch::Touch) -> crate::touch::Touch {
        use crate::touch::Touch;
        if self.is_identity() {
            return e;
        }
        match e {
            Touch::Down(x, y, t) => {
                let (x, y) = self.point(x, y);
                Touch::Down(x, y, t)
            }
            Touch::Move(x, y, t) => {
                let (x, y) = self.point(x, y);
                Touch::Move(x, y, t)
            }
            Touch::Up(x, y, t) => {
                let (x, y) = self.point(x, y);
                Touch::Up(x, y, t)
            }
            other => other,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    struct Tmp(PathBuf);
    impl Tmp {
        fn new(tag: &str) -> Tmp {
            let p = std::env::temp_dir().join(format!("fastshell-cfg-{tag}-{}", std::process::id()));
            let _ = fs::remove_dir_all(&p);
            fs::create_dir_all(&p).unwrap();
            Tmp(p)
        }
        fn put(&self, rel: &str, v: &str) {
            let p = self.0.join(rel);
            fs::create_dir_all(p.parent().unwrap()).unwrap();
            fs::write(p, format!("{v}\n")).unwrap();
        }
    }
    impl Drop for Tmp {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn defaults_are_the_s22_values() {
        let c = Config::default();
        assert_eq!(c.chroot, "/mnt/omarchy-trial");
        assert_eq!(c.seatd_socket(), "/mnt/omarchy-trial/run/seatd.sock");
        assert_eq!(c.touch_name, "sec_touchscreen");
        assert_eq!(c.api, "http://127.0.0.1:18880");
        assert!(c.touch_map().is_identity());
        // an empty file changes nothing
        assert_eq!(Config::from_json(&serde_json::json!({})), c);
    }

    #[test]
    fn json_overlay() {
        let c = Config::from_json(&serde_json::json!({"chroot": "", "drm_card": "/dev/dri/card0", "net_wifi": "wlan1", "thermal_types": ["cpu0-thermal"], "touch_invert_y": true, "bogus": 1}));
        assert_eq!(c.chroot, "");
        assert_eq!(c.seatd_socket(), "/run/seatd.sock");
        assert_eq!(c.drm_card, "/dev/dri/card0");
        assert_eq!(c.net_wifi, vec!["wlan1".to_string()]);
        assert_eq!(c.thermal_types, vec!["cpu0-thermal".to_string()]);
        assert!(c.touch_invert_y);
        assert_eq!(c.state_dir, "/srv/s22/state/fastshell");
    }

    #[test]
    fn drm_card_s22_layout() {
        let t = Tmp::new("drm");
        // S22: card0 = sgpu (no connectors), card1-DSI-1 connected
        t.put("sys/card0/dev", "226:0");
        t.put("sys/card1/dev", "226:1");
        t.put("sys/card1-DSI-1/status", "connected");
        t.put("sys/renderD128/dev", "226:128");
        let dev = t.0.join("dev");
        assert_eq!(detect_drm_card(&t.0.join("sys"), &dev, "auto").unwrap(), dev.join("card1").to_string_lossy());
        assert_eq!(detect_drm_card(&t.0.join("sys"), &dev, "/dev/dri/card7").unwrap(), "/dev/dri/card7");
    }

    #[test]
    fn drm_card_skips_disconnected() {
        let t = Tmp::new("drm2");
        t.put("sys/card0-HDMI-A-1/status", "disconnected");
        t.put("sys/card0/dev", "");
        t.put("sys/card2/dev", "");
        t.put("sys/card2-DSI-1/status", "connected");
        let dev = t.0.join("dev");
        assert_eq!(detect_drm_card(&t.0.join("sys"), &dev, "auto").unwrap(), dev.join("card2").to_string_lossy());
        let e = Tmp::new("drm3");
        assert!(detect_drm_card(&e.0, &dev, "auto").is_none());
    }

    #[test]
    fn touchscreen_by_name_then_caps() {
        let t = Tmp::new("touch");
        let si = t.0.join("input");
        let di = t.0.join("devinput");
        // event0: power key (no abs); event2: touchpad (MT but no DIRECT);
        // event10: touchscreen by caps only
        t.put("input/event0/device/name", "gpio_keys");
        t.put("input/event0/device/capabilities/abs", "0");
        t.put("input/event2/device/name", "synaptics touchpad");
        t.put("input/event2/device/capabilities/abs", "660800000000003");
        t.put("input/event2/device/properties", "5"); // POINTER | BUTTONPAD
        t.put("input/event10/device/name", "goodix-ts");
        t.put("input/event10/device/capabilities/abs", "660800000000000"); // MT_SLOT, POS_X/Y, TRACKING_ID...
        t.put("input/event10/device/properties", "2"); // DIRECT
        assert_eq!(detect_touchscreen(&si, &di, "auto", "sec_touchscreen").unwrap(), di.join("event10").to_string_lossy());
        // the S22 name wins over capability order
        t.put("input/event3/device/name", "sec_touchscreen");
        assert_eq!(detect_touchscreen(&si, &di, "auto", "sec_touchscreen").unwrap(), di.join("event3").to_string_lossy());
        assert_eq!(detect_touchscreen(&si, &di, "/dev/input/event9", "x").unwrap(), "/dev/input/event9");
    }

    #[test]
    fn capability_bitmap_words() {
        // 64-bit words, MSB word first: bit 0x35 is in word 0
        assert!(has_bit("660800000000000", 0x35, 64));
        assert!(has_bit("660800000000000", 0x36, 64));
        assert!(!has_bit("3", 0x35, 64));
        // two words: BTN_TOUCH (0x14a = word 5, bit 10) needs 6 words
        assert!(has_bit("400 0 0 0 0 0", 0x14a, 64));
        // 32-bit kernels: 0x35 = word 1, bit 21
        assert!(has_bit("600000 0", 0x35, 32));
    }

    #[test]
    fn touchscreen_btn_touch_when_no_properties() {
        let t = Tmp::new("touch2");
        t.put("input/event1/device/name", "old-ts");
        t.put("input/event1/device/capabilities/abs", "660800000000000");
        t.put("input/event1/device/capabilities/key", "400 0 0 0 0 0");
        assert!(detect_touchscreen(&t.0.join("input"), &t.0.join("d"), "auto", "").unwrap().ends_with("event1"));
    }

    #[test]
    fn battery_and_backlight() {
        let t = Tmp::new("ps");
        t.put("ps/usb/type", "USB");
        t.put("ps/max170xx_battery/type", "Battery");
        t.put("ps/max170xx_battery/capacity", "77");
        assert_eq!(detect_battery(&t.0.join("ps"), "auto").unwrap(), t.0.join("ps/max170xx_battery"));
        t.put("ps/battery/capacity", "50"); // the S22 name wins
        assert_eq!(detect_battery(&t.0.join("ps"), "auto").unwrap(), t.0.join("ps/battery"));
        t.put("bl/backlight-dsi/max_brightness", "4095");
        t.put("bl/backlight-dsi/brightness", "100");
        assert_eq!(detect_backlight(&t.0.join("bl"), "auto").unwrap(), t.0.join("bl/backlight-dsi"));
        t.put("bl/panel/brightness", "100");
        assert_eq!(detect_backlight(&t.0.join("bl"), "auto").unwrap(), t.0.join("bl/panel"));
        assert!(detect_battery(&t.0.join("none"), "auto").is_none());
    }

    #[test]
    fn thermal_s22_then_fallback() {
        let t = Tmp::new("th");
        t.put("th/thermal_zone0/type", "BIG");
        t.put("th/thermal_zone0/temp", "38000");
        t.put("th/thermal_zone1/type", "battery");
        t.put("th/thermal_zone2/type", "G3D");
        t.put("th/thermal_zone10/type", "MID");
        let s22 = Config::default().thermal_types;
        let z: Vec<String> = detect_thermal(&t.0.join("th"), &s22).into_iter().map(|z| z.0).collect();
        assert_eq!(z, vec!["BIG", "MID", "G3D"]); // zone10 sorts before zone2, as before
        let u = Tmp::new("th2");
        u.put("th/thermal_zone0/type", "cpu0-thermal");
        u.put("th/thermal_zone1/type", "pm8150-tz");
        u.put("th/thermal_zone2/type", "gpu-thermal");
        let z: Vec<String> = detect_thermal(&u.0.join("th"), &s22).into_iter().map(|z| z.0).collect();
        assert_eq!(z, vec!["cpu0-thermal", "gpu-thermal"]);
    }

    #[test]
    fn touch_map_identity_on_s22_and_letterbox() {
        use crate::touch::Touch;
        let m = TouchMap::default();
        assert!(m.is_identity());
        assert_eq!(m.apply(Touch::Down(10, 20, 5)), Touch::Down(10, 20, 5));
        // 1080x2400 panel: canvas centred, 30 px band top and bottom
        let l = TouchMap::default().with_panel(1080, 2400);
        assert_eq!((l.ox, l.oy), (0, 30));
        assert_eq!(l.point(0, 0), (0, -30));
        assert_eq!(l.point(540, 1170), (540, 1170));
        let inv = TouchMap { invert_x: true, invert_y: true, ..TouchMap::default() };
        assert_eq!(inv.point(0, 0), (1079, 2339));
        assert_eq!(inv.apply(Touch::Cancel(3)), Touch::Cancel(3));
    }
}
