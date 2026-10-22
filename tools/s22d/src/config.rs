//! Every path s22d touches, in one place. `Config::rooted` prefixes all of them with a root
//! directory so tests can run against a fixture tree instead of a phone.

use std::path::{Path, PathBuf};
use std::time::Duration;

#[derive(Debug, Clone)]
pub struct Config {
    pub root: PathBuf,
    pub audit_path: PathBuf,
    pub tcp_addr: String,
    pub socket_path: PathBuf,
    pub audio_flag: PathBuf,
    pub volume_file: PathBuf,
    pub display_state: PathBuf,
    pub backlight_dir: PathBuf,
    pub keepalive_enabled: PathBuf,
    pub wpa_cli: String,
    pub wpa_ctrl_dir: PathBuf,
    pub wifi_iface: String,
    pub phoned_sock: PathBuf,
    pub phoned_tcp: String,
    pub python: String,
    pub python_args: Vec<String>,
    pub camera_client: PathBuf,
    pub develop_tool: PathBuf,
    pub camera_dir: PathBuf,
    /// OpenUnum gallery (`<home>/assets/generated`); each successful capture is copied there.
    /// Skipped unless the OpenUnum home exists; `S22D_GALLERY_DIR=""` turns it off.
    pub gallery_dir: Option<PathBuf>,
    pub camera_keep: usize,
    pub camera_min_interval: Duration,
    pub camera_failure_cooldown: Duration,
    pub camera_deadline_s: u64,
    pub camera_allow_fw_stall: bool,
    pub display_tool: PathBuf,
    pub touch_sock: PathBuf,
    pub confirm_dir: PathBuf,
    pub confirm_timeout_s: u64,
    pub modem_up: PathBuf,
    pub reboot_tool: PathBuf,
    pub reboot_delay_s: u64,
    // ---- device API (`/v1/device`, src/device.rs)
    /// The audit file is renamed to `<name>.1` once it grows past this size.
    pub audit_max_bytes: u64,
    /// s22-say's speech level 0-10 (tmpfs; the shell writes it too).
    pub say_level: PathBuf,
    /// Pre-mute level, so unmute restores it.
    pub audio_state: PathBuf,
    pub haptics_config: PathBuf,
    pub haptic_sock: PathBuf,
    pub autosleep_flag: PathBuf,
    pub autosleep_status: PathBuf,
    pub screen_timeout: PathBuf,
    /// Private call/SMS allowlist (`{"numbers": [...]}`); lives only on the phone.
    pub allowlist: PathBuf,
    /// `{"enabled": bool}` gate for call.dial (missing = enabled).
    pub calls_flag: PathBuf,
    /// One-shot grants s22d leaves for s22-phoned after its own policy passed.
    pub phoned_grants: PathBuf,
    pub bt_tool: PathBuf,
    pub bt_hci: PathBuf,
    pub netpolicy_status: PathBuf,
    pub netpolicy_config: PathBuf,
    pub usb_carrier: PathBuf,
    /// s22-netpolicy's Wi-Fi saver kill switch and state (issue #71): while the owner has Wi-Fi
    /// off, s22d holds the kill switch so the saver never "unparks" (reconnects) Wi-Fi.
    pub saver_off: PathBuf,
    pub saver_state: PathBuf,
    /// tmpfs marker: Wi-Fi was switched off through s22d (gone after a reboot).
    pub wifi_off_marker: PathBuf,
    /// unum-fastshell's control socket (screenshot).
    pub shell_sock: PathBuf,
    /// How long wifi.set on waits for an association before answering.
    pub wifi_wait: Duration,
}

impl Config {
    /// Phone defaults with every path below `root`.
    pub fn rooted(root: &Path) -> Self {
        let p = |rel: &str| root.join(rel);
        Self {
            root: root.to_path_buf(),
            audit_path: p("srv/s22/state/audit/s22d.jsonl"),
            tcp_addr: "127.0.0.1:8766".into(),
            socket_path: p("run/s22d.sock"),
            audio_flag: p("etc/s22-audio-unmuted"),
            volume_file: p("srv/s22/buttons/volume"),
            display_state: p("run/s22-display-state"),
            backlight_dir: p("sys/class/backlight/panel"),
            keepalive_enabled: p("srv/s22/state/keepalive/enabled"),
            wpa_cli: "wpa_cli".into(),
            wpa_ctrl_dir: p("run/wpa_supplicant-s22"),
            wifi_iface: "wlan0".into(),
            phoned_sock: p("run/s22-phoned.sock"),
            phoned_tcp: "127.0.0.1:8095".into(),
            python: "python3".into(),
            python_args: vec!["-I".into(), "-B".into()],
            camera_client: p("srv/s22/hardware/bin/s22-camera"),
            develop_tool: p("srv/s22/hardware/bin/s22-develop"),
            camera_dir: p("srv/s22/state/s22d/camera"),
            gallery_dir: Some(p("mnt/omarchy-trial/root/.openunum/assets/generated")),
            camera_keep: 8,
            camera_min_interval: Duration::from_secs(10),
            camera_failure_cooldown: Duration::from_secs(60),
            camera_deadline_s: 120,
            camera_allow_fw_stall: true,
            display_tool: p("srv/s22/hardware/bin/s22-display"),
            touch_sock: p("mnt/omarchy-trial/run/s22-touch/ctl.sock"),
            confirm_dir: p("mnt/omarchy-trial/run/s22-touch/confirm"),
            confirm_timeout_s: 60,
            modem_up: p("srv/s22/hardware/bin/s22-modem-up"),
            reboot_tool: p("usr/local/sbin/s22-reboot"),
            reboot_delay_s: 3,
            audit_max_bytes: 1 << 20,
            say_level: p("run/s22-say.level"),
            audio_state: p("srv/s22/state/audio/volume.json"),
            haptics_config: p("srv/s22/state/haptics/config.json"),
            haptic_sock: p("run/s22-haptic.sock"),
            autosleep_flag: p("srv/s22/state/autosleep/disabled"),
            autosleep_status: p("run/s22-autosleep/status.json"),
            screen_timeout: p("srv/s22/state/fastshell/screen-timeout.json"),
            allowlist: p("srv/s22/state/phone/allowlist.json"),
            calls_flag: p("srv/s22/state/phone/calls.json"),
            phoned_grants: p("srv/s22/state/phoned/grants"),
            bt_tool: p("usr/local/bin/s22-bt"),
            bt_hci: p("sys/class/bluetooth/hci0"),
            netpolicy_status: p("run/s22-netpolicy/status.json"),
            netpolicy_config: p("srv/s22/state/netpolicy/config.json"),
            usb_carrier: p("sys/class/net/ecm0/carrier"),
            saver_off: p("srv/s22/state/netpolicy/wifi-saver-off"),
            saver_state: p("run/s22-netpolicy/saver.json"),
            wifi_off_marker: p("run/s22d-wifi-off"),
            shell_sock: p("mnt/omarchy-trial/run/s22-touch/shell.sock"),
            wifi_wait: Duration::from_secs(20),
        }
    }

    /// Phone defaults plus `S22D_*` overrides. Only paths a test or a rig run needs to move.
    pub fn from_env() -> Self {
        let mut c = Self::rooted(Path::new("/"));
        let path = |key: &str, slot: &mut PathBuf| {
            if let Ok(v) = std::env::var(key) {
                *slot = PathBuf::from(v);
            }
        };
        path("S22D_AUDIT", &mut c.audit_path);
        path("S22D_SOCKET", &mut c.socket_path);
        path("S22D_TOUCH_SOCK", &mut c.touch_sock);
        path("S22D_CONFIRM_DIR", &mut c.confirm_dir);
        path("S22D_CAMERA_CLIENT", &mut c.camera_client);
        path("S22D_DEVELOP", &mut c.develop_tool);
        path("S22D_CAMERA_DIR", &mut c.camera_dir);
        if let Ok(v) = std::env::var("S22D_GALLERY_DIR") {
            c.gallery_dir = (!v.is_empty()).then(|| PathBuf::from(v));
        }
        if let Ok(v) = std::env::var("S22D_ADDR") {
            c.tcp_addr = v;
        }
        if let Ok(v) = std::env::var("S22D_CONFIRM_TIMEOUT_S") {
            if let Ok(n) = v.parse::<u64>() {
                c.confirm_timeout_s = n.clamp(5, 600);
            }
        }
        c
    }
}
