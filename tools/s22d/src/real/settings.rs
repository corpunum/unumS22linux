//! The small, file- and socket-backed settings behind the device API (`/v1/device`): speech
//! volume, vibration and haptics, autosleep, screen timeout, Bluetooth (`s22-bt`), the call/SMS
//! allowlist and call gate, the one-shot grants for `s22-phoned`, the network policy's state,
//! the 5G badge and the shell's screenshot hook. Every path comes from `Config`, so the tests run
//! against a fixture tree. Each setter returns the state read back from where it was written.

use super::runner::{Runner, SysRunner};
use super::touch;
use crate::config::Config;
use crate::error::{ApiError, ApiResult};
use serde_json::{json, Map, Value};
use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

pub fn now_s() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

pub fn read_json(p: &Path) -> Option<Value> {
    serde_json::from_str(&std::fs::read_to_string(p).ok()?).ok()
}

fn read_trim(p: &Path) -> Option<String> {
    std::fs::read_to_string(p)
        .ok()
        .map(|s| s.trim().to_string())
}

/// Write via `<file>.s22d-tmp` + rename, creating the directory.
pub fn write_atomic(p: &Path, text: &str) -> Result<(), ApiError> {
    let fail = |e: std::io::Error| ApiError::unavailable(format!("{}: {e}", p.display()));
    if let Some(d) = p.parent() {
        if !d.exists() {
            use std::os::unix::fs::PermissionsExt;
            std::fs::create_dir_all(d).map_err(fail)?;
            // keepalive's umask is 0: do not leave world-writable directories behind.
            let _ = std::fs::set_permissions(d, std::fs::Permissions::from_mode(0o755));
        }
    }
    let mut tmp = p.as_os_str().to_owned();
    tmp.push(".s22d-tmp");
    {
        use std::io::Write;
        use std::os::unix::fs::OpenOptionsExt;
        // s22d runs with keepalive's umask 0: state files are 0644, never world-writable.
        let mut f = std::fs::OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o644)
            .open(&tmp)
            .map_err(fail)?;
        f.write_all(text.as_bytes()).map_err(fail)?;
        use std::os::unix::fs::PermissionsExt;
        f.set_permissions(std::fs::Permissions::from_mode(0o644))
            .map_err(fail)?;
    }
    std::fs::rename(&tmp, p).map_err(fail)
}

/// Set keys in a JSON object file, keeping every other key the owner or a tool put there.
pub fn merge_json(p: &Path, patch: Value) -> Result<Value, ApiError> {
    let mut obj = match read_json(p) {
        Some(Value::Object(m)) => m,
        _ => Map::new(),
    };
    if let Value::Object(m) = patch {
        obj.extend(m);
    }
    let v = Value::Object(obj);
    write_atomic(p, &format!("{v}\n"))?;
    Ok(v)
}

// ---------------------------------------------------------------- speech volume

/// What s22-say uses when `/run/s22-say.level` is absent.
pub const SAY_DEFAULT: u8 = 6;
/// Level an unmute returns to when the pre-mute level is unknown (the shell's default).
pub const UNMUTE_DEFAULT: u8 = 4;

pub fn volume(cfg: &Config) -> Value {
    let file = read_trim(&cfg.say_level).and_then(|s| s.parse::<u8>().ok());
    let level = file.unwrap_or(SAY_DEFAULT).min(10);
    json!({
        "level": level,
        "muted": level == 0,
        "max": 10,
        "source": if file.is_some() { "file" } else { "default" },
    })
}

/// `value` 0-10 sets the level (0 = mute); `muted` true/false mutes or restores the pre-mute level.
pub fn set_volume(cfg: &Config, value: Option<u8>, muted: Option<bool>) -> ApiResult {
    let cur = volume(cfg)["level"].as_u64().unwrap_or(SAY_DEFAULT as u64) as u8;
    let saved = read_json(&cfg.audio_state)
        .and_then(|v| v["muted_from"].as_u64())
        .map(|l| l.clamp(1, 10) as u8);
    let level = match (value, muted) {
        (Some(v), _) if v > 10 => return Err(ApiError::bad_request("value must be 0-10")),
        (Some(v), Some(true)) if v > 0 => {
            return Err(ApiError::bad_request("muted:true with a non-zero value"))
        }
        (Some(v), _) => v,
        (None, Some(true)) => 0,
        (None, Some(false)) if cur > 0 => cur,
        (None, Some(false)) => saved.unwrap_or(UNMUTE_DEFAULT),
        (None, None) => return Err(ApiError::bad_request("give value (0-10) or muted")),
    };
    if level == 0 && cur > 0 {
        merge_json(&cfg.audio_state, json!({"muted_from": cur}))?;
    }
    write_atomic(&cfg.say_level, &format!("{level}\n"))?;
    // The shell and s22-buttons keep the same number as the saved level.
    let _ = write_atomic(&cfg.volume_file, &format!("{level}\n"));
    Ok(volume(cfg))
}

// ---------------------------------------------------------------- vibration and haptics

pub fn vibration(cfg: &Config) -> Value {
    // s22-haptic's rule: a missing file means enabled, anything but a boolean means disabled.
    let enabled = match std::fs::read_to_string(&cfg.haptics_config) {
        Err(_) => true,
        Ok(t) => serde_json::from_str::<Value>(&t)
            .ok()
            .and_then(|v| v["enabled"].as_bool())
            .unwrap_or(false),
    };
    json!({"enabled": enabled, "daemon": cfg.haptic_sock.exists()})
}

pub fn set_vibration(cfg: &Config, on: bool) -> ApiResult {
    merge_json(&cfg.haptics_config, json!({"enabled": on}))?;
    Ok(vibration(cfg))
}

/// `tick`, `buzz`, `stop`, or `ON,OFF,ON,...` milliseconds (at most 16 numbers, each 1-3000).
pub fn haptic_command(pattern: &str) -> Result<String, ApiError> {
    let p = pattern.trim();
    if matches!(p, "tick" | "buzz" | "stop") {
        return Ok(p.to_string());
    }
    let parts: Vec<&str> = p.split(',').map(str::trim).collect();
    let ok = !p.is_empty()
        && parts.len() <= 16
        && parts
            .iter()
            .all(|x| x.parse::<u32>().is_ok_and(|n| (1..=3000).contains(&n)));
    if ok {
        Ok(format!("pattern {}", parts.join(",")))
    } else {
        Err(ApiError::bad_request(
            "pattern must be tick, buzz, stop or ON,OFF,... milliseconds (max 16 numbers, each 1-3000)",
        ))
    }
}

/// One datagram to s22-haptic from a bound (abstract) socket, so the daemon answers
/// `ok`, `dropped` (rate limit or disabled) or `error ...`.
pub fn haptic_send(cfg: &Config, cmd: &str) -> Result<String, ApiError> {
    use std::os::linux::net::SocketAddrExt;
    use std::os::unix::net::{SocketAddr, UnixDatagram};
    if !cfg.haptic_sock.exists() {
        return Err(ApiError::unavailable(format!(
            "s22-haptic daemon not running ({} missing)",
            cfg.haptic_sock.display()
        )));
    }
    let name = format!(
        "s22d-haptic-{}-{}",
        std::process::id(),
        (now_s() * 1e6) as u64
    );
    let addr = SocketAddr::from_abstract_name(name.as_bytes())
        .map_err(|e| ApiError::internal(e.to_string()))?;
    let s = UnixDatagram::bind_addr(&addr).map_err(|e| ApiError::internal(e.to_string()))?;
    let _ = s.set_read_timeout(Some(Duration::from_millis(1500)));
    s.send_to(cmd.as_bytes(), &cfg.haptic_sock)
        .map_err(|e| ApiError::unavailable(format!("s22-haptic: {e}")))?;
    let mut buf = [0u8; 1024];
    let n = s
        .recv(&mut buf)
        .map_err(|e| ApiError::timeout(format!("s22-haptic did not answer: {e}")))?;
    Ok(String::from_utf8_lossy(&buf[..n]).trim().to_string())
}

// ---------------------------------------------------------------- autosleep and screen timeout

pub fn autosleep(cfg: &Config) -> Value {
    let st = read_json(&cfg.autosleep_status);
    json!({
        "enabled": !cfg.autosleep_flag.exists(),
        "state": st.as_ref().and_then(|v| v["state"].as_str().map(String::from)),
        "cycles": st.as_ref().and_then(|v| v["cycles"].as_u64()),
    })
}

/// The same flag file `s22-autosleep on|off` uses; the daemon obeys within one poll (2 s).
pub fn set_autosleep(cfg: &Config, on: bool) -> ApiResult {
    if on {
        match std::fs::remove_file(&cfg.autosleep_flag) {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => return Err(ApiError::unavailable(format!("autosleep flag: {e}"))),
        }
    } else {
        write_atomic(&cfg.autosleep_flag, "disabled by s22d\n")?;
    }
    Ok(autosleep(cfg))
}

pub const SCREEN_DEFAULT: (u64, u64) = (60, 15);

pub fn screen_timeout(cfg: &Config) -> Value {
    let v = read_json(&cfg.screen_timeout).unwrap_or(Value::Null);
    json!({
        "unlocked_s": v["unlocked_s"].as_u64().unwrap_or(SCREEN_DEFAULT.0),
        "locked_s": v["locked_s"].as_u64().unwrap_or(SCREEN_DEFAULT.1),
        "never": 0,
    })
}

/// unum-fastshell re-reads the file every 10 s while the screen is on.
pub fn set_screen_timeout(cfg: &Config, unlocked: Option<u64>, locked: Option<u64>) -> ApiResult {
    if unlocked.is_none() && locked.is_none() {
        return Err(ApiError::bad_request(
            "give value (unlocked seconds) and/or locked",
        ));
    }
    if unlocked.into_iter().chain(locked).any(|s| s > 3600) {
        return Err(ApiError::bad_request("seconds must be 0-3600 (0 = never)"));
    }
    let cur = screen_timeout(cfg);
    let u = unlocked.unwrap_or(cur["unlocked_s"].as_u64().unwrap_or(SCREEN_DEFAULT.0));
    let l = locked.unwrap_or(cur["locked_s"].as_u64().unwrap_or(SCREEN_DEFAULT.1));
    // The shell's own format (tools/unum-fastshell/src/idle.rs).
    write_atomic(
        &cfg.screen_timeout,
        &format!("{{\"unlocked_s\":{u},\"locked_s\":{l}}}\n"),
    )?;
    Ok(screen_timeout(cfg))
}

// ---------------------------------------------------------------- calls, SMS and the allowlist

/// Same rule as s22-phoned's `normalize_number`: drop spaces, '-', '.', parentheses; 00 -> +.
pub fn normalize_number(n: &str) -> String {
    let s: String = n
        .chars()
        .filter(|c| !c.is_whitespace() && !"-().".contains(*c))
        .collect();
    match s.strip_prefix("00") {
        Some(rest) if s.len() > 4 => format!("+{rest}"),
        _ => s,
    }
}

/// `{"numbers": ["+30...", {"number": "+30...", "name": "..."}]}` (or a bare array).
pub fn allowlist(cfg: &Config) -> Vec<String> {
    let v = read_json(&cfg.allowlist).unwrap_or(Value::Null);
    let rows = v
        .get("numbers")
        .and_then(Value::as_array)
        .or_else(|| v.as_array())
        .cloned()
        .unwrap_or_default();
    rows.iter()
        .filter_map(|r| r.as_str().or_else(|| r["number"].as_str()))
        .map(normalize_number)
        .filter(|n| n.chars().filter(char::is_ascii_digit).count() >= 3)
        .collect()
}

pub fn allowlisted(cfg: &Config, number: &str) -> bool {
    let n = normalize_number(number);
    allowlist(cfg).contains(&n)
}

pub fn calls_enabled(cfg: &Config) -> bool {
    match read_json(&cfg.calls_flag) {
        None => !cfg.calls_flag.exists(),
        Some(v) => v["enabled"].as_bool().unwrap_or(false),
    }
}

pub fn set_calls(cfg: &Config, on: bool) -> Result<bool, ApiError> {
    merge_json(&cfg.calls_flag, json!({"enabled": on}))?;
    Ok(calls_enabled(cfg))
}

/// A one-shot permission for s22-phoned: s22d's policy (allowlist or the owner's yes) passed for
/// this number and kind. phoned accepts it once, within `GRANT_TTL_S`, instead of its own
/// allowlist; `tx_enabled`, the emergency-number refusal and the rate limits still apply.
pub const GRANT_TTL_S: f64 = 120.0;

pub fn grant(cfg: &Config, number: &str, kind: &str) -> Result<(), ApiError> {
    let n = normalize_number(number);
    if !n
        .strip_prefix('+')
        .unwrap_or(&n)
        .chars()
        .all(|c| c.is_ascii_digit())
    {
        return Err(ApiError::bad_request("number must be digits"));
    }
    std::fs::create_dir_all(&cfg.phoned_grants)
        .map_err(|e| ApiError::unavailable(format!("grant dir: {e}")))?;
    {
        use std::os::unix::fs::PermissionsExt;
        let _ =
            std::fs::set_permissions(&cfg.phoned_grants, std::fs::Permissions::from_mode(0o700));
    }
    write_atomic(
        &cfg.phoned_grants.join(format!("{n}.{kind}")),
        &json!({"number": n, "kind": kind, "expires": now_s() + GRANT_TTL_S}).to_string(),
    )
}

// ---------------------------------------------------------------- Bluetooth (s22-bt)

fn bt_run(cfg: &Config, arg: &str, timeout: Duration) -> Result<super::runner::Output, ApiError> {
    if !cfg.bt_tool.exists() {
        return Err(ApiError::unavailable(format!(
            "s22-bt missing: {}",
            cfg.bt_tool.display()
        )));
    }
    SysRunner
        .run(&cfg.bt_tool.to_string_lossy(), &[arg.to_string()], timeout)
        .map_err(|e| ApiError::unavailable(format!("s22-bt: {e}")))
}

pub fn bt_status(cfg: &Config) -> ApiResult {
    let o = bt_run(cfg, "status", Duration::from_secs(10))?;
    let first = o.stdout.lines().next().unwrap_or("").trim().to_string();
    Ok(json!({
        "on": first.starts_with("up"),
        "hci0": cfg.bt_hci.exists(),
        "detail": first,
    }))
}

/// `s22-bt up` waits for Wi-Fi/CNSS (the radios share a power rail) and for hci0: up to ~2 min.
pub fn bt_set(cfg: &Config, on: bool) -> ApiResult {
    let o = bt_run(
        cfg,
        if on { "up" } else { "down" },
        Duration::from_secs(130),
    )?;
    if !o.success() {
        let why = format!("{} {}", o.stderr.trim(), o.stdout.trim());
        let tail: String = why
            .trim()
            .chars()
            .rev()
            .take(300)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect();
        return Err(if o.timed_out {
            ApiError::timeout(format!("s22-bt timed out: {tail}"))
        } else {
            ApiError::upstream(format!("s22-bt {}: {tail}", if on { "up" } else { "down" }))
        });
    }
    bt_status(cfg)
}

// ---------------------------------------------------------------- network policy

/// s22-netpolicy's last tick: the active uplink and the mobile mode it obeyed.
pub fn netpolicy(cfg: &Config) -> Value {
    let st = read_json(&cfg.netpolicy_status).unwrap_or(Value::Null);
    let mode = read_json(&cfg.netpolicy_config)
        .and_then(|v| v["mobile_mode"].as_str().map(String::from))
        .or_else(|| st["mobile_mode"].as_str().map(String::from))
        .unwrap_or_else(|| "failover".into());
    json!({
        "uplink": st["uplink"],
        "mobile_mode": mode,
        "status_age_s": st["t"].as_f64().map(|t| (now_s() - t).round()),
    })
}

pub fn set_mobile_mode(cfg: &Config, mode: &str) -> Result<(), ApiError> {
    merge_json(&cfg.netpolicy_config, json!({"mobile_mode": mode})).map(|_| ())
}

const SAVER_TAG: &str = "s22d wifi.set off";

/// Wi-Fi switched off by the owner through s22d (until wifi.set on or a reboot).
pub fn wifi_user_off(cfg: &Config) -> bool {
    cfg.wifi_off_marker.exists()
}

pub fn saver_parked(cfg: &Config) -> bool {
    read_json(&cfg.saver_state).is_some_and(|v| v["parked"] == true)
}

/// Before Wi-Fi goes off: mark it, and hold netpolicy's saver kill switch (unless the owner already
/// set it) so the saver neither parks nor, worse, unparks (= reconnects) Wi-Fi behind our back.
pub fn wifi_off_begin(cfg: &Config) -> Result<(), ApiError> {
    write_atomic(&cfg.wifi_off_marker, "off\n")?;
    if !cfg.saver_off.exists() {
        write_atomic(&cfg.saver_off, &format!("{SAVER_TAG}\n"))?;
    }
    Ok(())
}

/// Wi-Fi on again (or s22d starting after a reboot): drop the marker and our kill switch only.
pub fn wifi_off_end(cfg: &Config) {
    let _ = std::fs::remove_file(&cfg.wifi_off_marker);
    if read_trim(&cfg.saver_off).as_deref() == Some(SAVER_TAG) {
        let _ = std::fs::remove_file(&cfg.saver_off);
    }
}

/// At start: a kill switch s22d left behind without its tmpfs marker is from before a reboot.
pub fn wifi_off_cleanup_at_start(cfg: &Config) {
    if !wifi_user_off(cfg) {
        wifi_off_end(cfg);
    }
}

pub fn usb_carrier(cfg: &Config) -> bool {
    read_trim(&cfg.usb_carrier).as_deref() == Some("1")
}

/// Status-bar radio badge from s22-phoned's /status, the same rule as unum-fastshell's
/// `net::radio_badge`: "5G" while an NR leg is reported (NSA keeps act "lte"), else 4G/3G/2G.
pub fn radio_badge(v: &Value, now: f64) -> Option<&'static str> {
    if v["ok"] == false {
        return None;
    }
    let reg = &v["registration"];
    let rs = reg["reg_status"].as_str().unwrap_or("");
    if !(rs.starts_with("home") || rs.starts_with("roam")) {
        return None;
    }
    let nr_recent = v["nr_last_seen"]
        .as_f64()
        .is_some_and(|t| (now - t).abs() < 120.0);
    match reg["act"].as_str().unwrap_or("") {
        "nr" | "5g" | "nr5g" => Some("5G"),
        "lte" | "lte_ca" | "4g" => Some(if v["radio"]["nr_nsa"] == true || nr_recent {
            "5G"
        } else {
            "4G"
        }),
        "umts" | "wcdma" | "hspa" | "3g" => Some("3G"),
        "gsm" | "gprs" | "edge" | "2g" => Some("2G"),
        _ => None,
    }
}

// ---------------------------------------------------------------- screenshot (unum-fastshell)

fn newest_screenshot(dir: &Path) -> Option<(String, SystemTime)> {
    std::fs::read_dir(dir)
        .ok()?
        .flatten()
        .filter_map(|e| {
            let name = e.file_name().to_string_lossy().into_owned();
            (name.starts_with("screenshot-") && name.ends_with(".png"))
                .then(|| Some((name, e.metadata().ok()?.modified().ok()?)))
                .flatten()
        })
        .max_by_key(|(_, t)| *t)
}

/// Ask the shell to take the same screenshot as Power + Volume down, then find the new file.
pub fn screenshot(cfg: &Config, wait: Duration) -> ApiResult {
    if !cfg.shell_sock.exists() {
        return Err(ApiError::unavailable("unum-fastshell is not running"));
    }
    let before = cfg
        .gallery_dir
        .as_deref()
        .and_then(newest_screenshot)
        .map(|(n, _)| n);
    let r = touch::request(
        &cfg.shell_sock,
        &json!({"fn": "screenshot", "args": []}),
        Duration::from_secs(10),
    )
    .map_err(|e| ApiError::unavailable(format!("shell: {e}")))?;
    let result = r["result"].as_str().unwrap_or("").to_string();
    if r["ok"] != true || result.starts_with("unknown function") {
        return Err(ApiError::unavailable(
            "the running shell has no screenshot hook (deploy a newer unum-fastshell)",
        ));
    }
    if result != "ok" {
        return Err(ApiError::busy("screen_off", result));
    }
    let until = std::time::Instant::now() + wait;
    loop {
        let now = cfg.gallery_dir.as_deref().and_then(newest_screenshot);
        if let Some((name, _)) = now.filter(|(n, _)| Some(n) != before.as_ref()) {
            let dir = cfg.gallery_dir.as_deref().unwrap_or(Path::new(""));
            return Ok(json!({"taken": true, "path": dir.join(&name), "file": name}));
        }
        if std::time::Instant::now() >= until {
            return Ok(
                json!({"taken": true, "path": null, "note": "requested; the file did not appear yet"}),
            );
        }
        std::thread::sleep(Duration::from_millis(150));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::Tmp;

    fn cfg(t: &Tmp) -> Config {
        Config::rooted(t.path())
    }

    #[test]
    fn volume_mute_and_unmute_restore_the_level() {
        let t = Tmp::new();
        let c = cfg(&t);
        assert_eq!(volume(&c)["level"], SAY_DEFAULT);
        assert_eq!(volume(&c)["source"], "default");
        assert_eq!(set_volume(&c, Some(7), None).unwrap()["level"], 7);
        assert_eq!(std::fs::read_to_string(&c.volume_file).unwrap(), "7\n");
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&c.say_level)
                .unwrap()
                .permissions()
                .mode();
            assert_eq!(mode & 0o777, 0o644);
        }
        let v = set_volume(&c, None, Some(true)).unwrap();
        assert_eq!(
            (v["level"].clone(), v["muted"].clone()),
            (json!(0), json!(true))
        );
        assert_eq!(set_volume(&c, None, Some(false)).unwrap()["level"], 7);
        assert_eq!(
            set_volume(&c, Some(11), None).unwrap_err().code,
            "bad_request"
        );
        assert_eq!(set_volume(&c, None, None).unwrap_err().code, "bad_request");
        assert_eq!(
            set_volume(&c, Some(3), Some(true)).unwrap_err().code,
            "bad_request"
        );
    }

    #[test]
    fn vibration_flag_keeps_other_keys() {
        let t = Tmp::new();
        let c = cfg(&t);
        assert_eq!(vibration(&c)["enabled"], true, "missing file = enabled");
        t.write(
            "srv/s22/state/haptics/config.json",
            r#"{"enabled": true, "tick_index": 39}"#,
        );
        assert_eq!(set_vibration(&c, false).unwrap()["enabled"], false);
        let v = read_json(&c.haptics_config).unwrap();
        assert_eq!(
            (v["enabled"].clone(), v["tick_index"].clone()),
            (json!(false), json!(39))
        );
        t.write("srv/s22/state/haptics/config.json", r#"{"enabled": "yes"}"#);
        assert_eq!(
            vibration(&c)["enabled"],
            false,
            "non-boolean = disabled, like s22-haptic"
        );
    }

    #[test]
    fn haptic_patterns_are_validated() {
        assert_eq!(haptic_command("tick").unwrap(), "tick");
        assert_eq!(haptic_command(" 40, 80,40 ").unwrap(), "pattern 40,80,40");
        for bad in [
            "",
            "boom",
            "0,10",
            "5000",
            "1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17",
            "40;rm",
        ] {
            assert!(haptic_command(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn haptic_send_talks_to_the_daemon_socket() {
        use std::os::unix::net::UnixDatagram;
        let t = Tmp::new();
        let c = cfg(&t);
        assert_eq!(haptic_send(&c, "tick").unwrap_err().code, "unavailable");
        std::fs::create_dir_all(c.haptic_sock.parent().unwrap()).unwrap();
        let d = UnixDatagram::bind(&c.haptic_sock).unwrap();
        let th = std::thread::spawn(move || {
            let mut b = [0u8; 64];
            let (n, from) = d.recv_from(&mut b).unwrap();
            assert_eq!(&b[..n], b"pattern 40,80");
            d.send_to_addr(b"ok", &from).unwrap();
        });
        assert_eq!(haptic_send(&c, "pattern 40,80").unwrap(), "ok");
        th.join().unwrap();
    }

    #[test]
    fn autosleep_and_screen_timeout_round_trip() {
        let t = Tmp::new();
        let c = cfg(&t);
        assert_eq!(set_autosleep(&c, false).unwrap()["enabled"], false);
        assert!(c.autosleep_flag.exists());
        assert_eq!(set_autosleep(&c, true).unwrap()["enabled"], true);
        assert_eq!(
            set_autosleep(&c, true).unwrap()["enabled"],
            true,
            "idempotent"
        );
        assert_eq!(screen_timeout(&c)["unlocked_s"], 60);
        let v = set_screen_timeout(&c, Some(120), None).unwrap();
        assert_eq!(
            (v["unlocked_s"].clone(), v["locked_s"].clone()),
            (json!(120), json!(15))
        );
        assert_eq!(
            std::fs::read_to_string(&c.screen_timeout).unwrap(),
            "{\"unlocked_s\":120,\"locked_s\":15}\n"
        );
        assert_eq!(
            set_screen_timeout(&c, Some(5000), None).unwrap_err().code,
            "bad_request"
        );
        assert_eq!(
            set_screen_timeout(&c, None, Some(5000)).unwrap_err().code,
            "bad_request"
        );
    }

    #[test]
    fn numbers_normalize_like_phoned_and_allowlist_accepts_both_forms() {
        assert_eq!(normalize_number("+30 690-000.0000"), "+306900000000");
        assert_eq!(normalize_number("00306900000000"), "+306900000000");
        assert_eq!(normalize_number("(210) 1234567"), "2101234567");
        let t = Tmp::new();
        let c = cfg(&t);
        assert!(
            !allowlisted(&c, "+306900000000"),
            "no file: nothing allowlisted"
        );
        t.write(
            "srv/s22/state/phone/allowlist.json",
            r#"{"numbers": ["+30 690 000 0000", {"number": "0030211", "name": "x"}, "+3000000000X0"]}"#,
        );
        assert!(allowlisted(&c, "0030 6900000000"));
        assert!(allowlisted(&c, "+30211"));
        assert!(!allowlisted(&c, "+306900000001"));
    }

    #[test]
    fn calls_gate_and_grants() {
        let t = Tmp::new();
        let c = cfg(&t);
        assert!(calls_enabled(&c), "missing file = enabled");
        assert!(!set_calls(&c, false).unwrap());
        assert!(set_calls(&c, true).unwrap());
        t.write("srv/s22/state/phone/calls.json", "garbage");
        assert!(!calls_enabled(&c), "unreadable gate = disabled");
        grant(&c, "+30 690 000 0000", "sms").unwrap();
        let g = read_json(&c.phoned_grants.join("+306900000000.sms")).unwrap();
        assert_eq!(g["kind"], "sms");
        assert!(g["expires"].as_f64().unwrap() > now_s());
        assert!(grant(&c, "+30*", "dial").is_err());
    }

    #[test]
    fn bluetooth_wraps_s22_bt() {
        let t = Tmp::new();
        let c = cfg(&t);
        assert_eq!(bt_status(&c).unwrap_err().code, "unavailable");
        let state = t.path().join("bt-state");
        t.script(
            "usr/local/bin/s22-bt",
            &format!(
                "S={s}\ncase \"$1\" in up) echo up > $S;; down) echo down > $S;; status) cat $S 2>/dev/null || echo down;; esac",
                s = state.display()
            ),
        );
        assert_eq!(bt_status(&c).unwrap()["on"], false);
        assert_eq!(bt_set(&c, true).unwrap()["on"], true);
        assert_eq!(bt_set(&c, false).unwrap()["on"], false);
        t.script(
            "usr/local/bin/s22-bt",
            "echo 'prerequisites not ready' >&2; exit 1",
        );
        let e = bt_set(&c, true).unwrap_err();
        assert_eq!(e.code, "upstream_error");
        assert!(e.message.contains("prerequisites"));
    }

    #[test]
    fn netpolicy_mode_comes_from_config_then_status() {
        let t = Tmp::new();
        let c = cfg(&t);
        assert_eq!(netpolicy(&c)["mobile_mode"], "failover");
        t.write(
            "run/s22-netpolicy/status.json",
            r#"{"uplink": "wifi", "mobile_mode": "always", "t": 1}"#,
        );
        assert_eq!(netpolicy(&c)["mobile_mode"], "always");
        assert_eq!(netpolicy(&c)["uplink"], "wifi");
        t.write("srv/s22/state/netpolicy/config.json", r#"{"poll_s": 5}"#);
        set_mobile_mode(&c, "off").unwrap();
        let cfgv = read_json(&c.netpolicy_config).unwrap();
        assert_eq!(
            (cfgv["mobile_mode"].clone(), cfgv["poll_s"].clone()),
            (json!("off"), json!(5))
        );
        assert_eq!(netpolicy(&c)["mobile_mode"], "off");
        assert!(!usb_carrier(&c));
        t.write("sys/class/net/ecm0/carrier", "1\n");
        assert!(usb_carrier(&c));
    }

    #[test]
    fn wifi_off_holds_only_its_own_saver_kill_switch() {
        let t = Tmp::new();
        let c = cfg(&t);
        wifi_off_begin(&c).unwrap();
        assert!(wifi_user_off(&c) && c.saver_off.exists());
        wifi_off_end(&c);
        assert!(!wifi_user_off(&c) && !c.saver_off.exists());
        // The owner's own kill switch is never removed.
        t.write("srv/s22/state/netpolicy/wifi-saver-off", "owner\n");
        wifi_off_begin(&c).unwrap();
        wifi_off_end(&c);
        assert!(c.saver_off.exists());
        // After a reboot (/run marker gone) a leftover s22d switch is cleaned up at start.
        std::fs::remove_file(&c.saver_off).unwrap();
        wifi_off_begin(&c).unwrap();
        std::fs::remove_file(&c.wifi_off_marker).unwrap();
        wifi_off_cleanup_at_start(&c);
        assert!(!c.saver_off.exists());
        t.write("run/s22-netpolicy/saver.json", r#"{"parked": true}"#);
        assert!(saver_parked(&c));
    }

    #[test]
    fn badge_matches_the_shell() {
        let reg = |act: &str| json!({"ok": true, "registration": {"reg_status": "home_3gpp", "act": act}});
        assert_eq!(radio_badge(&reg("lte"), 0.0), Some("4G"));
        assert_eq!(radio_badge(&reg("nr"), 0.0), Some("5G"));
        let mut v = reg("lte");
        v["nr_last_seen"] = json!(1000.0);
        assert_eq!(radio_badge(&v, 1050.0), Some("5G"));
        assert_eq!(radio_badge(&v, 2000.0), Some("4G"));
        assert_eq!(radio_badge(&json!({"ok": false}), 0.0), None);
    }

    #[test]
    fn screenshot_uses_the_shell_hook_and_reports_the_file() {
        let t = Tmp::new();
        let mut c = cfg(&t);
        let gallery = t.path().join("gallery");
        std::fs::create_dir_all(&gallery).unwrap();
        c.gallery_dir = Some(gallery.clone());
        assert_eq!(
            screenshot(&c, Duration::ZERO).unwrap_err().code,
            "unavailable"
        );
        let g2 = gallery.clone();
        let _rx = touch::fake::serve(&c.shell_sock, move |req| {
            assert_eq!(req["fn"], "screenshot");
            std::fs::write(g2.join("screenshot-1.png"), b"png").unwrap();
            json!({"ok": true, "result": "ok"})
        });
        let v = screenshot(&c, Duration::from_secs(2)).unwrap();
        assert_eq!(v["file"], "screenshot-1.png");
    }

    #[test]
    fn screenshot_on_an_old_shell_is_unavailable() {
        let t = Tmp::new();
        let c = cfg(&t);
        let _rx = touch::fake::serve(
            &c.shell_sock,
            |_| json!({"ok": true, "result": "unknown function: screenshot"}),
        );
        let e = screenshot(&c, Duration::ZERO).unwrap_err();
        assert_eq!(e.code, "unavailable");
        assert!(e.message.contains("fastshell"));
    }
}
