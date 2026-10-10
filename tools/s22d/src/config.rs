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
}

impl Config {
    /// Phone defaults with every path below `root`.
    pub fn rooted(root: &Path) -> Self {
        let p = |rel: &str| root.join(rel);
        Self {
            root: root.to_path_buf(),
            audit_path: p("srv/s22/state/s22d/audit.jsonl"),
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
