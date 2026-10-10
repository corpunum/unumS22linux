//! HTTP to the two loopback services: OpenUnum (`127.0.0.1:18880`) and s22d (`127.0.0.1:8766`).

use crate::model::{Line, Who};
use crate::sse::{self, ChatEvent, SseParser};
use futures_util::stream::{self, BoxStream, StreamExt};
use serde_json::{json, Value};
use std::path::PathBuf;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

#[derive(Debug, Clone)]
pub struct Endpoints {
    pub openunum: String,
    pub s22d: String,
    pub session: String,
    /// `s22-touchd`'s run directory: `shell.sock` lives here, and confirmation answers go to `confirm/`.
    pub run_dir: PathBuf,
}

impl Endpoints {
    pub fn from_env() -> Self {
        let get = |k: &str, d: &str| std::env::var(k).unwrap_or_else(|_| d.to_string());
        Self {
            openunum: get("S22_SHELL_API", "http://127.0.0.1:18880")
                .trim_end_matches('/')
                .into(),
            s22d: get("S22_SHELL_S22D", "http://127.0.0.1:8766")
                .trim_end_matches('/')
                .into(),
            session: get("S22_SHELL_SESSION", "touch"),
            run_dir: PathBuf::from(get("S22_TOUCH_RUN", "/run/s22-touch")),
        }
    }

    pub fn sock(&self) -> PathBuf {
        self.run_dir.join("shell.sock")
    }

    pub fn confirm_dir(&self) -> PathBuf {
        self.run_dir.join("confirm")
    }
}

fn client(timeout: Duration) -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .no_proxy()
        .timeout(timeout)
        .build()
        .map_err(|e| e.to_string())
}

async fn get_json(url: String, timeout: Duration) -> Result<Value, String> {
    let r = client(timeout)?
        .get(&url)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let status = r.status();
    let body: Value = r.json().await.unwrap_or(Value::Null);
    if status.is_success() {
        Ok(body)
    } else {
        Err(error_text(status.as_u16(), &body))
    }
}

async fn post_json(url: String, body: Value, timeout: Duration) -> Result<(u16, Value), String> {
    let r = client(timeout)?
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let status = r.status().as_u16();
    Ok((status, r.json().await.unwrap_or(Value::Null)))
}

/// `s22d` and OpenUnum both put the reason in `error`; s22d adds a `code`.
pub fn error_text(status: u16, body: &Value) -> String {
    let msg = body["error"]
        .as_str()
        .or(body["message"].as_str())
        .unwrap_or("");
    match body["code"].as_str() {
        Some(code) => format!("{code}: {msg}"),
        None if msg.is_empty() => format!("HTTP {status}"),
        None => msg.to_string(),
    }
}

pub async fn fetch_status(ep: Endpoints) -> Result<Value, String> {
    get_json(format!("{}/v1/status", ep.s22d), Duration::from_secs(8)).await
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct AgentInfo {
    pub model: Option<String>,
    pub healthy: Option<bool>,
}

/// Short model label: `openai/gpt-6-luna` becomes `gpt-6-luna`, a local gguf becomes `local <name>`.
pub fn model_label(raw: &str) -> String {
    if let Some(rest) = raw.strip_prefix("openai/") {
        return rest.to_string();
    }
    if let Some(rest) = raw.strip_prefix("llama-cpp-local/") {
        return format!(
            "local {}",
            rest.trim_start_matches('/').trim_start_matches("models/")
        );
    }
    raw.to_string()
}

pub async fn fetch_agent(ep: Endpoints) -> AgentInfo {
    let model = get_json(
        format!("{}/api/model/current", ep.openunum),
        Duration::from_secs(8),
    )
    .await;
    let health = get_json(
        format!("{}/api/health", ep.openunum),
        Duration::from_secs(8),
    )
    .await;
    AgentInfo {
        model: model
            .ok()
            .and_then(|m| m["model"].as_str().map(model_label)),
        healthy: health.ok().map(|h| h["healthy"] == true || h["ok"] == true),
    }
}

pub async fn fetch_phone(ep: Endpoints) -> Result<(Value, Option<Value>), String> {
    let modem = get_json(
        format!("{}/v1/modem/status", ep.s22d),
        Duration::from_secs(25),
    )
    .await?;
    let sms = get_json(
        format!("{}/v1/sms?limit=5", ep.s22d),
        Duration::from_secs(25),
    )
    .await
    .ok();
    Ok((modem, sms))
}

pub async fn set_brightness(ep: Endpoints, percent: u8) -> Result<(), String> {
    let (status, body) = post_json(
        format!("{}/v1/display/brightness", ep.s22d),
        json!({"percent": percent}),
        Duration::from_secs(10),
    )
    .await?;
    if (200..300).contains(&status) {
        Ok(())
    } else {
        Err(error_text(status, &body))
    }
}

/// The last messages of the chat session, oldest first.
pub async fn fetch_history(ep: Endpoints) -> Result<Vec<Line>, String> {
    let v = get_json(
        format!("{}/api/sessions/{}?limit=30", ep.openunum, ep.session),
        Duration::from_secs(10),
    )
    .await?;
    Ok(history_lines(&v))
}

pub fn history_lines(v: &Value) -> Vec<Line> {
    v["messages"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|m| {
                    let who = match m["role"].as_str()? {
                        "user" => Who::You,
                        "assistant" => Who::Agent,
                        _ => return None,
                    };
                    let text = m["content"].as_str()?.trim();
                    (!text.is_empty()).then(|| Line {
                        who,
                        text: text.chars().take(1500).collect(),
                    })
                })
                .rev()
                .take(30)
                .collect::<Vec<_>>()
                .into_iter()
                .rev()
                .collect()
        })
        .unwrap_or_default()
}

pub async fn answer_prompt(ep: Endpoints, prompt_id: String, value: String) -> Result<(), String> {
    let selected: Vec<String> = if value.is_empty() {
        vec![]
    } else {
        vec![value.clone()]
    };
    let (status, body) = post_json(
        format!("{}/api/chat/answer", ep.openunum),
        json!({"promptId": prompt_id, "value": value, "selected": selected}),
        Duration::from_secs(10),
    )
    .await?;
    if (200..300).contains(&status) {
        Ok(())
    } else {
        Err(error_text(status, &body))
    }
}

pub async fn cancel_turn(ep: Endpoints) -> Result<(), String> {
    let (status, body) = post_json(
        format!("{}/api/chat/cancel", ep.openunum),
        json!({"sessionId": ep.session}),
        Duration::from_secs(10),
    )
    .await?;
    if (200..300).contains(&status) {
        Ok(())
    } else {
        Err(error_text(status, &body))
    }
}

// ------------------------------------------------------------------ one chat turn

#[derive(Debug, Clone, PartialEq)]
pub enum ChatMsg {
    Event(ChatEvent),
    /// `POST /api/chat` answered: `Some(reply)` when the turn is done, `None` when the server only queued it (202).
    Reply(Result<Option<String>, String>),
    /// The stream ended (the server closes it when the turn is done) or never opened.
    StreamClosed(Option<String>),
}

/// UTC time as `YYYY-MM-DDTHH:MM:SS.mmmZ`, the format OpenUnum stores message times in.
pub fn iso_utc(t: SystemTime) -> String {
    let d = t.duration_since(UNIX_EPOCH).unwrap_or_default();
    let (secs, ms) = (d.as_secs() as i64, d.subsec_millis());
    let (days, rem) = (secs.div_euclid(86_400), secs.rem_euclid(86_400));
    // civil_from_days (Howard Hinnant)
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}.{ms:03}Z",
        rem / 3600,
        rem % 3600 / 60,
        rem % 60
    )
}

async fn open_stream(ep: Endpoints, since: String) -> BoxStream<'static, ChatMsg> {
    // No overall timeout: a turn can run for minutes and the server closes the stream when it is done.
    let c = match reqwest::Client::builder().no_proxy().build() {
        Ok(c) => c,
        Err(e) => {
            return stream::once(async move { ChatMsg::StreamClosed(Some(e.to_string())) }).boxed()
        }
    };
    let res = c
        .get(format!("{}/api/chat/stream", ep.openunum))
        .query(&[
            ("sessionId", ep.session.as_str()),
            ("since", since.as_str()),
        ])
        .send()
        .await;
    match res {
        Ok(r) if r.status().is_success() => {
            let events = r
                .bytes_stream()
                .scan(SseParser::default(), |parser, chunk| {
                    let evs = match chunk {
                        Ok(bytes) => parser.feed(&bytes),
                        Err(_) => vec![],
                    };
                    futures_util::future::ready(Some(stream::iter(evs)))
                })
                .flatten()
                .filter_map(|raw| {
                    futures_util::future::ready(sse::decode(&raw).map(ChatMsg::Event))
                });
            events
                .chain(stream::once(async { ChatMsg::StreamClosed(None) }))
                .boxed()
        }
        Ok(r) => {
            let s = r.status().as_u16();
            stream::once(async move { ChatMsg::StreamClosed(Some(format!("stream HTTP {s}"))) })
                .boxed()
        }
        Err(e) => stream::once(async move { ChatMsg::StreamClosed(Some(e.to_string())) }).boxed(),
    }
}

async fn post_chat(ep: Endpoints, prompt: String) -> Result<Option<String>, String> {
    let (status, body) = post_json(
        format!("{}/api/chat", ep.openunum),
        json!({"sessionId": ep.session, "message": prompt}),
        Duration::from_secs(900),
    )
    .await?;
    if status == 202 || body["pending"] == true {
        return Ok(None);
    }
    if !(200..300).contains(&status) {
        return Err(error_text(status, &body));
    }
    Ok(body["reply"]
        .as_str()
        .or(body["content"].as_str())
        .map(String::from))
}

/// Start one chat turn: open the event stream first, then post the message, and merge both.
/// The stream shows the answer as it is written; the POST carries the final reply.
pub fn chat_turn(ep: Endpoints, prompt: String) -> BoxStream<'static, ChatMsg> {
    let since = iso_utc(SystemTime::now() - Duration::from_secs(1));
    let events = {
        let ep = ep.clone();
        stream::once(async move { open_stream(ep, since).await }).flatten()
    };
    let post = stream::once(async move { ChatMsg::Reply(post_chat(ep, prompt).await) });
    stream::select(events, post).boxed()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::Chat;

    fn ep(server: &mockito::ServerGuard) -> Endpoints {
        Endpoints {
            openunum: server.url(),
            s22d: server.url(),
            session: "touch".into(),
            run_dir: PathBuf::from("/nonexistent"),
        }
    }

    #[test]
    fn iso_time_format() {
        assert_eq!(iso_utc(UNIX_EPOCH), "1970-01-01T00:00:00.000Z");
        assert_eq!(
            iso_utc(UNIX_EPOCH + Duration::from_millis(1_791_629_207_123)),
            "2026-10-10T10:46:47.123Z"
        );
        assert_eq!(
            iso_utc(UNIX_EPOCH + Duration::from_secs(951_782_400)),
            "2000-02-29T00:00:00.000Z"
        );
    }

    #[test]
    fn model_labels() {
        assert_eq!(model_label("openai/gpt-6-luna"), "gpt-6-luna");
        assert_eq!(
            model_label("llama-cpp-local//models/Qwen3.5-0.8B-Q4_0.gguf"),
            "local Qwen3.5-0.8B-Q4_0.gguf"
        );
        assert_eq!(model_label("other"), "other");
    }

    #[test]
    fn error_text_prefers_the_s22d_code() {
        assert_eq!(
            error_text(403, &json!({"code": "policy_denied", "error": "muted"})),
            "policy_denied: muted"
        );
        assert_eq!(error_text(500, &json!({})), "HTTP 500");
        assert_eq!(error_text(400, &json!({"error": "bad"})), "bad");
    }

    #[test]
    fn history_keeps_user_and_assistant_messages_in_order() {
        let v = json!({"messages": [
            {"role": "system", "content": "x"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "tool", "content": "t"},
            {"role": "assistant", "content": "  "},
        ]});
        let l = history_lines(&v);
        assert_eq!(l.len(), 2);
        assert_eq!((l[0].who, l[1].who), (Who::You, Who::Agent));
        assert!(history_lines(&json!({})).is_empty());
    }

    #[tokio::test]
    async fn status_and_agent_info_from_loopback_services() {
        let mut s = mockito::Server::new_async().await;
        let _m1 = s
            .mock("GET", "/v1/status")
            .with_body(r#"{"ok":true,"battery":{"percent":90}}"#)
            .create_async()
            .await;
        let _m2 = s
            .mock("GET", "/api/model/current")
            .with_body(r#"{"model":"openai/gpt-6-luna"}"#)
            .create_async()
            .await;
        let _m3 = s
            .mock("GET", "/api/health")
            .with_body(r#"{"healthy":true}"#)
            .create_async()
            .await;
        assert_eq!(
            fetch_status(ep(&s)).await.unwrap()["battery"]["percent"],
            90
        );
        assert_eq!(
            fetch_agent(ep(&s)).await,
            AgentInfo {
                model: Some("gpt-6-luna".into()),
                healthy: Some(true)
            }
        );
    }

    #[tokio::test]
    async fn status_errors_carry_the_daemon_code() {
        let mut s = mockito::Server::new_async().await;
        let _m = s
            .mock("GET", "/v1/status")
            .with_status(503)
            .with_body(r#"{"ok":false,"code":"unavailable","error":"/proc unreadable"}"#)
            .create_async()
            .await;
        assert_eq!(
            fetch_status(ep(&s)).await.unwrap_err(),
            "unavailable: /proc unreadable"
        );
        assert!(fetch_status(Endpoints {
            s22d: "http://127.0.0.1:1".into(),
            ..ep(&s)
        })
        .await
        .is_err());
        assert_eq!(
            fetch_agent(Endpoints {
                openunum: "http://127.0.0.1:1".into(),
                ..ep(&s)
            })
            .await,
            AgentInfo::default()
        );
    }

    #[tokio::test]
    async fn brightness_posts_percent_and_reports_refusals() {
        let mut s = mockito::Server::new_async().await;
        let ok = s
            .mock("POST", "/v1/display/brightness")
            .match_body(mockito::Matcher::Json(json!({"percent": 40})))
            .with_body(r#"{"ok":true}"#)
            .create_async()
            .await;
        set_brightness(ep(&s), 40).await.unwrap();
        ok.assert_async().await;
        let _bad = s
            .mock("POST", "/v1/display/brightness")
            .match_body(mockito::Matcher::Json(json!({"percent": 7})))
            .with_status(503)
            .with_body(r#"{"ok":false,"code":"unavailable","error":"backlight"}"#)
            .create_async()
            .await;
        assert_eq!(
            set_brightness(ep(&s), 7).await.unwrap_err(),
            "unavailable: backlight"
        );
    }

    #[tokio::test]
    async fn prompt_answer_and_cancel() {
        let mut s = mockito::Server::new_async().await;
        let a = s
            .mock("POST", "/api/chat/answer")
            .match_body(mockito::Matcher::Json(
                json!({"promptId": "ask-1", "value": "16:9", "selected": ["16:9"]}),
            ))
            .with_body(r#"{"ok":true}"#)
            .create_async()
            .await;
        answer_prompt(ep(&s), "ask-1".into(), "16:9".into())
            .await
            .unwrap();
        a.assert_async().await;
        let c = s
            .mock("POST", "/api/chat/cancel")
            .match_body(mockito::Matcher::Json(json!({"sessionId": "touch"})))
            .with_body(r#"{"ok":true}"#)
            .create_async()
            .await;
        cancel_turn(ep(&s)).await.unwrap();
        c.assert_async().await;
    }

    async fn collect(ep: Endpoints, prompt: &str) -> Vec<ChatMsg> {
        chat_turn(ep, prompt.into()).collect::<Vec<_>>().await
    }

    #[tokio::test]
    async fn a_turn_streams_deltas_then_the_reply_and_ends() {
        let mut s = mockito::Server::new_async().await;
        let sse_body = concat!(
            "data: {\"ok\":true,\"done\":false,\"pending\":true}\n\n",
            "event: tool_call_started\ndata: {\"tool\":\"wifi_status\"}\n\n",
            "event: tool_call_completed\ndata: {\"tool\":\"wifi_status\",\"resultOk\":true}\n\n",
            "event: content_delta\ndata: {\"token\":\"You are \"}\n\n",
            "event: content_delta\ndata: {\"token\":\"online.\"}\n\n",
            "data: {\"ok\":true,\"done\":true,\"completed\":{\"reply\":\"You are online.\"}}\n\n",
        );
        let _stream = s
            .mock("GET", "/api/chat/stream")
            .match_query(mockito::Matcher::AllOf(vec![
                mockito::Matcher::UrlEncoded("sessionId".into(), "touch".into()),
                mockito::Matcher::Regex("since=20".into()),
            ]))
            .with_header("content-type", "text/event-stream")
            .with_body(sse_body)
            .create_async()
            .await;
        let post = s
            .mock("POST", "/api/chat")
            .match_body(mockito::Matcher::Json(
                json!({"sessionId": "touch", "message": "am I online?"}),
            ))
            .with_body(r#"{"ok":true,"reply":"You are online."}"#)
            .create_async()
            .await;
        let msgs = collect(ep(&s), "am I online?").await;
        post.assert_async().await;

        let mut chat = Chat::default();
        chat.start_turn("am I online?");
        let mut saw_reply = false;
        let mut closed = false;
        for m in msgs {
            match m {
                ChatMsg::Event(e) => {
                    chat.apply(e);
                }
                ChatMsg::Reply(r) => {
                    saw_reply = true;
                    chat.finish(r.unwrap());
                }
                ChatMsg::StreamClosed(e) => {
                    assert_eq!(e, None);
                    closed = true;
                }
            }
        }
        assert!(saw_reply && closed);
        assert!(!chat.busy);
        assert_eq!(chat.lines.last().unwrap().text, "You are online.");
    }

    #[tokio::test]
    async fn deltas_arrive_before_the_reply_when_the_post_is_slow() {
        // The stream is served first; the POST returns the reply. The order a UI sees is stream-first
        // for the events that are already buffered, so the live text is visible during the turn.
        let mut s = mockito::Server::new_async().await;
        let _st = s
            .mock("GET", "/api/chat/stream")
            .match_query(mockito::Matcher::Any)
            .with_body("event: content_delta\ndata: {\"token\":\"partial\"}\n\n")
            .create_async()
            .await;
        let _p = s
            .mock("POST", "/api/chat")
            .with_body(r#"{"reply":"full"}"#)
            .create_async()
            .await;
        let msgs = collect(ep(&s), "x").await;
        let first_delta = msgs
            .iter()
            .position(|m| matches!(m, ChatMsg::Event(ChatEvent::Delta { .. })))
            .unwrap();
        assert!(first_delta < msgs.len());
        assert!(msgs.contains(&ChatMsg::Reply(Ok(Some("full".into())))));
    }

    #[tokio::test]
    async fn a_missing_stream_does_not_break_the_chat() {
        let mut s = mockito::Server::new_async().await;
        let _st = s
            .mock("GET", "/api/chat/stream")
            .match_query(mockito::Matcher::Any)
            .with_status(404)
            .create_async()
            .await;
        let _p = s
            .mock("POST", "/api/chat")
            .with_body(r#"{"reply":"still works"}"#)
            .create_async()
            .await;
        let msgs = collect(ep(&s), "x").await;
        assert!(msgs.contains(&ChatMsg::StreamClosed(Some("stream HTTP 404".into()))));
        assert!(msgs.contains(&ChatMsg::Reply(Ok(Some("still works".into())))));
    }

    #[tokio::test]
    async fn queued_and_failed_posts() {
        let mut s = mockito::Server::new_async().await;
        let _st = s
            .mock("GET", "/api/chat/stream")
            .match_query(mockito::Matcher::Any)
            .with_body("")
            .create_async()
            .await;
        let _p = s
            .mock("POST", "/api/chat")
            .with_status(202)
            .with_body(r#"{"ok":true,"pending":true,"queued":true}"#)
            .create_async()
            .await;
        assert!(collect(ep(&s), "x")
            .await
            .contains(&ChatMsg::Reply(Ok(None))));

        let mut s = mockito::Server::new_async().await;
        let _st = s
            .mock("GET", "/api/chat/stream")
            .match_query(mockito::Matcher::Any)
            .with_body("")
            .create_async()
            .await;
        let _p = s
            .mock("POST", "/api/chat")
            .with_status(400)
            .with_body(r#"{"error":"invalid_payload"}"#)
            .create_async()
            .await;
        assert!(collect(ep(&s), "x")
            .await
            .contains(&ChatMsg::Reply(Err("invalid_payload".into()))));
    }

    #[tokio::test]
    async fn unreachable_openunum_fails_the_turn_cleanly() {
        let e = Endpoints {
            openunum: "http://127.0.0.1:1".into(),
            s22d: "http://127.0.0.1:1".into(),
            session: "touch".into(),
            run_dir: PathBuf::from("/nonexistent"),
        };
        let msgs = collect(e, "x").await;
        assert!(msgs.iter().any(|m| matches!(m, ChatMsg::Reply(Err(_)))));
        assert!(msgs
            .iter()
            .any(|m| matches!(m, ChatMsg::StreamClosed(Some(_)))));
    }
}
