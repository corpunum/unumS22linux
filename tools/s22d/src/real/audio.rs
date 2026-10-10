//! The phone's saved volume level (`/srv/s22/buttons/volume`). The route layer decides whether
//! a non-zero level is allowed at all; this only reads and writes the file atomically.
//! `s22-keepalive`'s mute guard keeps the amplifier at 0 independently of this file.

use crate::backends::Audio;
use crate::config::Config;
use crate::error::{ApiError, ApiResult};
use serde_json::json;
use std::sync::Arc;

pub struct VolumeFile {
    cfg: Arc<Config>,
}

impl VolumeFile {
    pub fn new(cfg: Arc<Config>) -> Self {
        Self { cfg }
    }
}

impl Audio for VolumeFile {
    fn level(&self) -> Option<u8> {
        std::fs::read_to_string(&self.cfg.volume_file)
            .ok()?
            .trim()
            .parse()
            .ok()
    }

    fn set_level(&self, level: u8) -> ApiResult {
        let tmp = self.cfg.volume_file.with_extension("s22d-tmp");
        std::fs::write(&tmp, format!("{level}\n"))
            .and_then(|_| std::fs::rename(&tmp, &self.cfg.volume_file))
            .map_err(|e| ApiError::unavailable(format!("volume file: {e}")))?;
        Ok(json!({"ok": true, "level": level, "muted": level == 0}))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::Tmp;

    #[test]
    fn reads_and_writes_atomically() {
        let t = Tmp::new();
        t.write("srv/s22/buttons/volume", "7\n");
        let a = VolumeFile::new(Arc::new(Config::rooted(t.path())));
        assert_eq!(a.level(), Some(7));
        assert_eq!(a.set_level(0).unwrap()["muted"], true);
        assert_eq!(a.level(), Some(0));
        assert!(!t.path().join("srv/s22/buttons/volume.s22d-tmp").exists());
    }

    #[test]
    fn missing_file_reads_as_unknown_and_write_fails_cleanly() {
        let t = Tmp::new();
        let a = VolumeFile::new(Arc::new(Config::rooted(t.path())));
        assert_eq!(a.level(), None);
        assert_eq!(a.set_level(0).unwrap_err().code, "unavailable");
    }
}
