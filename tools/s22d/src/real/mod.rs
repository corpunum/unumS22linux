//! Phone implementations of the backend traits.

pub mod audio;
pub mod camera;
pub mod confirm;
pub mod display;
pub mod httpc;
pub mod phoned;
pub mod recovery;
pub mod runner;
pub mod services;
pub mod sys;
pub mod touch;
pub mod wifi;

use crate::backends::Backends;
use crate::config::Config;
use std::sync::Arc;

pub fn backends(cfg: &Arc<Config>) -> Backends {
    let runner: Arc<dyn runner::Runner> = Arc::new(runner::SysRunner);
    Backends {
        telemetry: Arc::new(sys::SysTelemetry::new(cfg.clone())),
        wifi: Arc::new(wifi::WpaCli::new(cfg.clone(), runner.clone())),
        phoned: Arc::new(phoned::PhonedProxy::new(cfg.clone())),
        camera: Arc::new(camera::CameraClient::new(cfg.clone(), runner.clone())),
        display: Arc::new(display::DisplayTool::new(cfg.clone(), runner.clone())),
        services: Arc::new(services::KeepaliveServices::new(cfg.clone(), runner)),
        recovery: Arc::new(recovery::RebootRecovery::new(cfg.clone())),
        audio: Arc::new(audio::VolumeFile::new(cfg.clone())),
        confirmer: Arc::new(confirm::TouchConfirmer::new(cfg.clone())),
    }
}
