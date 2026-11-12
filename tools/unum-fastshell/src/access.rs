//! Settings → "Rig access": the owner's on/off switches for what the rig's agent may use on this phone
//! through s22d's remote device API (tools/s22d/src/remote.rs). The file is
//! `/srv/s22/state/device-switches.json` (native root only, 0600): the shell runs natively, s22d reads it on
//! every request, and nothing inside the chroot (OpenUnum and its agent shells) can see or change it.
//! A missing entry means the capability's default: everything on, except room listening, which stays
//! off until the owner turns it on here (corpunum-82, 2026-11-12).

use serde_json::{Map, Value};
use std::collections::HashMap;

/// (capability id, row label, detail line, default)
pub const ITEMS: [(&str, &str, &str, bool); 6] = [
    (
        "audio.listen",
        "Room listening",
        "the rig may record the room and get a transcript",
        false,
    ),
    (
        "camera.capture",
        "Camera",
        "the rig may take photos (not kept unless asked)",
        true,
    ),
    (
        "speak",
        "Speak",
        "the rig may talk through the speaker",
        true,
    ),
    (
        "task.submit",
        "Tasks",
        "the rig may give the phone's agent a task",
        true,
    ),
    (
        "sms.send",
        "Text messages",
        "the rig may send SMS (allowlist rules apply)",
        true,
    ),
    (
        "call.dial",
        "Calls",
        "the rig may place calls (allowlist rules apply)",
        true,
    ),
];

pub fn default_on(id: &str) -> bool {
    ITEMS
        .iter()
        .find(|i| i.0 == id)
        .map(|i| i.3)
        .unwrap_or(true)
}

/// Current state of every item (file entries over defaults). An unreadable file means the defaults.
pub fn load(path: &str) -> HashMap<&'static str, bool> {
    let doc: Map<String, Value> = std::fs::read_to_string(path)
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_default();
    ITEMS
        .iter()
        .map(|(id, ..)| {
            (
                *id,
                doc.get(*id)
                    .and_then(Value::as_bool)
                    .unwrap_or_else(|| default_on(id)),
            )
        })
        .collect()
}

/// Set one switch, keeping every other entry; atomic and 0600.
pub fn set(path: &str, id: &str, on: bool) -> Result<(), String> {
    use std::io::Write;
    use std::os::unix::fs::OpenOptionsExt;
    if path.is_empty() {
        return Err("no switch file configured".into());
    }
    let mut doc: Map<String, Value> = std::fs::read_to_string(path)
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_default();
    doc.insert(id.to_string(), Value::Bool(on));
    let tmp = format!("{path}.tmp");
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(&tmp)
        .map_err(|e| format!("{tmp}: {e}"))?;
    f.write_all(
        serde_json::to_string_pretty(&Value::Object(doc))
            .unwrap_or_default()
            .as_bytes(),
    )
    .map_err(|e| format!("{tmp}: {e}"))?;
    drop(f);
    std::fs::rename(&tmp, path).map_err(|e| format!("{path}: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> String {
        let d =
            std::env::temp_dir().join(format!("fastshell-access-{}-{name}", std::process::id()));
        let _ = std::fs::remove_file(&d);
        d.display().to_string()
    }

    #[test]
    fn defaults_listening_off_everything_else_on() {
        let s = load("/nonexistent/switches.json");
        assert!(!s["audio.listen"]);
        assert!(ITEMS
            .iter()
            .filter(|i| i.0 != "audio.listen")
            .all(|i| s[i.0]));
    }

    #[test]
    fn set_keeps_other_entries_and_is_private() {
        use std::os::unix::fs::PermissionsExt;
        let p = tmp("set");
        std::fs::write(&p, r#"{"camera.capture": false, "x.other": true}"#).unwrap();
        set(&p, "audio.listen", true).unwrap();
        let s = load(&p);
        assert!(s["audio.listen"]);
        assert!(!s["camera.capture"], "untouched entry kept");
        let raw: Value = serde_json::from_str(&std::fs::read_to_string(&p).unwrap()).unwrap();
        assert_eq!(raw["x.other"], true, "unknown entries kept");
        assert_eq!(
            std::fs::metadata(&p).unwrap().permissions().mode() & 0o777,
            0o600
        );
        set(&p, "audio.listen", false).unwrap();
        assert!(!load(&p)["audio.listen"]);
        let _ = std::fs::remove_file(&p);
    }
}
