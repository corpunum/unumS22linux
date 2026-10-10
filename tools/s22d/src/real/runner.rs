//! Run a short command with a timeout. A trait so the Wi-Fi, display and service backends can be
//! tested with scripted output.

use std::io::Read;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

#[derive(Debug, Clone, Default)]
pub struct Output {
    pub code: Option<i32>,
    pub stdout: String,
    pub stderr: String,
    pub timed_out: bool,
}

impl Output {
    pub fn success(&self) -> bool {
        self.code == Some(0) && !self.timed_out
    }
}

pub trait Runner: Send + Sync {
    fn run(&self, program: &str, args: &[String], timeout: Duration) -> std::io::Result<Output>;
}

pub struct SysRunner;

fn drain<R: Read + Send + 'static>(mut r: R) -> std::thread::JoinHandle<String> {
    std::thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = r.by_ref().take(1 << 20).read_to_end(&mut buf);
        String::from_utf8_lossy(&buf).into_owned()
    })
}

impl Runner for SysRunner {
    fn run(&self, program: &str, args: &[String], timeout: Duration) -> std::io::Result<Output> {
        let mut child = Command::new(program)
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?;
        let out = child.stdout.take().map(drain);
        let err = child.stderr.take().map(drain);
        let deadline = Instant::now() + timeout;
        let mut timed_out = false;
        let status = loop {
            if let Some(s) = child.try_wait()? {
                break Some(s);
            }
            if Instant::now() >= deadline {
                timed_out = true;
                let _ = child.kill();
                let _ = child.wait();
                break None;
            }
            std::thread::sleep(Duration::from_millis(20));
        };
        Ok(Output {
            code: status.and_then(|s| s.code()),
            stdout: out
                .map(|h| h.join().unwrap_or_default())
                .unwrap_or_default(),
            stderr: err
                .map(|h| h.join().unwrap_or_default())
                .unwrap_or_default(),
            timed_out,
        })
    }
}

#[cfg(test)]
pub mod fake {
    use super::*;
    use std::collections::VecDeque;
    use std::sync::Mutex;

    /// Scripted runner. The first rule whose key is contained in `program args...` answers.
    /// A rule with several answers plays them in order and repeats the last one.
    pub struct FakeRunner {
        rules: Vec<(String, Mutex<VecDeque<Output>>)>,
        calls: Mutex<Vec<String>>,
    }

    impl FakeRunner {
        pub fn new(rules: Vec<(&str, Vec<Output>)>) -> Self {
            Self {
                rules: rules
                    .into_iter()
                    .map(|(k, v)| (k.to_string(), Mutex::new(v.into())))
                    .collect(),
                calls: Mutex::new(vec![]),
            }
        }
        pub fn calls(&self) -> Vec<String> {
            self.calls.lock().unwrap().clone()
        }
        pub fn called(&self, needle: &str) -> bool {
            self.calls().iter().any(|c| c.contains(needle))
        }
    }

    pub fn out(stdout: &str) -> Output {
        Output {
            code: Some(0),
            stdout: stdout.into(),
            ..Default::default()
        }
    }

    pub fn fail(stderr: &str) -> Output {
        Output {
            code: Some(1),
            stderr: stderr.into(),
            ..Default::default()
        }
    }

    impl Runner for FakeRunner {
        fn run(&self, program: &str, args: &[String], _: Duration) -> std::io::Result<Output> {
            let line = format!("{program} {}", args.join(" "));
            self.calls.lock().unwrap().push(line.clone());
            for (k, answers) in &self.rules {
                if line.contains(k.as_str()) {
                    let mut q = answers.lock().unwrap();
                    return Ok(if q.len() > 1 {
                        q.pop_front().unwrap()
                    } else {
                        q[0].clone()
                    });
                }
            }
            Err(std::io::Error::new(
                std::io::ErrorKind::NotFound,
                format!("no rule for {line}"),
            ))
        }
    }

    #[test]
    fn sys_runner_captures_output_and_times_out() {
        let r = SysRunner;
        let o = r
            .run(
                "sh",
                &["-c".into(), "echo hi; echo oops >&2".into()],
                Duration::from_secs(5),
            )
            .unwrap();
        assert!(o.success());
        assert_eq!(o.stdout.trim(), "hi");
        assert_eq!(o.stderr.trim(), "oops");
        let o = r
            .run(
                "sh",
                &["-c".into(), "sleep 5".into()],
                Duration::from_millis(100),
            )
            .unwrap();
        assert!(o.timed_out && !o.success());
        assert!(r
            .run("/nonexistent/binary", &[], Duration::from_secs(1))
            .is_err());
    }
}
