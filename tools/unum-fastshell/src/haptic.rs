//! Haptic feedback through s22-haptic's daemon: one datagram per event to
//! `/run/s22-haptic.sock` ("tick" ~9 ms, "buzz" ~41 ms; see docs/S22_HAPTICS.md).
//! Fire-and-forget on a non-blocking socket: no daemon = no vibration, never a stall.
use std::os::unix::net::UnixDatagram;

pub const SOCK: &str = "/run/s22-haptic.sock";
/// The daemon's switch ({"enabled": false} = every command is a no-op); the shell
/// also reads it so it does not send at all when vibration is off.
pub const CONFIG: &str = "/srv/s22/state/haptics/config.json";

pub struct Haptic {
    sock: Option<UnixDatagram>,
    path: String,
    pub sent: u64,
    pub failed: u64,
}

impl Haptic {
    pub fn new(path: &str) -> Haptic {
        let sock = UnixDatagram::unbound().ok();
        if let Some(s) = &sock {
            let _ = s.set_nonblocking(true);
        }
        Haptic { sock, path: path.into(), sent: 0, failed: 0 }
    }

    pub fn send(&mut self, what: &str) -> bool {
        let ok = self.sock.as_ref().is_some_and(|s| s.send_to(what.as_bytes(), &self.path).is_ok());
        if ok {
            self.sent += 1;
        } else {
            self.failed += 1;
        }
        ok
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sends_one_datagram_and_never_blocks_without_a_daemon() {
        let dir = std::env::temp_dir().join(format!("fs-haptic-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let p = dir.join("h.sock");
        let _ = std::fs::remove_file(&p);
        let rx = UnixDatagram::bind(&p).unwrap();
        let mut h = Haptic::new(p.to_str().unwrap());
        assert!(h.send("tick"));
        let mut b = [0u8; 16];
        let n = rx.recv(&mut b).unwrap();
        assert_eq!(&b[..n], b"tick");
        drop(rx);
        let _ = std::fs::remove_file(&p);
        let t = std::time::Instant::now();
        assert!(!h.send("tick"), "no daemon: fails quietly");
        assert!(t.elapsed().as_millis() < 50);
        assert_eq!((h.sent, h.failed), (1, 1));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
