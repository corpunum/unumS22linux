//! Screen-off timeout: pure policy, no I/O except the config file helpers.
//!
//! `unlocked_s`: no touch while unlocked -> lock + panel off.
//! `locked_s`:   lock cover lit with no touch -> panel off.
//! 0 = never. Config: `<state>/screen-timeout.json`, e.g. {"unlocked_s":60,"locked_s":15}.
use std::time::Duration;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Cfg {
    pub unlocked_s: u64,
    pub locked_s: u64,
}

impl Default for Cfg {
    fn default() -> Cfg {
        Cfg { unlocked_s: 60, locked_s: 15 }
    }
}

pub const PRESETS: [u64; 6] = [0, 15, 30, 60, 120, 300];

/// Missing or invalid keys keep the defaults; negative or non-integer values are ignored.
pub fn parse(s: &str) -> Cfg {
    let mut c = Cfg::default();
    if let Ok(v) = serde_json::from_str::<serde_json::Value>(s) {
        if let Some(n) = v["unlocked_s"].as_u64() {
            c.unlocked_s = n;
        }
        if let Some(n) = v["locked_s"].as_u64() {
            c.locked_s = n;
        }
    }
    c
}

pub fn to_json(c: &Cfg) -> String {
    format!("{{\"unlocked_s\":{},\"locked_s\":{}}}\n", c.unlocked_s, c.locked_s)
}

/// Next preset after `cur` (wraps; an off-list value goes to the first larger one).
pub fn next_preset(cur: u64) -> u64 {
    PRESETS.iter().copied().find(|&p| p > cur).unwrap_or(PRESETS[0])
}

/// Should the panel go off now? An inhibited screen never does.
pub fn due(c: &Cfg, idle: Duration, locked: bool, inhibited: bool) -> bool {
    if inhibited {
        return false;
    }
    let limit = if locked { c.locked_s } else { c.unlocked_s };
    limit > 0 && idle >= Duration::from_secs(limit)
}

/// True while a process holds the flock on `path` (s22-say keeps /run/s22-say.lock
/// locked for the whole utterance). Never creates the file.
pub fn flock_held(path: &str) -> bool {
    use std::os::fd::AsRawFd;
    match std::fs::File::open(path) {
        Ok(f) => {
            let r = unsafe { libc::flock(f.as_raw_fd(), libc::LOCK_SH | libc::LOCK_NB) };
            if r == 0 {
                unsafe { libc::flock(f.as_raw_fd(), libc::LOCK_UN) };
                false
            } else {
                std::io::Error::last_os_error().raw_os_error() == Some(libc::EWOULDBLOCK)
            }
        }
        Err(_) => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    const S: fn(u64) -> Duration = Duration::from_secs;

    #[test]
    fn parse_defaults_and_zero() {
        assert_eq!(parse(""), Cfg { unlocked_s: 60, locked_s: 15 });
        assert_eq!(parse("{\"unlocked_s\":120}"), Cfg { unlocked_s: 120, locked_s: 15 });
        assert_eq!(parse("{\"unlocked_s\":0,\"locked_s\":0}"), Cfg { unlocked_s: 0, locked_s: 0 });
        assert_eq!(parse("{\"unlocked_s\":-5,\"locked_s\":\"x\"}"), Cfg::default());
        assert_eq!(parse(&to_json(&Cfg { unlocked_s: 30, locked_s: 0 })), Cfg { unlocked_s: 30, locked_s: 0 });
    }

    #[test]
    fn due_thresholds() {
        let c = Cfg::default();
        assert!(!due(&c, S(59), false, false));
        assert!(due(&c, S(60), false, false));
        assert!(!due(&c, S(14), true, false));
        assert!(due(&c, S(15), true, false));
        assert!(!due(&c, S(3600), false, true), "inhibited");
        let never = Cfg { unlocked_s: 0, locked_s: 0 };
        assert!(!due(&never, S(99999), false, false));
        assert!(!due(&never, S(99999), true, false));
    }

    #[test]
    fn presets_cycle() {
        assert_eq!(next_preset(60), 120);
        assert_eq!(next_preset(300), 0);
        assert_eq!(next_preset(0), 15);
        assert_eq!(next_preset(45), 60);
    }

    #[test]
    fn flock_probe() {
        use std::os::fd::AsRawFd;
        let p = std::env::temp_dir().join(format!("idle-lock-{}", std::process::id()));
        let ps = p.to_str().unwrap();
        assert!(!flock_held(ps), "missing file");
        let f = std::fs::File::create(&p).unwrap();
        assert!(!flock_held(ps), "unlocked");
        unsafe { libc::flock(f.as_raw_fd(), libc::LOCK_EX) };
        assert!(flock_held(ps), "held");
        drop(f);
        assert!(!flock_held(ps), "released");
        let _ = std::fs::remove_file(&p);
    }
}
