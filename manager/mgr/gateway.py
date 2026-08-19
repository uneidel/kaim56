"""Security Gateway: unsichtbare-Unicode-Filter (Text) + Bild-Metadaten-Strip.
Pro Chat schaltbar; Zustand in gateway.json. Teil des mgr-Pakets, nur BASE.
"""
import json
import os
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
# Pro Chat ankreuzbar. Zwei Dinge, beide am Manager, nicht im Gast:
#
#   Text   unsichtbare Zeichen raus — Tag-Zeichen (U+E0020..E007F), Zero-Width,
#          Bidi-Overrides, Homoglyph-Leerzeichen. Das ist der Kanal, ueber den
#          man einem Agenten Anweisungen unterschiebt, die im Chatfenster
#          schlicht nicht zu sehen sind. Gefiltert wird in BEIDE Richtungen:
#          eine Antwort landet in chats.json und wird spaeter wieder gelesen.
#   Bilder EXIF/XMP/C2PA raus, bevor das Bild den Host verlaesst. Ein Foto vom
#          Handy traegt GPS-Koordinaten, Geraetenummer und Aufnahmezeit mit.
#
# Der Zustand liegt bewusst HIER und nicht im Chat-Objekt: chats.json wird
# zwischen App und Web gemerged, und jedes zusaetzliche Feld dort hat sich
# bisher als Bruchstelle erwiesen.
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
    """Ohne Chat-Kennung ist das Gateway aus — ein Aufrufer, der nicht sagt,
    zu welchem Chat er gehoert, kann auch nicht angehakt worden sein."""
    if not chat_id or _clean_unicode is None:
        return False
    return bool(load_gateway()["chats"].get(str(chat_id)))


def gateway_count(chat_id, key, n):
    """Entfernte Zeichen/Bilder mitzaehlen. Still zu filtern waere das
    Unangenehmste: man will sehen, dass etwas drin war."""
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
    """Text saeubern und zaehlen. Gibt den Text unveraendert zurueck, wenn das
    Gateway aus ist."""
    if not text or not gateway_on(chat_id):
        return text
    out, st = _clean_unicode(text)
    gateway_count(chat_id, key, st.get("removed_count", 0) + st.get("replaced_count", 0))
    return out


class StreamGuard:
    """Saeubert einen Token-Strom, ohne ihn zu stauen.

    Geschnitten wird an der Wortgrenze: Unicode-Kleber (ZWJ in Emoji-Ketten,
    Tag-Zeichen) haengt immer an einem Zeichen, nie an einem Leerzeichen. Wer
    stur alle 4 KB schneidet, zerreisst dagegen eine Emoji-Kette und der
    Filter sieht einen Verbinder ohne Vorzeichen — und wirft ihn weg."""

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
    """EXIF/XMP/C2PA aus einem Base64-Bild schneiden. (bereinigt, entfernte Bloecke)

    Von Hand statt mit Pillow: Pillow ist hier nicht installiert, und ein
    Neu-Kodieren wuerde das Bild ausserdem verlustbehaftet anfassen. Hier
    bleiben die Bilddaten Byte fuer Byte gleich, es fallen nur Metadaten weg.
    Bei allem, was nicht sicher erkannt wird, bleibt das Bild unangetastet —
    ein kaputtes Bild waere schlimmer als ein Zeitstempel darin."""
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
    """APP1..APP15 raus (Exif, XMP, C2PA/JUMBF) — APP0/JFIF bleibt, das ist
    der Bildkopf. Danach kommt SOS und der komprimierte Rest; ab dort wird
    nichts mehr angefasst."""
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
            return raw, 0                            # unerwartet -> nicht anfassen
        if 0xE1 <= m <= 0xEF or m == 0xFE:           # APP1..APP15, COM
            n += 1
        else:
            out += raw[i:i + 2 + ln]
        i += 2 + ln
    if i < len(raw):
        out += raw[i:]
    return bytes(out), n


def _png_strip(raw):
    """Textbloecke und eXIf raus. PNG ist in Bloecken mit Laenge und Pruefsumme
    aufgebaut, das laesst sich sauber trennen."""
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
    """EXIF/XMP-Bloecke aus dem RIFF-Container. Die Gesamtlaenge im Kopf muss
    danach stimmen, sonst halten manche Betrachter die Datei fuer defekt."""
    out = bytearray(raw[:12])
    i, n = 12, 0
    while i + 8 <= len(raw):
        typ = raw[i:i + 4]
        ln = int.from_bytes(raw[i + 4:i + 8], "little")
        end = i + 8 + ln + (ln & 1)                  # Bloecke sind gerade lang
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


