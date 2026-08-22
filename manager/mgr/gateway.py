# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Security Gateway: unsichtbare-Unicode-Filter (Text) + Bild-Metadaten-Strip.
Toggleable per chat; state in gateway.json. Part of the mgr package, only BASE.
"""
import json
import os
import re
import struct
import threading

GATEWAY_FILE = None
_gw_lock = threading.Lock()
try:
    from text_unicode import clean_text as _clean_unicode
except Exception:
    _clean_unicode = None


def configure(base):
    global GATEWAY_FILE
    GATEWAY_FILE = os.path.join(base, "gateway.json")


# ---- Security Gateway ------------------------------------------------------
# Toggleable per chat. Two things, both in the manager, not in the guest:
#
#   Text   strip invisible characters — tag chars (U+E0020..E007F), zero-width,
#          bidi overrides, homoglyph spaces. That is the channel used to slip an
#          agent instructions that simply are not visible in the chat window.
#          Filtered in BOTH directions: a reply lands in chats.json and is read
#          again later.
#   Images strip EXIF/XMP/C2PA before the image leaves the host. A phone photo
#          carries GPS coordinates, device serial and capture time.
#
# The state deliberately lives HERE and not in the chat object: chats.json is
# merged between app and web, and every extra field there has so far proven to
# be a breaking point.
_gateway_lock = threading.Lock()



def load_gateway():
    try:
        with open(GATEWAY_FILE) as fh:
            d = json.load(fh)
            return {"chats": d.get("chats") or {}, "stats": d.get("stats") or {}}
    except (FileNotFoundError, ValueError):
        return {"chats": {}, "stats": {}}


def save_gateway(d):
    with _gateway_lock:
        try:
            with open(GATEWAY_FILE, "w") as fh:
                json.dump(d, fh)
            return True
        except OSError:
            return False


def gateway_on(chat_id):
    """Without a chat id the gateway is off — a caller that doesn't say which
    chat it belongs to cannot have been ticked on either."""
    if not chat_id or _clean_unicode is None:
        return False
    return bool(load_gateway()["chats"].get(str(chat_id)))


def gateway_count(chat_id, key, n):
    """Count removed characters/images. Filtering silently would be the worst
    part: you want to see that something was in there."""
    if not n:
        return
    with _gateway_lock:
        d = load_gateway()
        s = d["stats"].setdefault(str(chat_id), {})
        s[key] = s.get(key, 0) + n
        try:
            with open(GATEWAY_FILE, "w") as fh:
                json.dump(d, fh)
        except OSError:
            pass


def gateway_clean(text, chat_id, key):
    """Clean and count text. Returns the text unchanged when the gateway is
    off."""
    if not text or not gateway_on(chat_id):
        return text
    out, st = _clean_unicode(text)
    gateway_count(chat_id, key, st.get("removed_count", 0) + st.get("replaced_count", 0))
    return out


class StreamGuard:
    """Cleans a token stream without stalling it.

    Cut at the word boundary: Unicode glue (ZWJ in emoji chains, tag chars)
    always hangs on a character, never on a space. Cutting stubbornly every
    4 KB instead tears an emoji chain apart and the filter sees a joiner with
    no lead-in — and throws it away."""

    def __init__(self, chat_id, key="out"):
        self.chat_id = chat_id
        self.key = key
        self.buf = ""
        self.removed = 0

    def feed(self, chunk):
        self.buf += chunk
        cut = max(self.buf.rfind(" "), self.buf.rfind("\n"))
        if cut < 0:
            return ""
        head, self.buf = self.buf[:cut + 1], self.buf[cut + 1:]
        return self._clean(head)

    def flush(self):
        head, self.buf = self.buf, ""
        out = self._clean(head)
        if self.removed:
            gateway_count(self.chat_id, self.key, self.removed)
            self.removed = 0
        return out

    def _clean(self, s):
        if not s:
            return ""
        out, st = _clean_unicode(s)
        self.removed += st.get("removed_count", 0) + st.get("replaced_count", 0)
        return out


def strip_image_meta(b64):
    """Cut EXIF/XMP/C2PA out of a base64 image. (returns cleaned image, removed blocks)

    By hand instead of with Pillow: Pillow isn't installed here, and re-encoding
    would also touch the image lossily. Here the image data stays byte-for-byte
    identical, only metadata is dropped. For anything not reliably recognised
    the image is left untouched — a broken image would be worse than a
    timestamp inside it."""
    if not b64:
        return b64, 0
    prefix = ""
    payload = b64
    if b64.startswith("data:"):
        head, _, payload = b64.partition(",")
        prefix = head + ","
    try:
        raw = base64.b64decode(payload, validate=True)
    except Exception:
        return b64, 0

    out, n = raw, 0
    if raw[:2] == b"\xff\xd8":                       # JPEG
        out, n = _jpeg_strip(raw)
    elif raw[:8] == b"\x89PNG\r\n\x1a\n":            # PNG
        out, n = _png_strip(raw)
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        out, n = _webp_strip(raw)
    if not n:
        return b64, 0
    return prefix + base64.b64encode(out).decode(), n


def _jpeg_strip(raw):
    """Strip APP1..APP15 (Exif, XMP, C2PA/JUMBF) — APP0/JFIF stays, that's the
    image header. After it come SOS and the compressed rest; from there on
    nothing is touched."""
    out = bytearray(raw[:2])
    i, n = 2, 0
    while i + 4 <= len(raw):
        if raw[i] != 0xFF:
            break
        m = raw[i + 1]
        if m == 0xDA:                                # Start of Scan -> Rest 1:1
            out += raw[i:]
            return bytes(out), n
        ln = int.from_bytes(raw[i + 2:i + 4], "big")
        if ln < 2 or i + 2 + ln > len(raw):
            return raw, 0                            # unexpected -> do not touch
        if 0xE1 <= m <= 0xEF or m == 0xFE:           # APP1..APP15, COM
            n += 1
        else:
            out += raw[i:i + 2 + ln]
        i += 2 + ln
    if i < len(raw):
        out += raw[i:]
    return bytes(out), n


def _png_strip(raw):
    """Strip text chunks and eXIf. PNG is built from chunks with a length and
    checksum, which separate cleanly."""
    drop = {b"eXIf", b"tEXt", b"iTXt", b"zTXt", b"tIME", b"caBX"}
    out = bytearray(raw[:8])
    i, n = 8, 0
    while i + 8 <= len(raw):
        ln = int.from_bytes(raw[i:i + 4], "big")
        typ = raw[i + 4:i + 8]
        end = i + 12 + ln
        if end > len(raw):
            return raw, 0
        if typ in drop:
            n += 1
        else:
            out += raw[i:end]
        i = end
        if typ == b"IEND":
            break
    return bytes(out), n


def _webp_strip(raw):
    """EXIF/XMP chunks out of the RIFF container. The total length in the header
    must be correct afterwards, else some viewers consider the file broken."""
    out = bytearray(raw[:12])
    i, n = 12, 0
    while i + 8 <= len(raw):
        typ = raw[i:i + 4]
        ln = int.from_bytes(raw[i + 4:i + 8], "little")
        end = i + 8 + ln + (ln & 1)                  # blocks are of even length
        if end > len(raw):
            return raw, 0
        if typ in (b"EXIF", b"XMP "):
            n += 1
        else:
            out += raw[i:end]
        i = end
    if not n:
        return raw, 0
    out[4:8] = (len(out) - 8).to_bytes(4, "little")
    return bytes(out), n


# ---- Leak filter: secrets must not leave the system in a message ----------
# Applied to OUTGOING channels (notify, send_signal): known key patterns are
# replaced rather than blocked — the message arrives, the secret does not.
# Deliberately NO generic 40-hex pattern (it would hit every git SHA).
_LEAK_PATTERNS = [
    re.compile(r"sk-or-v1-[A-Za-z0-9]{16,}"),        # OpenRouter
    re.compile(r"sk-orca-[A-Za-z0-9]{8,}"),           # OrcaRouter
    re.compile(r"sk-ant-[A-Za-z0-9-]{16,}"),          # Anthropic
    re.compile(r"sk-[A-Za-z0-9]{32,}"),               # OpenAI u.ae.
    re.compile(r"ptr_[A-Za-z0-9+/=]{16,}"),           # Portainer
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),        # GitHub
    re.compile(r"AKIA[0-9A-Z]{16}"),                  # AWS Access Key
    re.compile(r"hf_[A-Za-z0-9]{30,}"),               # HuggingFace
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),      # Slack
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}"),  # JWT
]


def redact_secrets(text):
    """(gefilterter_text, anzahl_funde)."""
    out = str(text or "")
    hits = 0
    for pat in _LEAK_PATTERNS:
        out, n = pat.subn("[SECRET entfernt]", out)
        hits += n
    return out, hits
