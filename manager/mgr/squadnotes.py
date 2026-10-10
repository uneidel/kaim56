# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Squad feedback notes: the operator annotates a channel message (a quote of
it + a comment), keeps the notes as drafts, and sends them as ONE post to the
lead — "Feedback on message #<id> (<author>): 1. «quote» — comment …" — which
wakes the lead through the normal post path (mgr/squadgate.operator_post).

Notes live in squad.db (table squad_note, made in mgr/squads._conn); prefix /
suffix (≤ 40 chars) let the UI find the quote again in the rendered text.
A person sees its own notes plus everybody's sent ones; a role-user deletes
only its own drafts, the admin any draft; sent notes stay.

Part of the mgr package: no import from manager.py.
"""
import time

from mgr import squadgate as _squadgate
from mgr import squadrec as _squadrec
from mgr import squads as _squads

QUOTE_MAX, COMMENT_MAX, FIX_MAX = 1000, 4000, 40
_COLS = ("id", "project", "msg_id", "quote", "prefix", "suffix", "comment", "by", "created", "status", "sent_msg_id")


def _out(row):
    return dict(zip(_COLS, row))


def _rows(where, args):
    with _squads._db() as c:
        return [_out(r) for r in c.execute(f"SELECT {','.join(_COLS)} FROM squad_note WHERE {where} ORDER BY id", args)]


def notes(project, by, msg_id=None):
    """`by`'s notes and everybody's sent ones, of one message or the whole project."""
    q, a = "project=? AND (by=? OR status='sent')", [project, by]
    if msg_id is not None:
        q, a = q + " AND msg_id=?", a + [int(msg_id)]
    return _rows(q, a)


def create(proj, by, body):
    """A draft on a message of the project -> (note, '') or (None, why)."""
    quote, comment = str(body.get("quote") or ""), str(body.get("comment") or "").strip()
    mid = str(body.get("msg_id") or "")
    msg = _squads._get(mid) if mid.isdigit() else None
    if not msg or msg["project"] != proj["name"]:
        return None, "msg_id: no such message in this project"
    if not quote.strip() or not comment:
        return None, "quote and comment are required"
    if len(quote) > QUOTE_MAX:
        return None, f"quote longer than {QUOTE_MAX} characters"
    if len(comment) > COMMENT_MAX:
        return None, f"comment longer than {COMMENT_MAX} characters"
    row = (proj["name"], msg["id"], quote, str(body.get("prefix") or "")[-FIX_MAX:],
           str(body.get("suffix") or "")[:FIX_MAX], comment, by, int(time.time()), "draft", None)
    with _squads._db() as c:
        nid = c.execute(f"INSERT INTO squad_note({','.join(_COLS[1:])}) VALUES({','.join('?' * len(row))})", row).lastrowid
    return _rows("id=?", [nid])[0], ""


def delete(project, nid, by, admin):
    """Delete a draft: its author's, or any as admin -> (True, '') or (False, why)."""
    n = _rows("id=? AND project=?", [int(nid), project])
    if not n or (n[0]["by"] != by and not admin):
        return False, "no such note"
    if n[0]["status"] != "draft":
        return False, "a sent note stays"
    with _squads._db() as c:
        c.execute("DELETE FROM squad_note WHERE id=?", (n[0]["id"],))
    return True, ""


def compose(msg, drafts):
    lines = [f"Feedback on message #{msg['id']} ({msg['from']}):"]
    lines += [f"{i}. «{n['quote']}» — {n['comment']}" for i, n in enumerate(drafts, 1)]
    lines.append("Turn each point into work: create/assign tickets (ticket_create/ticket_assign) or ask_human if unclear.")
    return "\n".join(lines)


def send(proj, who, body):
    """`who`'s drafts (body ids:[…] or msg_id: all drafts of that message) of ONE
    message as one post to the lead (no lead: the message's author) ->
    (post, '') or (None, why)."""
    by = "user:" + who
    if body.get("ids"):
        ids = [int(x) for x in body["ids"] if str(x).isdigit()][:200]
        drafts = _rows(f"project=? AND by=? AND status='draft' AND id IN ({','.join('?' * len(ids))})",
                       [proj["name"], by] + ids) if ids else []
    else:
        mid = str(body.get("msg_id") or "")
        drafts = _rows("project=? AND by=? AND status='draft' AND msg_id=?", [proj["name"], by, int(mid)]) if mid.isdigit() else []
    if not drafts:
        return None, "no drafts to send"
    if len({n["msg_id"] for n in drafts}) > 1:
        return None, "send the notes of one message at a time"
    msg = _squads._get(drafts[0]["msg_id"])
    if not msg:
        return None, "the annotated message is gone"
    to = _squadgate.lead(proj) or (msg["from"] if msg["from"] in (proj.get("members") or {}) else "")
    if not to:
        return None, "no lead to send the feedback to"
    post, why = _squadgate.operator_post(proj, who, {"to": [to], "text": compose(msg, drafts), "reply_to": msg["id"]})
    if not post:
        return None, why
    with _squads._db() as c:
        c.executemany("UPDATE squad_note SET status='sent', sent_msg_id=? WHERE id=?", [(post["id"], n["id"]) for n in drafts])
    _squadrec.log_feedback(proj["name"], {"by": by, "msg_id": msg["id"], "post_id": post["id"], "to": to,
                                          "notes": [{"id": n["id"], "quote": n["quote"], "comment": n["comment"]} for n in drafts]})
    return post, ""
