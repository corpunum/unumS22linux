//! OpenUnum's chat stream: `GET /api/chat/stream?sessionId=...` (src/server/routes/chat_tools.mjs).
//!
//! It sends typed events (`event: content_delta` plus a JSON `data:` line) and, without an event
//! name, periodic snapshots of the whole turn (`done`, `liveContent`, `completed.reply`). The server
//! closes the stream when a snapshot says `done`.

use crate::model::Prompt;
use serde_json::Value;

#[derive(Debug, Clone, PartialEq)]
pub struct RawEvent {
    pub event: Option<String>,
    pub data: String,
}

/// Incremental parser: feed it whatever bytes arrive, get back the complete events.
#[derive(Default)]
pub struct SseParser {
    buf: Vec<u8>,
}

impl SseParser {
    pub fn feed(&mut self, bytes: &[u8]) -> Vec<RawEvent> {
        // CR is only ever half of a CRLF line ending here; dropping it leaves "\n\n" as the separator.
        self.buf
            .extend(bytes.iter().copied().filter(|b| *b != b'\r'));
        let mut out = vec![];
        while let Some(end) = self.buf.windows(2).position(|w| w == b"\n\n") {
            let block: Vec<u8> = self.buf.drain(..end + 2).collect();
            // Only whole blocks are decoded, so a multi-byte character split across chunks is intact.
            if let Some(ev) = parse_block(&String::from_utf8_lossy(&block)) {
                out.push(ev);
            }
        }
        out
    }
}

fn parse_block(block: &str) -> Option<RawEvent> {
    let mut event = None;
    let mut data: Vec<&str> = vec![];
    for line in block.lines() {
        if line.starts_with(':') || line.is_empty() {
            continue;
        }
        let (field, value) = line.split_once(':').unwrap_or((line, ""));
        let value = value.strip_prefix(' ').unwrap_or(value);
        match field {
            "event" => event = Some(value.to_string()),
            "data" => data.push(value),
            _ => {}
        }
    }
    (!data.is_empty()).then(|| RawEvent {
        event,
        data: data.join("\n"),
    })
}

#[derive(Debug, Clone, PartialEq)]
pub enum ChatEvent {
    /// A piece of the assistant's answer. `replace` means the token is the whole final reply.
    Delta {
        token: String,
        replace: bool,
    },
    Thinking,
    ToolStarted(String),
    ToolFinished {
        name: String,
        ok: bool,
    },
    Prompt(Prompt),
    Snapshot {
        done: bool,
        reply: Option<String>,
        live: Option<String>,
    },
    TurnEnd {
        reply: Option<String>,
    },
}

fn text(v: &Value) -> Option<String> {
    v.as_str().map(String::from)
}

/// Map one raw event onto what the shell shows; events it does not show decode to `None`.
pub fn decode(raw: &RawEvent) -> Option<ChatEvent> {
    let v: Value = serde_json::from_str(&raw.data).ok()?;
    Some(match raw.event.as_deref() {
        Some("content_delta") => ChatEvent::Delta {
            token: text(&v["token"])?,
            replace: v["final"] == true,
        },
        Some("reasoning_start") | Some("reasoning_delta") | Some("agent_thinking") => {
            ChatEvent::Thinking
        }
        Some("tool_call_started") => ChatEvent::ToolStarted(text(&v["tool"])?),
        Some("tool_call_completed") => ChatEvent::ToolFinished {
            name: text(&v["tool"])?,
            ok: v["resultOk"] != false,
        },
        Some("tool_call_failed") => ChatEvent::ToolFinished {
            name: text(&v["tool"])?,
            ok: false,
        },
        Some("user_prompt_requested") => ChatEvent::Prompt(Prompt {
            id: text(&v["promptId"])?,
            header: text(&v["header"]).unwrap_or_default(),
            question: text(&v["question"])?,
            options: v["options"]
                .as_array()
                .map(|a| {
                    a.iter()
                        .filter_map(|o| text(o).or_else(|| text(&o["label"])))
                        .take(6)
                        .collect()
                })
                .unwrap_or_default(),
        }),
        Some("turn_end") => ChatEvent::TurnEnd {
            reply: text(&v["reply"]),
        },
        Some("turn_cancelled") => ChatEvent::TurnEnd { reply: None },
        Some(_) => return None,
        None => ChatEvent::Snapshot {
            done: v["done"] == true,
            reply: text(&v["completed"]["reply"]),
            live: text(&v["liveContent"]),
        },
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ev(event: Option<&str>, data: &str) -> RawEvent {
        RawEvent {
            event: event.map(String::from),
            data: data.into(),
        }
    }

    #[test]
    fn parses_typed_events_and_snapshots() {
        let mut p = SseParser::default();
        let out =
            p.feed(b"event: content_delta\ndata: {\"token\":\"Hi\"}\n\ndata: {\"done\":false}\n\n");
        assert_eq!(
            out,
            vec![
                ev(Some("content_delta"), "{\"token\":\"Hi\"}"),
                ev(None, "{\"done\":false}")
            ]
        );
    }

    #[test]
    fn events_split_across_chunks_and_crlf() {
        let mut p = SseParser::default();
        assert!(p.feed(b"event: turn_end\r\nda").is_empty());
        assert!(p.feed(b"ta: {\"reply\":\"ok\"}\r\n").is_empty());
        let out = p.feed(b"\r\n");
        assert_eq!(out, vec![ev(Some("turn_end"), "{\"reply\":\"ok\"}")]);
    }

    #[test]
    fn multibyte_text_survives_a_chunk_boundary_between_events() {
        let mut p = SseParser::default();
        let one = "data: {\"liveContent\":\"καλημέρα\"}\n\n"
            .as_bytes()
            .to_vec();
        let cut = one.iter().position(|b| *b == 0xCE).unwrap() + 1; // inside the first Greek letter
        let mut out = p.feed(&one[..cut]);
        out.extend(p.feed(&one[cut..]));
        assert_eq!(out.len(), 1);
        assert!(out[0].data.contains("καλημέρα"));
    }

    #[test]
    fn comments_heartbeats_and_multiline_data() {
        let mut p = SseParser::default();
        let out = p.feed(b": ping\n\nevent: x\ndata: a\ndata: b\n\n");
        assert_eq!(out, vec![ev(Some("x"), "a\nb")]);
    }

    #[test]
    fn decodes_the_events_the_server_sends() {
        assert_eq!(
            decode(&ev(
                Some("content_delta"),
                r#"{"sessionId":"touch","token":"Hel"}"#
            )),
            Some(ChatEvent::Delta {
                token: "Hel".into(),
                replace: false
            })
        );
        assert_eq!(
            decode(&ev(
                Some("content_delta"),
                r#"{"token":"whole","final":true}"#
            )),
            Some(ChatEvent::Delta {
                token: "whole".into(),
                replace: true
            })
        );
        assert_eq!(
            decode(&ev(Some("reasoning_start"), "{}")),
            Some(ChatEvent::Thinking)
        );
        assert_eq!(
            decode(&ev(
                Some("tool_call_started"),
                r#"{"tool":"wifi_status","args":{},"step":1}"#
            )),
            Some(ChatEvent::ToolStarted("wifi_status".into()))
        );
        assert_eq!(
            decode(&ev(
                Some("tool_call_completed"),
                r#"{"tool":"wifi_status","resultOk":true}"#
            )),
            Some(ChatEvent::ToolFinished {
                name: "wifi_status".into(),
                ok: true
            })
        );
        assert_eq!(
            decode(&ev(
                Some("tool_call_failed"),
                r#"{"tool":"camera_capture"}"#
            )),
            Some(ChatEvent::ToolFinished {
                name: "camera_capture".into(),
                ok: false
            })
        );
        assert_eq!(
            decode(&ev(Some("turn_end"), r#"{"reply":"All good."}"#)),
            Some(ChatEvent::TurnEnd {
                reply: Some("All good.".into())
            })
        );
    }

    #[test]
    fn decodes_an_ask_user_prompt_with_string_and_object_options() {
        let raw = ev(
            Some("user_prompt_requested"),
            r#"{"promptId":"ask-1-x","sessionId":"touch","question":"Which aspect?","header":"Aspect","options":[{"label":"16:9","description":"wide"},"1:1"],"allowFreeText":true}"#,
        );
        assert_eq!(
            decode(&raw),
            Some(ChatEvent::Prompt(Prompt {
                id: "ask-1-x".into(),
                header: "Aspect".into(),
                question: "Which aspect?".into(),
                options: vec!["16:9".into(), "1:1".into()],
            }))
        );
    }

    #[test]
    fn decodes_snapshots() {
        let done = ev(
            None,
            r#"{"ok":true,"done":true,"completed":{"reply":"final text"},"liveContent":null}"#,
        );
        assert_eq!(
            decode(&done),
            Some(ChatEvent::Snapshot {
                done: true,
                reply: Some("final text".into()),
                live: None
            })
        );
        let running = ev(
            None,
            r#"{"ok":true,"done":false,"pending":true,"liveContent":"par"}"#,
        );
        assert_eq!(
            decode(&running),
            Some(ChatEvent::Snapshot {
                done: false,
                reply: None,
                live: Some("par".into())
            })
        );
    }

    #[test]
    fn ignored_and_malformed_events_decode_to_none() {
        assert_eq!(decode(&ev(Some("step_started"), "{}")), None);
        assert_eq!(decode(&ev(Some("content_delta"), "not json")), None);
        assert_eq!(decode(&ev(Some("content_delta"), r#"{"nope":1}"#)), None);
        assert_eq!(decode(&ev(Some("tool_call_started"), "{}")), None);
    }
}
