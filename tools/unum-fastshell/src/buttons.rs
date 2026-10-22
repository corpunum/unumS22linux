//! Physical buttons (volume up/down, Power) read straight from evdev, plus the
//! chord / long-press / double-press recogniser and the speaker level.
//!
//! The key nodes are found by their key capabilities (gpio_keys has volume up,
//! the PMIC key node has volume down and Power on the S22), opened without a
//! grab, with CLOCK_MONOTONIC timestamps so they compare with DRM flip events.
//!
//! Ownership: while the shell handles the keys it writes its pid to
//! `/run/s22-fastshell.buttons`; s22-buttons then only logs, and keeps the one
//! binding that must not depend on the shell: Power held >= 3 s alone ->
//! hooks/power-long (back to Quickshell). Without the marker (shell stopped,
//! crashed, or button handling switched off in Settings) s22-buttons does
//! everything as before.
//!
//! `Machine` is pure (times in µs, no I/O) so the timing rules are unit-tested.
use std::io;
use std::os::fd::RawFd;

pub const KEY_VOLUMEDOWN: u16 = 114;
pub const KEY_VOLUMEUP: u16 = 115;
pub const KEY_POWER: u16 = 116;

pub const MARKER: &str = "/run/s22-fastshell.buttons";
pub const SAY_LEVEL: &str = "/run/s22-say.level";
pub const SAVED_LEVEL: &str = "/srv/s22/buttons/volume";
pub const BUTTONS_BASE: &str = "/srv/s22/buttons";
pub const LEVELS: i32 = 10;
/// s22-say's speech level when /run/s22-say.level is absent.
pub const DEFAULT_LEVEL: i32 = 4;

/// What the Settings page lists (keep in step with `Machine`).
pub const BINDINGS: [(&str, &str); 9] = [
    ("Volume up / down", "speaker level ±1, on-screen level"),
    ("Hold Volume up", "push-to-talk: speak while held"),
    ("Hold Volume down 1 s", "mute / unmute"),
    ("Power", "lock + screen off / screen on"),
    ("Power twice", "camera"),
    ("Power + Volume down", "screenshot → gallery, Files"),
    ("Power + Volume up", "keep screen on (toggle)"),
    ("Hold Power 3 s", "classic shell (safety, s22-buttons)"),
    ("Any key, screen off", "wakes the screen"),
];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Key {
    VolUp,
    VolDown,
    Power,
}

impl Key {
    pub fn from_code(c: u16) -> Option<Key> {
        match c {
            KEY_VOLUMEUP => Some(Key::VolUp),
            KEY_VOLUMEDOWN => Some(Key::VolDown),
            KEY_POWER => Some(Key::Power),
            _ => None,
        }
    }
    fn idx(self) -> usize {
        self as usize
    }
    pub fn name(self) -> &'static str {
        match self {
            Key::VolUp => "volup",
            Key::VolDown => "voldown",
            Key::Power => "power",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Action {
    /// Speaker level +1 / -1 (on key-down, instantly).
    Step(i32),
    /// Undo the step this press made (it became a hold or a chord).
    Revert,
    /// Volume down held: mute, or unmute when already muted.
    Mute,
    /// Volume up held, hold mode: start push-to-talk (held ms).
    PttStart(u32),
    /// Volume up released after PttStart (held ms).
    PttRelease(u32),
    /// Volume up held without hold mode: the assistant hook on release (fixed window).
    Assistant(u32),
    /// A key went down while the screen was off.
    Wake,
    /// Power tapped with the screen on: lock + panel off.
    Lock,
    /// Power pressed twice.
    Camera,
    /// Power + volume down.
    Screenshot,
    /// Power + volume up.
    KeepAwake,
    /// Power alone held 1 s: show "keep holding" (the classic-shell fallback is 3 s away).
    PowerHint,
    /// Power released before the fallback hold time: drop the hint.
    PowerHintClear,
}

#[derive(Clone, Copy, Debug)]
pub struct Timing {
    /// Hold threshold (same as s22-buttons LONG_MS and hooks/power-long).
    pub long_us: u64,
    /// Power alone held this long: s22-buttons runs hooks/power-long (classic shell).
    pub power_long_us: u64,
    /// The second key of a chord must go down this soon after the first.
    pub chord_us: u64,
    /// Second Power press this soon after the first release = double press.
    pub double_us: u64,
}

impl Default for Timing {
    fn default() -> Timing {
        Timing { long_us: 1_000_000, power_long_us: 3_000_000, chord_us: 400_000, double_us: 400_000 }
    }
}

#[derive(Clone, Copy, Debug, Default)]
pub struct Ctx {
    pub display_on: bool,
    /// /srv/s22/buttons/ptt-hold exists and hooks/assistant is executable.
    pub ptt_hold: bool,
}

#[derive(Clone, Copy, Debug, Default)]
struct Press {
    t: u64,
    /// Part of a chord or a double press: no single-key action on release.
    consumed: bool,
    stepped: bool,
    long_fired: bool,
    ptt: bool,
    /// This press woke the screen (so its release does not lock it again).
    woke: bool,
}

#[derive(Default)]
pub struct Machine {
    pub timing: Timing,
    down: [Option<Press>; 3],
    last_tap: Option<u64>,
}

impl Machine {
    pub fn new(timing: Timing) -> Machine {
        Machine { timing, down: [None; 3], last_tap: None }
    }

    pub fn any_down(&self) -> bool {
        self.down.iter().any(|p| p.is_some())
    }

    pub fn press(&mut self, k: Key, t: u64, ctx: Ctx) -> Vec<Action> {
        let mut out = vec![];
        if self.down[k.idx()].is_some() {
            return out; // autorepeat / duplicate
        }
        let mut p = Press { t, ..Default::default() };
        if !ctx.display_on {
            out.push(Action::Wake);
            p.woke = true;
        }
        // chord: Power together with a volume key, either order, within chord_us
        let other = match k {
            Key::Power => [Key::VolDown, Key::VolUp].into_iter().find(|o| self.chord_ok(*o, t)),
            _ => Some(Key::Power).filter(|o| self.chord_ok(*o, t)),
        };
        if let Some(o) = other {
            let vol = if k == Key::Power { o } else { k };
            let first = self.down[o.idx()].as_mut().unwrap();
            first.consumed = true;
            if first.stepped {
                first.stepped = false;
                out.push(Action::Revert);
            }
            p.consumed = true;
            self.last_tap = None;
            out.push(if vol == Key::VolDown { Action::Screenshot } else { Action::KeepAwake });
            self.down[k.idx()] = Some(p);
            return out;
        }
        match k {
            Key::Power => {
                if self.last_tap.is_some_and(|lt| t.saturating_sub(lt) <= self.timing.double_us) {
                    p.consumed = true;
                    self.last_tap = None;
                    out.push(Action::Camera);
                }
            }
            Key::VolUp => {
                out.push(Action::Step(1));
                p.stepped = true;
            }
            Key::VolDown => {
                out.push(Action::Step(-1));
                p.stepped = true;
            }
        }
        self.down[k.idx()] = Some(p);
        out
    }

    fn chord_ok(&self, o: Key, t: u64) -> bool {
        self.down[o.idx()].is_some_and(|p| !p.consumed && !p.long_fired && t.saturating_sub(p.t) <= self.timing.chord_us)
    }

    pub fn release(&mut self, k: Key, t: u64, ctx: Ctx) -> Vec<Action> {
        let _ = ctx;
        let Some(p) = self.down[k.idx()].take() else { return vec![] };
        let held = (t.saturating_sub(p.t) / 1000) as u32;
        let long = t.saturating_sub(p.t) >= self.timing.long_us;
        match k {
            Key::Power => {
                if p.consumed {
                    vec![]
                } else if long {
                    // s22-buttons runs hooks/power-long (the safety path) itself at
                    // power_long_us; a 1 s .. 3 s hold does nothing but drop the hint
                    self.last_tap = None;
                    if p.long_fired { vec![Action::PowerHintClear] } else { vec![] }
                } else {
                    self.last_tap = Some(t);
                    if p.woke { vec![] } else { vec![Action::Lock] }
                }
            }
            Key::VolUp => {
                if p.ptt {
                    vec![Action::PttRelease(held)]
                } else if p.consumed {
                    vec![]
                } else if p.long_fired {
                    vec![Action::Assistant(held)]
                } else {
                    vec![]
                }
            }
            Key::VolDown => vec![],
        }
    }

    /// Call when `next_deadline` passes (and harmlessly at any other time).
    pub fn tick(&mut self, now: u64, ctx: Ctx) -> Vec<Action> {
        let mut out = vec![];
        let long = self.timing.long_us;
        for k in [Key::VolUp, Key::VolDown, Key::Power] {
            let Some(p) = self.down[k.idx()].as_mut() else { continue };
            if p.consumed || p.long_fired || now.saturating_sub(p.t) < long {
                continue;
            }
            p.long_fired = true;
            if k == Key::Power {
                out.push(Action::PowerHint);
                continue;
            }
            if p.stepped {
                p.stepped = false;
                out.push(Action::Revert);
            }
            match k {
                Key::VolUp if ctx.ptt_hold => {
                    p.ptt = true;
                    out.push(Action::PttStart((now.saturating_sub(p.t) / 1000) as u32));
                }
                Key::VolUp => {}
                _ => out.push(Action::Mute),
            }
        }
        out
    }

    /// When `tick` next has something to decide (a key reaching the hold threshold).
    pub fn next_deadline(&self) -> Option<u64> {
        [Key::VolUp, Key::VolDown, Key::Power]
            .iter()
            .filter_map(|k| self.down[k.idx()])
            .filter(|p| !p.consumed && !p.long_fired)
            .map(|p| p.t + self.timing.long_us)
            .min()
    }
}

// ---------------------------------------------------------------- speaker level

/// The speaker level 0-10 that applies now: /run/s22-say.level (kept across
/// s22-keepalive's mute guard), else s22-say's default 4.
pub fn read_level(path: &str) -> i32 {
    std::fs::read_to_string(path).ok().and_then(|s| s.trim().parse().ok()).map(|l: i32| l.clamp(0, LEVELS)).unwrap_or(DEFAULT_LEVEL)
}

/// CS35L41 'Digital PCM Volume': 0.125 dB/step, 817 = 0 dB, 0 = mute (as s22-buttons / s22-say).
pub fn amp_raw(level: i32) -> i64 {
    if level <= 0 {
        0
    } else {
        817 - (LEVELS - level.min(LEVELS)) as i64 * 32
    }
}

fn write_atomic(path: &str, v: &str) -> io::Result<()> {
    let tmp = format!("{path}.tmp");
    std::fs::write(&tmp, v)?;
    std::fs::rename(&tmp, path)
}

/// Store the level for s22-say (and s22-buttons' saved level); when something is
/// playing, set the amps too so the change is heard at once. At rest the amps
/// stay at 0, as s22-keepalive's mute guard wants; s22-say raises them to this
/// level for each playback.
pub fn apply_level(level: i32, playing: bool) -> io::Result<()> {
    write_atomic(SAY_LEVEL, &format!("{level}\n"))?;
    let _ = write_atomic(SAVED_LEVEL, &format!("{level}\n"));
    if playing {
        let raw = amp_raw(level);
        for side in ["Left Digital PCM Volume", "Right Digital PCM Volume"] {
            alsa_set(0, side, raw)?;
        }
    }
    Ok(())
}

/// A playback stream is running (s22-say, the voice loop, the assistant cue).
pub fn playback_running() -> bool {
    for p in ["pcm100p", "pcm102p", "pcm6p"] {
        if std::fs::read_to_string(format!("/proc/asound/card0/{p}/sub0/status")).is_ok_and(|s| s.contains("RUNNING")) {
            return true;
        }
    }
    false
}

// struct snd_ctl_elem_value on LP64: id (64 bytes) + indirect (4 + 4 pad) +
// value union (1024) + reserved (128) = 1224 bytes.
const ELEM_VALUE_SIZE: usize = 1224;
const SNDRV_CTL_IOCTL_ELEM_WRITE: u64 = (3 << 30) | ((ELEM_VALUE_SIZE as u64) << 16) | ((b'U' as u64) << 8) | 0x13;
const SNDRV_CTL_ELEM_IFACE_MIXER: i32 = 2;

fn elem_value(name: &str) -> [u8; ELEM_VALUE_SIZE] {
    let mut b = [0u8; ELEM_VALUE_SIZE];
    b[4..8].copy_from_slice(&SNDRV_CTL_ELEM_IFACE_MIXER.to_ne_bytes());
    let n = name.as_bytes();
    let l = n.len().min(43);
    b[16..16 + l].copy_from_slice(&n[..l]);
    b
}

/// Write an integer mixer control by name, all (up to two) channels, with one
/// ioctl on /dev/snd/controlC<card> (no amixer process, no chroot).
pub fn alsa_set(card: u32, name: &str, v: i64) -> io::Result<()> {
    let c = std::ffi::CString::new(format!("/dev/snd/controlC{card}")).unwrap();
    let fd = unsafe { libc::open(c.as_ptr(), libc::O_RDWR | libc::O_CLOEXEC) };
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }
    let mut b = elem_value(name);
    b[72..80].copy_from_slice(&v.to_ne_bytes());
    b[80..88].copy_from_slice(&v.to_ne_bytes());
    let r = unsafe { libc::ioctl(fd, SNDRV_CTL_IOCTL_ELEM_WRITE as _, b.as_mut_ptr()) };
    let e = io::Error::last_os_error();
    unsafe { libc::close(fd) };
    if r < 0 {
        Err(e)
    } else {
        Ok(())
    }
}

// ---------------------------------------------------------------- evdev

/// Event nodes that report any of the three keys (by capabilities, not by name).
pub fn find_key_nodes(sys: &str) -> Vec<String> {
    let mut v: Vec<String> = std::fs::read_dir(sys).map(|d| d.flatten().map(|e| e.file_name().to_string_lossy().to_string()).filter(|n| n.starts_with("event")).collect()).unwrap_or_default();
    v.sort();
    v.into_iter()
        .filter(|n| {
            std::fs::read_to_string(format!("{sys}/{n}/device/capabilities/key"))
                .map(|s| caps_have(&s, &[KEY_VOLUMEUP, KEY_VOLUMEDOWN, KEY_POWER]))
                .unwrap_or(false)
        })
        // the touchscreen reports KEY_POWER-range bits too on some kernels: skip touch devices
        .filter(|n| !std::fs::read_to_string(format!("{sys}/{n}/device/capabilities/abs")).map(|s| s.trim() != "0").unwrap_or(false))
        .map(|n| format!("/dev/input/{n}"))
        .collect()
}

/// sysfs capability bitmap: space-separated hex words, most significant first.
pub fn caps_have(s: &str, codes: &[u16]) -> bool {
    let words: Vec<u64> = s.split_whitespace().rev().filter_map(|w| u64::from_str_radix(w, 16).ok()).collect();
    codes.iter().any(|&c| words.get(c as usize / 64).is_some_and(|w| w >> (c % 64) & 1 == 1))
}

pub struct KeyDev {
    pub fd: RawFd,
}

impl KeyDev {
    pub fn open(path: &str) -> io::Result<KeyDev> {
        let c = std::ffi::CString::new(path).unwrap();
        let fd = unsafe { libc::open(c.as_ptr(), libc::O_RDONLY | libc::O_NONBLOCK | libc::O_CLOEXEC) };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        let clk: libc::c_int = libc::CLOCK_MONOTONIC;
        let req = (1u64 << 30) | (4u64 << 16) | ((b'E' as u64) << 8) | 0xa0; // EVIOCSCLOCKID
        unsafe { libc::ioctl(fd, req as _, &clk) };
        Ok(KeyDev { fd })
    }

    /// Drain pending key events: (key, pressed, t µs CLOCK_MONOTONIC). Autorepeat (value 2) is dropped.
    pub fn read(&mut self, out: &mut Vec<(Key, bool, u64)>) {
        let mut buf = [0u8; 24 * 32];
        loop {
            let n = unsafe { libc::read(self.fd, buf.as_mut_ptr() as *mut _, buf.len()) };
            if n <= 0 {
                return;
            }
            for ev in buf[..n as usize].chunks_exact(24) {
                let sec = i64::from_ne_bytes(ev[0..8].try_into().unwrap());
                let usec = i64::from_ne_bytes(ev[8..16].try_into().unwrap());
                let typ = u16::from_ne_bytes(ev[16..18].try_into().unwrap());
                let code = u16::from_ne_bytes(ev[18..20].try_into().unwrap());
                let val = i32::from_ne_bytes(ev[20..24].try_into().unwrap());
                if typ == 1 && (val == 0 || val == 1) {
                    if let Some(k) = Key::from_code(code) {
                        out.push((k, val == 1, (sec * 1_000_000 + usec) as u64));
                    }
                }
            }
        }
    }
}

impl Drop for KeyDev {
    fn drop(&mut self) {
        unsafe { libc::close(self.fd) };
    }
}

/// /srv/s22/buttons/ptt-hold exists and hooks/assistant is executable (as s22-buttons).
pub fn ptt_hold_mode(base: &str) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::path::Path::new(&format!("{base}/ptt-hold")).is_file()
        && std::fs::metadata(format!("{base}/hooks/assistant")).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
}

/// The release note s22-assistant waits for in hold mode (same format as s22-buttons).
pub fn write_ptt_release(base: &str, held_ms: u32) -> io::Result<()> {
    let t = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs_f64();
    write_atomic(&format!("{base}/ptt-release"), &format!("{{\"t\": {t:.3}, \"held_ms\": {held_ms}}}\n"))
}

pub fn claim(on: bool) {
    if on {
        let _ = write_atomic(MARKER, &format!("{}\n", std::process::id()));
    } else {
        let _ = std::fs::remove_file(MARKER);
    }
}

/// Settings toggle, `<state>/buttons.json` {"enabled": true}. Default on.
pub fn load_enabled(path: &str) -> bool {
    std::fs::read_to_string(path).ok().and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok()).and_then(|v| v["enabled"].as_bool()).unwrap_or(true)
}

pub fn save_enabled(path: &str, on: bool) {
    let _ = write_atomic(path, &format!("{{\"enabled\": {on}}}\n"));
}

#[cfg(test)]
mod tests {
    use super::*;

    const ON: Ctx = Ctx { display_on: true, ptt_hold: true };
    const OFF: Ctx = Ctx { display_on: false, ptt_hold: true };
    const MS: u64 = 1000;

    fn m() -> Machine {
        Machine::new(Timing::default())
    }

    #[test]
    fn volume_steps_on_key_down_and_release_does_nothing() {
        let mut b = m();
        assert_eq!(b.press(Key::VolUp, 0, ON), vec![Action::Step(1)]);
        assert_eq!(b.release(Key::VolUp, 120 * MS, ON), vec![]);
        assert_eq!(b.press(Key::VolDown, 500 * MS, ON), vec![Action::Step(-1)]);
        assert_eq!(b.release(Key::VolDown, 600 * MS, ON), vec![]);
    }

    #[test]
    fn volume_up_hold_reverts_the_step_and_starts_push_to_talk() {
        let mut b = m();
        assert_eq!(b.press(Key::VolUp, 0, ON), vec![Action::Step(1)]);
        assert_eq!(b.next_deadline(), Some(1000 * MS));
        assert_eq!(b.tick(999 * MS, ON), vec![]);
        assert_eq!(b.tick(1000 * MS, ON), vec![Action::Revert, Action::PttStart(1000)]);
        assert_eq!(b.tick(1500 * MS, ON), vec![], "fires once");
        assert_eq!(b.next_deadline(), None);
        assert_eq!(b.release(Key::VolUp, 4200 * MS, ON), vec![Action::PttRelease(4200)]);
    }

    #[test]
    fn volume_up_hold_without_hold_mode_runs_the_assistant_on_release() {
        let mut b = m();
        let c = Ctx { display_on: true, ptt_hold: false };
        b.press(Key::VolUp, 0, c);
        assert_eq!(b.tick(1100 * MS, c), vec![Action::Revert]);
        assert_eq!(b.release(Key::VolUp, 1300 * MS, c), vec![Action::Assistant(1300)]);
    }

    #[test]
    fn volume_down_hold_mutes() {
        let mut b = m();
        b.press(Key::VolDown, 0, ON);
        assert_eq!(b.tick(1000 * MS, ON), vec![Action::Revert, Action::Mute]);
        assert_eq!(b.release(Key::VolDown, 1400 * MS, ON), vec![]);
    }

    #[test]
    fn power_tap_locks_on_release_only() {
        let mut b = m();
        assert_eq!(b.press(Key::Power, 0, ON), vec![]);
        assert_eq!(b.release(Key::Power, 130 * MS, ON), vec![Action::Lock]);
    }

    #[test]
    fn any_key_wakes_on_key_down_and_the_waking_power_tap_does_not_lock() {
        let mut b = m();
        assert_eq!(b.press(Key::Power, 0, OFF), vec![Action::Wake]);
        assert_eq!(b.release(Key::Power, 120 * MS, ON), vec![]);
        let mut b = m();
        assert_eq!(b.press(Key::VolDown, 0, OFF), vec![Action::Wake, Action::Step(-1)]);
    }

    #[test]
    fn power_hold_hints_at_1s_and_is_left_to_the_safety_hook_at_3s() {
        let mut b = m();
        b.press(Key::Power, 0, ON);
        assert_eq!(b.next_deadline(), Some(1000 * MS));
        assert_eq!(b.tick(900 * MS, ON), vec![], "no hint before 1 s");
        assert_eq!(b.tick(1000 * MS, ON), vec![Action::PowerHint]);
        assert_eq!(b.tick(2000 * MS, ON), vec![], "hint only once");
        assert_eq!(b.next_deadline(), None);
        assert_eq!(b.release(Key::Power, 3200 * MS, ON), vec![Action::PowerHintClear], "no lock: s22-buttons runs hooks/power-long at 3 s");
        // and a following quick press is not a double press
        assert_eq!(b.press(Key::Power, 3400 * MS, ON), vec![]);
    }

    #[test]
    fn power_released_between_1_and_3s_cancels_the_hint_and_does_nothing() {
        let mut b = m();
        b.press(Key::Power, 0, ON);
        assert_eq!(b.tick(1000 * MS, ON), vec![Action::PowerHint]);
        assert_eq!(b.release(Key::Power, 1500 * MS, ON), vec![Action::PowerHintClear], "no lock, no classic shell");
        assert_eq!(b.press(Key::Power, 1700 * MS, ON), vec![], "not a double press");
    }

    #[test]
    fn power_tap_under_1s_has_no_hint() {
        let mut b = m();
        b.press(Key::Power, 0, ON);
        assert_eq!(b.tick(500 * MS, ON), vec![]);
        assert_eq!(b.release(Key::Power, 500 * MS, ON), vec![Action::Lock]);
    }

    #[test]
    fn power_then_volume_down_is_a_screenshot_and_nothing_else() {
        let mut b = m();
        assert_eq!(b.press(Key::Power, 0, ON), vec![]);
        assert_eq!(b.press(Key::VolDown, 150 * MS, ON), vec![Action::Screenshot]);
        assert_eq!(b.release(Key::VolDown, 300 * MS, ON), vec![]);
        assert_eq!(b.release(Key::Power, 320 * MS, ON), vec![], "no lock after a chord");
        assert_eq!(b.tick(5000 * MS, ON), vec![]);
    }

    #[test]
    fn volume_down_then_power_reverts_the_step() {
        let mut b = m();
        assert_eq!(b.press(Key::VolDown, 0, ON), vec![Action::Step(-1)]);
        assert_eq!(b.press(Key::Power, 200 * MS, ON), vec![Action::Revert, Action::Screenshot]);
        // held past the hold threshold: still no mute, no power-long, no lock
        assert_eq!(b.tick(1500 * MS, ON), vec![]);
        assert_eq!(b.release(Key::Power, 1600 * MS, ON), vec![]);
        assert_eq!(b.release(Key::VolDown, 1650 * MS, ON), vec![]);
    }

    #[test]
    fn power_and_volume_up_toggle_keep_awake_without_push_to_talk() {
        let mut b = m();
        b.press(Key::Power, 0, ON);
        assert_eq!(b.press(Key::VolUp, 100 * MS, ON), vec![Action::KeepAwake]);
        assert_eq!(b.tick(2000 * MS, ON), vec![], "no PTT from a chord");
        assert_eq!(b.release(Key::VolUp, 2100 * MS, ON), vec![]);
        assert_eq!(b.release(Key::Power, 2100 * MS, ON), vec![]);
    }

    #[test]
    fn chord_window_is_400_ms() {
        let mut b = m();
        b.press(Key::Power, 0, ON);
        assert_eq!(b.press(Key::VolDown, 401 * MS, ON), vec![Action::Step(-1)], "too late: two single presses");
        assert_eq!(b.release(Key::Power, 450 * MS, ON), vec![Action::Lock]);
        let mut b = m();
        b.press(Key::VolUp, 0, ON);
        assert_eq!(b.press(Key::Power, 400 * MS, ON), vec![Action::Revert, Action::KeepAwake]);
    }

    #[test]
    fn push_to_talk_then_power_is_not_a_chord() {
        let mut b = m();
        b.press(Key::VolUp, 0, ON);
        b.tick(1000 * MS, ON);
        assert_eq!(b.press(Key::Power, 1100 * MS, ON), vec![]);
        assert_eq!(b.release(Key::Power, 1200 * MS, ON), vec![Action::Lock]);
        assert_eq!(b.release(Key::VolUp, 3000 * MS, ON), vec![Action::PttRelease(3000)]);
    }

    #[test]
    fn double_power_press_opens_the_camera() {
        let mut b = m();
        b.press(Key::Power, 0, ON);
        assert_eq!(b.release(Key::Power, 100 * MS, ON), vec![Action::Lock]);
        assert_eq!(b.press(Key::Power, 350 * MS, OFF), vec![Action::Wake, Action::Camera]);
        assert_eq!(b.release(Key::Power, 450 * MS, ON), vec![]);
        // a third press is a new single press
        assert_eq!(b.press(Key::Power, 600 * MS, ON), vec![]);
        assert_eq!(b.release(Key::Power, 700 * MS, ON), vec![Action::Lock]);
    }

    #[test]
    fn double_press_from_screen_off_and_window() {
        let mut b = m();
        assert_eq!(b.press(Key::Power, 0, OFF), vec![Action::Wake]);
        assert_eq!(b.release(Key::Power, 100 * MS, ON), vec![]);
        assert_eq!(b.press(Key::Power, 300 * MS, ON), vec![Action::Camera]);
        b.release(Key::Power, 400 * MS, ON);
        // slower than 400 ms between release and press: lock, not camera
        let mut b = m();
        b.press(Key::Power, 0, ON);
        b.release(Key::Power, 100 * MS, ON);
        assert_eq!(b.press(Key::Power, 501 * MS, OFF), vec![Action::Wake]);
    }

    #[test]
    fn duplicate_down_and_stray_up_are_ignored() {
        let mut b = m();
        assert_eq!(b.release(Key::Power, 0, ON), vec![]);
        b.press(Key::VolUp, 0, ON);
        assert_eq!(b.press(Key::VolUp, 10 * MS, ON), vec![]);
    }

    #[test]
    fn level_amp_and_caps() {
        assert_eq!(amp_raw(10), 817);
        assert_eq!(amp_raw(4), 817 - 6 * 32);
        assert_eq!(amp_raw(0), 0);
        // the S22's nodes: gpio_keys = volume up, sec-pmic-key = volume down + Power
        assert!(caps_have("8000000000000 0", &[KEY_VOLUMEUP]));
        assert!(!caps_have("8000000000000 0", &[KEY_POWER, KEY_VOLUMEDOWN]));
        assert!(caps_have("14000000000000 0", &[KEY_POWER]));
        assert!(caps_have("14000000000000 0", &[KEY_VOLUMEDOWN]));
        assert!(!caps_have("0", &[KEY_POWER]));
        let d = std::env::temp_dir().join(format!("fs-level-{}", std::process::id()));
        let p = d.to_string_lossy().to_string();
        assert_eq!(read_level(&p), DEFAULT_LEVEL);
        std::fs::write(&d, "7\n").unwrap();
        assert_eq!(read_level(&p), 7);
        std::fs::write(&d, "99\n").unwrap();
        assert_eq!(read_level(&p), 10);
        let _ = std::fs::remove_file(&d);
    }

    #[test]
    fn elem_value_layout() {
        assert_eq!(SNDRV_CTL_IOCTL_ELEM_WRITE, 0xC4C8_5513);
        let b = elem_value("Left Digital PCM Volume");
        assert_eq!(&b[4..8], &2i32.to_ne_bytes());
        assert_eq!(&b[16..39], b"Left Digital PCM Volume");
        assert_eq!(b[39], 0);
    }
}
