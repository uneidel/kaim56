// kAIm56 — self-hosted Firecracker AI-agent platform
// Copyright (C) 2026 the kAIm56 authors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The shared chat store (the manager's chats.json, also used by the phone app
// and the web UI): the JSON shape and the merge rules of the Android app's
// ChatMerge.kt, so all three clients agree.
use std::collections::HashMap;

use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Msg {
    pub user: bool,
    pub text: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub image: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub turn: Option<String>,
}

/// Equal by who and what (the merge compares content, not the turn id).
impl PartialEq for Msg {
    fn eq(&self, o: &Self) -> bool {
        self.user == o.user && self.text == o.text
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Conversation {
    pub id: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub mode: String,
    #[serde(default)]
    pub instance: String,
    #[serde(default, rename = "updatedAt")]
    pub updated_at: i64,
    #[serde(default)]
    pub messages: Vec<Msg>,
}

pub const NEW_TITLE: &str = "New Chat";

impl Conversation {
    pub fn new(instance: &str, now: i64) -> Self {
        Conversation {
            id: uuid::Uuid::new_v4().to_string(),
            title: NEW_TITLE.into(),
            mode: "server".into(),
            instance: instance.into(),
            updated_at: now,
            messages: vec![],
        }
    }

    /// A new chat takes its title from the first question (as the phone does).
    pub fn touch(&mut self, now: i64) {
        self.updated_at = now;
        if self.title == NEW_TITLE {
            if let Some(m) = self.messages.iter().find(|m| m.user && !m.text.trim().is_empty()) {
                self.title = m.text.trim().chars().take(40).collect();
            }
        }
    }
}

/// Merge the manager's chats into ours (ChatMerge.kt):
/// - tombstoned at or after its remote updatedAt: stays deleted;
/// - unknown remote chat: added;
/// - ours newer, or the chat a reply is streaming into (`busy`): untouched;
/// - ours a prefix of theirs: their new messages appended; a real divergence:
///   theirs adopted unless shorter (the next push reconciles a longer local one).
/// -> changed. `local` ends sorted newest first.
pub fn merge(local: &mut Vec<Conversation>, remote: Vec<Conversation>, tombs: &HashMap<String, i64>,
             busy: Option<&str>) -> bool {
    let mut changed = false;
    for r in remote {
        if tombs.get(&r.id).is_some_and(|t| r.updated_at <= *t) {
            continue;
        }
        let Some(l) = local.iter_mut().find(|c| c.id == r.id) else {
            local.push(r);
            changed = true;
            continue;
        };
        if r.updated_at <= l.updated_at || Some(r.id.as_str()) == busy {
            continue;
        }
        let prefix = r.messages.len() >= l.messages.len() && l.messages.iter().zip(&r.messages).all(|(a, b)| a == b);
        if prefix {
            if r.messages.len() > l.messages.len() {
                l.messages.extend_from_slice(&r.messages[l.messages.len()..]);
                changed = true;
            }
        } else if r.messages.len() >= l.messages.len() {
            l.messages = r.messages;
            changed = true;
        } else {
            continue;
        }
        if l.title != r.title && !r.title.trim().is_empty() {
            l.title = r.title;
            changed = true;
        }
        if !r.instance.is_empty() {
            l.instance = r.instance;
        }
        l.updated_at = r.updated_at;
    }
    local.sort_by(|a, b| b.updated_at.cmp(&a.updated_at));
    changed
}

/// Drop chats that a tombstone (from any device) deleted after their last change.
pub fn apply_tombs(local: &mut Vec<Conversation>, tombs: &HashMap<String, i64>) -> bool {
    let n = local.len();
    local.retain(|c| !tombs.get(&c.id).is_some_and(|t| c.updated_at <= *t));
    n != local.len()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn conv(id: &str, at: i64, msgs: &[(bool, &str)]) -> Conversation {
        Conversation {
            id: id.into(), title: "t".into(), mode: "server".into(), instance: "a".into(), updated_at: at,
            messages: msgs.iter().map(|(u, t)| Msg { user: *u, text: t.to_string(), image: None, turn: None }).collect(),
        }
    }

    #[test]
    fn appends_adopts_keeps_and_respects_tombstones() {
        let mut local = vec![conv("a", 1, &[(true, "q")]), conv("b", 5, &[(true, "x")]), conv("c", 1, &[(true, "q"), (false, "mine")])];
        let remote = vec![
            conv("a", 2, &[(true, "q"), (false, "answer")]),   // prefix: appended
            conv("b", 3, &[(true, "old")]),                     // ours newer: kept
            conv("c", 2, &[(true, "q"), (false, "theirs")]),   // diverged, not shorter: adopted
            conv("d", 2, &[(true, "new")]),                     // unknown: added
            conv("e", 2, &[(true, "gone")]),                    // tombstoned after: skipped
        ];
        let tombs = HashMap::from([("e".to_string(), 2)]);
        assert!(merge(&mut local, remote, &tombs, None));
        let get = |id: &str| local.iter().find(|c| c.id == id).unwrap().clone();
        assert_eq!(get("a").messages.len(), 2);
        assert_eq!(get("b").messages[0].text, "x");
        assert_eq!(get("c").messages[1].text, "theirs");
        assert!(local.iter().any(|c| c.id == "d"));
        assert!(!local.iter().any(|c| c.id == "e"));
        assert_eq!(local[0].id, "b");                            // newest first
    }

    #[test]
    fn a_streaming_chat_and_a_shorter_remote_stay_untouched() {
        let mut local = vec![conv("a", 1, &[(true, "q"), (false, "partial")]), conv("b", 1, &[(true, "q"), (false, "a")])];
        let remote = vec![conv("a", 9, &[(true, "q"), (false, "other")]), conv("b", 9, &[(true, "z")])];
        assert!(!merge(&mut local, remote, &HashMap::new(), Some("a")));
        assert_eq!(local.iter().find(|c| c.id == "a").unwrap().messages[1].text, "partial");
        assert_eq!(local.iter().find(|c| c.id == "b").unwrap().messages.len(), 2);
    }

    #[test]
    fn json_shape_matches_the_phone_and_titles_from_the_first_question() {
        let mut c = Conversation::new("vm1", 7);
        c.messages.push(Msg { user: true, text: "  Wie spät ist es in Tokio, bitte genau auf die Minute?  ".into(), image: None, turn: None });
        c.touch(8);
        assert_eq!(c.title, "Wie spät ist es in Tokio, bitte genau au");
        let j = serde_json::to_value(&c).unwrap();
        assert_eq!(j["updatedAt"], 8);
        assert_eq!(j["mode"], "server");
        assert!(j["messages"][0].get("turn").is_none());
        let back: Conversation = serde_json::from_value(serde_json::json!({"id": "x", "messages": [{"user": false, "text": "hi"}]})).unwrap();
        assert_eq!(back.updated_at, 0);
    }

    #[test]
    fn tombstones_remove_only_older_chats() {
        let mut local = vec![conv("a", 1, &[]), conv("b", 5, &[])];
        assert!(apply_tombs(&mut local, &HashMap::from([("a".to_string(), 2), ("b".to_string(), 2)])));
        assert_eq!(local.len(), 1);
        assert_eq!(local[0].id, "b");                            // changed after its deletion elsewhere
    }
}
