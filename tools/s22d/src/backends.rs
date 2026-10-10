//! The hardware seams. Each trait is implemented once for the phone (`real/`) and once as a
//! fake in the route tests, so every endpoint's policy and failure paths run without a phone.
//! The traits are synchronous; the routes call them through `spawn_blocking`.

use crate::error::ApiResult;
use serde::Deserialize;
use std::sync::Arc;

pub trait Telemetry: Send + Sync {
    fn status(&self) -> ApiResult;
    fn battery(&self) -> ApiResult;
    fn thermal(&self) -> ApiResult;
    fn top(&self, sort: TopSort, limit: usize) -> ApiResult;
    /// Kernel ring buffer records with `seq > since`, newest `limit` of them.
    fn dmesg(&self, since: Option<u64>, limit: usize) -> ApiResult;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TopSort {
    Cpu,
    Mem,
}

#[derive(Debug, Clone, Deserialize)]
pub struct WifiConnect {
    pub ssid: String,
    #[serde(default)]
    pub password: Option<String>,
}

pub trait Wifi: Send + Sync {
    /// `None` when usable, otherwise the reason it is not.
    fn availability(&self) -> Option<String>;
    fn status(&self) -> ApiResult;
    fn scan(&self) -> ApiResult;
    fn connect(&self, req: &WifiConnect) -> ApiResult;
    fn disconnect(&self) -> ApiResult;
    fn reconnect(&self) -> ApiResult;
}

/// Proxy to `s22-phoned`. Its allowlist, `tx_enabled` gate and rate limits stay authoritative.
pub trait Phoned: Send + Sync {
    fn availability(&self) -> Option<String>;
    fn modem_status(&self) -> ApiResult;
    fn sms_list(&self, outbox: bool, since: Option<String>, limit: usize) -> ApiResult;
    fn sms_send(&self, number: &str, text: &str) -> ApiResult;
    fn calls(&self) -> ApiResult;
    fn dial(&self, number: &str) -> ApiResult;
    fn hangup(&self) -> ApiResult;
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct CaptureReq {
    pub sensor: String,
    #[serde(default)]
    pub exposure_us: Option<u32>,
    #[serde(default)]
    pub gain: Option<f32>,
    #[serde(default = "yes")]
    pub develop: bool,
}

fn yes() -> bool {
    true
}

pub trait Camera: Send + Sync {
    fn availability(&self) -> Option<String>;
    fn status(&self) -> ApiResult;
    fn capture(&self, req: &CaptureReq) -> ApiResult;
}

pub trait Display: Send + Sync {
    fn availability(&self) -> Option<String>;
    fn status(&self) -> ApiResult;
    fn set_power(&self, on: bool) -> ApiResult;
    fn set_brightness(&self, percent: u8) -> ApiResult;
}

pub trait Services: Send + Sync {
    fn availability(&self) -> Option<String>;
    /// Ask `s22-keepalive` to restart `name` by stopping it; keepalive starts it again.
    fn restart(&self, name: &str) -> ApiResult;
}

pub trait Recovery: Send + Sync {
    fn availability(&self) -> Option<String>;
    /// Schedule `s22-reboot recovery` a few seconds out so the HTTP answer gets through first.
    fn schedule(&self) -> ApiResult;
}

pub trait Audio: Send + Sync {
    fn level(&self) -> Option<u8>;
    fn set_level(&self, level: u8) -> ApiResult;
}

#[derive(Debug, Clone, PartialEq)]
pub enum Answer {
    Yes,
    No,
    Timeout,
    /// Another question is already on screen.
    Busy,
    /// The touch UI cannot show the question (no socket, no session). Treated as a denial.
    Unavailable(String),
}

pub trait Confirmer: Send + Sync {
    fn ask(&self, question: &str, timeout_s: u64) -> Answer;
}

#[derive(Clone)]
pub struct Backends {
    pub telemetry: Arc<dyn Telemetry>,
    pub wifi: Arc<dyn Wifi>,
    pub phoned: Arc<dyn Phoned>,
    pub camera: Arc<dyn Camera>,
    pub display: Arc<dyn Display>,
    pub services: Arc<dyn Services>,
    pub recovery: Arc<dyn Recovery>,
    pub audio: Arc<dyn Audio>,
    pub confirmer: Arc<dyn Confirmer>,
}
