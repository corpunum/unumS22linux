//! s22d: the Galaxy S22's local device daemon. See `API.md` for the contract.

mod app;
mod backends;
mod catalog;
mod config;
mod device;
mod error;
mod real;
mod remote;
mod routes;
#[cfg(test)]
mod tests;
#[cfg(test)]
mod testutil;

use app::{App, Audit};
use axum::extract::connect_info::IntoMakeServiceWithConnectInfo;
use routes::{router, Transport};
use std::net::SocketAddr;
use std::sync::Arc;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let arg = std::env::args().nth(1);
    match arg.as_deref() {
        Some("--print-capabilities") => {
            let v = catalog::capabilities(&catalog::static_availability);
            println!("{}", serde_json::to_string_pretty(&v)?);
            return Ok(());
        }
        Some("--print-device-capabilities") => {
            let v = device::capabilities(&|_| None);
            println!("{}", serde_json::to_string_pretty(&v)?);
            return Ok(());
        }
        Some("--version") => {
            println!("s22d {}", env!("CARGO_PKG_VERSION"));
            return Ok(());
        }
        Some(other) => {
            eprintln!("usage: s22d [--print-capabilities | --print-device-capabilities | --version]  (unknown: {other})");
            std::process::exit(2);
        }
        None => {}
    }
    // keepalive starts services with a bare environment; helpers (wpa_cli, python3) live in sbin too.
    std::env::set_var(
        "PATH",
        "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    );
    // One worker thread keeps the resident set small; blocking backend calls get a small pool.
    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .max_blocking_threads(8)
        .build()?;
    rt.block_on(serve())
}

async fn serve() -> Result<(), Box<dyn std::error::Error>> {
    let cfg = Arc::new(config::Config::from_env());
    // No audit log, no daemon: state-changing requests must always leave a record.
    let audit = Audit::open_capped(&cfg.audit_path, cfg.audit_max_bytes)?;
    audit.record(
        "s22d.start",
        "read",
        "ok",
        serde_json::json!({"version": env!("CARGO_PKG_VERSION")}),
    );
    real::settings::wifi_off_cleanup_at_start(&cfg);
    let app = App::new(cfg.clone(), real::backends(&cfg), audit);
    // Internet sharing left on by a reboot or a dead watchdog is undone at start (s22-uplink-share sweep).
    if cfg.uplink_tool.exists() {
        let c = cfg.clone();
        std::thread::spawn(move || {
            let _ = device::uplink(&c, "sweep");
        });
    }

    let tcp = tokio::net::TcpListener::bind(&cfg.tcp_addr).await?;
    let tcp_app = router(app.clone(), Transport::Tcp);
    let tcp_task = tokio::spawn(async move {
        let svc: IntoMakeServiceWithConnectInfo<_, SocketAddr> =
            tcp_app.into_make_service_with_connect_info::<SocketAddr>();
        axum::serve(tcp, svc).await
    });

    let _ = std::fs::remove_file(&cfg.socket_path);
    let unix = tokio::net::UnixListener::bind(&cfg.socket_path)?;
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&cfg.socket_path, std::fs::Permissions::from_mode(0o600))?;
    }
    let remote_app = app.clone();
    let unix_task =
        tokio::spawn(async move { axum::serve(unix, router(app, Transport::Unix)).await });

    // The remote listener (remote.rs): only when its config exists. A bad config is reported and the
    // local listeners keep serving; binding is retried because tailscale0 may come up after s22d.
    match remote::RemoteConfig::load(&cfg.remote_config) {
        Ok(None) => {}
        Err(e) => remote_app.audit.record(
            "remote.config",
            "read",
            "error",
            serde_json::json!({"error": e}),
        ),
        Ok(Some(rc)) => {
            let st = remote::RemoteState {
                app: remote_app.clone(),
                cfg: Arc::new(rc),
                whois: remote::tailscale_whois,
            };
            tokio::spawn(async move {
                loop {
                    match tokio::net::TcpListener::bind(st.cfg.bind).await {
                        Ok(l) => {
                            st.app.audit.record("remote.listen", "read", "ok", serde_json::json!({"bind": st.cfg.bind.to_string(), "peers": st.cfg.peers.len()}));
                            let svc = remote::router(st.clone())
                                .into_make_service_with_connect_info::<SocketAddr>();
                            let _ = axum::serve(l, svc).await;
                        }
                        Err(_) => tokio::time::sleep(std::time::Duration::from_secs(30)).await,
                    }
                }
            });
        }
    }

    tokio::select! {
        r = tcp_task => r??,
        r = unix_task => r??,
    }
    Ok(())
}
