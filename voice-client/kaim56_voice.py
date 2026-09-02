#!/usr/bin/env python3
# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sprachclient fuer den Fedora-Desktop: Topbar-Icon, VAD, freihaendig reden.

Immer-an-Mikrofon mit Energie-VAD: eine erkannte Aeusserung geht als Audio an
den Manager (/api/stt), der Text an die gewaehlte Instanz (/api/chat/<name>,
Streaming), die Antwort kommt gesprochen zurueck (/api/tts -> Piper-WAV).
Waehrend Denken und Sprechen ist das Mikrofon stumm — sonst hoert sich der
Client selbst zu.

Der Client haelt KEINE Konversation: der Agent in der VM traegt seinen eigenen
Verlauf, Mehrfach-Turns funktionieren also von allein; "Neues Gespraech" im
Menue schickt schlicht /reset.

Abhaengigkeiten: nur stdlib fuer Audio/VAD/HTTP. Aufnahme/Wiedergabe laufen
ueber PipeWire-Werkzeuge als Subprozess (parec/pw-record/arecord bzw.
paplay/pw-play/aplay — das erste, das da ist). Das Topbar-Icon braucht
PyGObject + AppIndicator (auf Fedora vorinstalliert bzw. ein dnf install,
siehe README); ohne beides laeuft der Client headless weiter.

VAD statt Push-to-talk, bewusst simpel: RMS je 30-ms-Frame gegen einen
adaptiven Rauschteppich. Kein webrtcvad-Wheel, kein Modell — fuer ein
Schreibtischmikro reicht Energie plus Mindestdauer, und es bleibt stdlib
(audioop ist in Python 3.13 gestrichen, daher Summe der Quadrate von Hand).
"""
import argparse
import array
import json
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode

RATE = 16000
FRAME_MS = 30
FRAME_BYTES = RATE * FRAME_MS // 1000 * 2      # s16le mono
CONFIG_PATH = os.path.expanduser("~/.config/kaim56-voice.json")

CONFIG_TEMPLATE = {
    "base_url": "http://manager.example:8700",
    "user": "admin",
    "pass": "geheim",
    "instance": "myassistant",
    "vad": {"start_frames": 5, "end_ms": 800, "min_ms": 400, "max_s": 30,
            "threshold_factor": 3.0, "threshold_min": 350},
}


def load_config(path=CONFIG_PATH):
    """Config lesen; VAD-Luecken werden mit den Defaults aufgefuellt."""
    with open(path) as fh:
        cfg = json.load(fh)
    for k in ("base_url", "user", "pass"):
        if not cfg.get(k):
            raise ValueError(f"config: '{k}' fehlt in {path}")
    cfg.setdefault("instance", "myassistant")
    vad = dict(CONFIG_TEMPLATE["vad"])
    vad.update(cfg.get("vad") or {})
    cfg["vad"] = vad
    return cfg


def write_config_template(path=CONFIG_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(CONFIG_TEMPLATE, fh, indent=2)
    os.chmod(path, 0o600)              # da steht ein Passwort drin


# ---- VAD -------------------------------------------------------------------
def frame_rms(frame: bytes) -> int:
    """RMS eines s16le-Frames — audioop gibt es ab Python 3.13 nicht mehr."""
    if len(frame) < 2:
        return 0
    a = array.array("h")
    a.frombytes(frame[: len(frame) & ~1])
    if sys.byteorder == "big":
        a.byteswap()
    return int((sum(v * v for v in a) / len(a)) ** 0.5)


class Vad:
    """Energie-VAD ueber 30-ms-Frames mit adaptivem Rauschteppich.

    feed(frame) liefert None oder — am Ende einer Aeusserung — die kompletten
    PCM-Bytes inklusive Vorlauf. Zustandsautomat: IDLE (Ring von 8 Frames
    Vorlauf) -> TALK (sammeln) -> zurueck zu IDLE nach end_ms Stille. Der
    Teppich lernt nur in IDLE: Sprache darf den eigenen Schwellwert nicht
    hochziehen. Zu kurze Segmente (< min_ms) sind Tuerknallen, kein Satz —
    verworfen."""

    def __init__(self, start_frames=5, end_ms=800, min_ms=400, max_s=30,
                 threshold_factor=3.0, threshold_min=350):
        self.start_frames = start_frames
        self.end_frames = max(1, end_ms // FRAME_MS)
        self.min_frames = max(1, min_ms // FRAME_MS)
        self.max_frames = max(1, int(max_s * 1000) // FRAME_MS)
        self.factor = threshold_factor
        self.thr_min = threshold_min
        self.noise = 200.0
        self.ring = []                  # Vorlauf, damit der Satzanfang mitkommt
        self.talk = None                # None = IDLE, sonst Liste der Frames
        self.voiced_recent = []
        self.silent = 0

    @property
    def threshold(self):
        return max(self.noise * self.factor, self.thr_min)

    def feed(self, frame: bytes):
        rms = frame_rms(frame)
        voiced = rms > self.threshold
        if self.talk is None:
            # Teppich als traege EMA — aber NUR aus unstimmhaften Frames:
            # sonst zieht lauteres Sprechen den eigenen Schwellwert in
            # Sekunden ueber den Sprach-RMS und alles gilt als Stille.
            # Stimmhaft lernt nur homoeopathisch (Ventil, falls die Umgebung
            # dauerhaft lauter wird als der alte Teppich).
            self.noise += (0.1 if not voiced else 0.002) * (rms - self.noise)
            self.ring.append(frame)
            if len(self.ring) > 8:
                self.ring.pop(0)
            self.voiced_recent.append(voiced)
            if len(self.voiced_recent) > 8:
                self.voiced_recent.pop(0)
            if sum(self.voiced_recent) >= self.start_frames:
                self.talk = list(self.ring)
                self.silent = 0
                self.voiced_recent = []
            return None
        self.talk.append(frame)
        self.silent = 0 if voiced else self.silent + 1
        if self.silent >= self.end_frames or len(self.talk) >= self.max_frames:
            seg, self.talk, self.ring = self.talk, None, []
            spoken = len(seg) - self.silent
            if spoken < self.min_frames:
                return None
            return b"".join(seg)
        return None

    def reset(self):
        self.talk, self.ring, self.voiced_recent, self.silent = None, [], [], 0


def wav_wrap(pcm: bytes, rate=RATE) -> bytes:
    """s16le-mono-PCM als WAV — /api/stt nimmt zwar jedes Format, aber mit
    Header muss ffmpeg drueben nichts raten."""
    hdr = struct.pack(
        "<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(pcm), b"WAVE", b"fmt ",
        16, 1, 1, rate, rate * 2, 2, 16, b"data", len(pcm))
    return hdr + pcm


# ---- Text fuers Vorlesen ---------------------------------------------------
def speakable(text: str) -> str:
    """Antworttext -> vorlesbarer Text: Denk-Bloecke (⟦think⟧…⟦/think⟧, auch
    unvollstaendig), Codebloecke und Markdown-Dekor raus. Links behalten den
    Linktext, die URL faellt weg."""
    text = re.sub(r"⟦think⟧.*?(⟦/think⟧|$)", "", text, flags=re.S)
    text = re.sub(r"^\s*🔧.*$", "", text, flags=re.M)   # Tool-Statuszeilen

    text = re.sub(r"```.*?(```|$)", " Codeblock übersprungen. ", text, flags=re.S)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_`#>|]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


# ---- Manager-API -----------------------------------------------------------
class Manager:
    def __init__(self, base_url, user, password):
        self.base = base_url.rstrip("/")
        self.auth = "Basic " + b64encode(f"{user}:{password}".encode()).decode()

    def _req(self, path, data=None, ctype="application/json", timeout=60):
        req = urllib.request.Request(
            self.base + path, data=data,
            headers={"Authorization": self.auth,
                     **({"Content-Type": ctype} if data is not None else {})})
        return urllib.request.urlopen(req, timeout=timeout)

    def instances(self):
        with self._req("/api/instances") as r:
            arr = json.loads(r.read())
        return [i["name"] for i in arr
                if (i.get("config") or {}).get("TRANSPORT") == "web"]

    def stt(self, wav: bytes) -> str:
        with self._req("/api/stt", wav, "audio/wav", timeout=120) as r:
            return (json.loads(r.read()).get("text") or "").strip()

    def chat(self, instance, message, chat_id, on_token=None) -> str:
        body = json.dumps({"message": message, "chat": chat_id}).encode()
        path = f"/api/chat/{urllib.parse.quote(instance, safe='')}"
        out = []
        with self._req(path, body, timeout=600) as r:
            while True:
                chunk = r.read(1024)
                if not chunk:
                    break
                tok = chunk.decode("utf-8", "replace")
                out.append(tok)
                if on_token:
                    on_token(tok)
        return "".join(out)

    def tts(self, text: str) -> bytes:
        with self._req("/api/tts", json.dumps({"text": text}).encode(),
                       timeout=300) as r:
            return r.read()


# ---- Audio-Subprozesse -----------------------------------------------------
RECORDERS = [
    ["parec", "--rate=16000", "--channels=1", "--format=s16le",
     "--latency-msec=30"],
    ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", "-"],
    ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "raw"],
]
PLAYERS = [["paplay"], ["pw-play", "-"], ["aplay", "-q"]]


def pick_cmd(candidates):
    for cmd in candidates:
        if shutil.which(cmd[0]):
            return cmd
    return None


# ---- Der Client ------------------------------------------------------------
class VoiceClient:
    """Zustaende: aus | hört | denkt | spricht. Der Audioleser laeuft immer;
    ausserhalb von 'hört' werden Frames nur verworfen (Puffer bleibt leer,
    kein Nachlauf alter Sprache)."""

    def __init__(self, cfg, headless=False):
        self.cfg = cfg
        self.mgr = Manager(cfg["base_url"], cfg["user"], cfg["pass"])
        self.instance = cfg["instance"]
        self.vad = Vad(**cfg["vad"])
        self.chat_id = f"voice-{int(time.time())}"
        self.listening = True
        self.state = "hört"
        self.last_heard = ""
        self.last_reply = ""
        self.stop_flag = threading.Event()
        self.play_proc = None
        self.headless = headless
        self.on_state = lambda: None    # Tray haengt sich hier ein

    # -- Statuszeile ---------------------------------------------------------
    def set_state(self, s):
        self.state = s
        if self.headless:
            print(f"[{time.strftime('%H:%M:%S')}] {s}", flush=True)
        self.on_state()

    def notify(self, title, msg=""):
        if shutil.which("notify-send"):
            subprocess.run(["notify-send", "-a", "kAIm56", title, msg],
                           check=False)
        print(f"[kaim56-voice] {title}: {msg}", file=sys.stderr, flush=True)

    # -- Hauptschleife -------------------------------------------------------
    def run(self, once=False):
        cmd = pick_cmd(RECORDERS)
        if not cmd:
            self.notify("Kein Aufnahmewerkzeug",
                        "parec, pw-record oder arecord installieren.")
            return 1
        rec = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL)

        def read_frame():
            # Pipes liefern gern kurze Reads — ein VAD-Frame muss voll sein.
            buf = b""
            while len(buf) < FRAME_BYTES:
                chunk = rec.stdout.read(FRAME_BYTES - len(buf))
                if not chunk:
                    return None
                buf += chunk
            return buf

        try:
            while not self.stop_flag.is_set():
                frame = read_frame()
                if not frame:
                    if self.stop_flag.is_set():
                        break
                    self.notify("Aufnahme abgerissen", " ".join(cmd))
                    return 1
                if not self.listening or self.state != "hört":
                    self.vad.reset()
                    continue
                seg = self.vad.feed(frame)
                if seg is None:
                    continue
                self.handle_utterance(seg)
                if once:
                    break
        finally:
            rec.terminate()
        return 0

    def handle_utterance(self, pcm: bytes):
        self.set_state("denkt")
        try:
            text = self.mgr.stt(wav_wrap(pcm))
            if len(text) < 2:
                self.set_state("hört")
                return
            self.last_heard = text
            if self.headless:
                print(f"  > {text}", flush=True)
            reply = self.mgr.chat(self.instance, text, self.chat_id)
            self.last_reply = speakable(reply)
            if self.headless:
                print(f"  < {self.last_reply}", flush=True)
            self.speak(self.last_reply)
        except urllib.error.HTTPError as e:
            self.notify(f"Manager: HTTP {e.code}", e.read()[:200].decode("utf-8", "replace"))
        except Exception as e:
            self.notify("Sprach-Turn fehlgeschlagen", repr(e))
        finally:
            self.set_state("hört" if self.listening else "aus")

    def speak(self, text):
        if not text:
            return
        wav = self.mgr.tts(text)
        cmd = pick_cmd(PLAYERS)
        if not cmd:
            self.notify("Kein Abspielwerkzeug", "paplay, pw-play oder aplay installieren.")
            return
        self.set_state("spricht")
        # Ueber Tempfile statt stdin: paplay und aplay lesen WAV-Header aus
        # Dateien zuverlaessig, und "Stopp" ist ein schlichtes terminate().
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav)
            path = f.name
        try:
            play = cmd[:-1] + [path] if cmd[-1] == "-" else cmd + [path]
            self.play_proc = subprocess.Popen(play, stderr=subprocess.DEVNULL)
            self.play_proc.wait()
        finally:
            self.play_proc = None
            os.unlink(path)

    # -- Menue-Aktionen ------------------------------------------------------
    def toggle_listening(self):
        self.listening = not self.listening
        self.vad.reset()
        self.set_state("hört" if self.listening else "aus")

    def stop_speaking(self):
        p = self.play_proc
        if p:
            p.terminate()

    def new_conversation(self):
        self.chat_id = f"voice-{int(time.time())}"
        threading.Thread(target=self._send_reset, daemon=True).start()

    def _send_reset(self):
        try:
            self.mgr.chat(self.instance, "/reset", self.chat_id)
        except Exception as e:
            self.notify("/reset fehlgeschlagen", repr(e))

    def set_instance(self, name):
        self.instance = name
        self.on_state()

    def quit(self):
        self.stop_flag.set()
        self.stop_speaking()


# ---- Topbar (AppIndicator; optional) --------------------------------------
def run_tray(client):
    """Indicator in der Topbar: Status, Instanzwahl, Hoeren an/aus.
    Gibt False zurueck, wenn PyGObject/AppIndicator fehlen — dann headless."""
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3 as AppIndicator
        except (ValueError, ImportError):
            gi.require_version("AppIndicator3", "0.1")
            from gi.repository import AppIndicator3 as AppIndicator
        from gi.repository import Gtk, GLib
    except (ValueError, ImportError):
        return False

    ICONS = {"hört": "audio-input-microphone-symbolic",
             "denkt": "content-loading-symbolic",
             "spricht": "audio-speakers-symbolic",
             "aus": "microphone-sensitivity-muted-symbolic"}
    ind = AppIndicator.Indicator.new(
        "kaim56-voice", ICONS["hört"],
        AppIndicator.IndicatorCategory.APPLICATION_STATUS)
    ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)

    status_item = Gtk.MenuItem(label="…")
    status_item.set_sensitive(False)
    toggle_item = Gtk.MenuItem(label="Hören aus")
    stop_item = Gtk.MenuItem(label="Sprechen stoppen")
    fresh_item = Gtk.MenuItem(label="Neues Gespräch (/reset)")
    quit_item = Gtk.MenuItem(label="Beenden")
    inst_menu = Gtk.Menu()
    inst_root = Gtk.MenuItem(label=f"Instanz: {client.instance}")
    inst_root.set_submenu(inst_menu)

    def rebuild_instances(_w=None):
        for c in inst_menu.get_children():
            inst_menu.remove(c)
        try:
            names = client.mgr.instances()
        except Exception as e:
            names = []
            client.notify("Instanzliste fehlgeschlagen", repr(e))
        group = None
        for n in names or [client.instance]:
            it = Gtk.RadioMenuItem(label=n, group=group)
            group = group or it
            it.set_active(n == client.instance)
            it.connect("activate",
                       lambda w, name=n: w.get_active() and client.set_instance(name))
            inst_menu.append(it)
        inst_menu.show_all()

    def refresh():
        status_item.set_label(
            f"{client.instance} — {client.state}"
            + (f"  „{client.last_heard[:40]}“" if client.last_heard else ""))
        toggle_item.set_label("Hören aus" if client.listening else "Hören an")
        inst_root.set_label(f"Instanz: {client.instance}")
        ind.set_icon_full(ICONS.get(client.state, ICONS["aus"]), client.state)

    client.on_state = lambda: GLib.idle_add(refresh)
    toggle_item.connect("activate", lambda w: client.toggle_listening())
    stop_item.connect("activate", lambda w: client.stop_speaking())
    fresh_item.connect("activate", lambda w: client.new_conversation())
    inst_root.connect("activate", rebuild_instances)
    quit_item.connect("activate", lambda w: (client.quit(), Gtk.main_quit()))

    menu = Gtk.Menu()
    for it in (status_item, Gtk.SeparatorMenuItem(), toggle_item, stop_item,
               fresh_item, inst_root, Gtk.SeparatorMenuItem(), quit_item):
        menu.append(it)
    menu.show_all()
    ind.set_menu(menu)
    rebuild_instances()
    refresh()

    worker = threading.Thread(target=client.run, daemon=True)
    worker.start()
    signal.signal(signal.SIGINT, lambda *a: (client.quit(), Gtk.main_quit()))
    Gtk.main()
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description="kAIm56-Sprachclient (VAD, Topbar)")
    ap.add_argument("--config", default=CONFIG_PATH)
    ap.add_argument("--instance", help="Zielinstanz (statt Config-Wert)")
    ap.add_argument("--headless", action="store_true",
                    help="ohne Topbar-Icon, Status auf stdout")
    ap.add_argument("--once", action="store_true",
                    help="eine Aeusserung verarbeiten, dann beenden (Test)")
    args = ap.parse_args(argv)

    if not os.path.exists(args.config):
        write_config_template(args.config)
        print(f"Config-Vorlage nach {args.config} geschrieben — bitte "
              f"base_url/user/pass eintragen und neu starten.", file=sys.stderr)
        return 2
    cfg = load_config(args.config)
    if args.instance:
        cfg["instance"] = args.instance

    client = VoiceClient(cfg, headless=args.headless or args.once)
    if not args.headless and not args.once:
        if run_tray(client):
            return 0
        print("PyGObject/AppIndicator nicht gefunden — laufe headless "
              "(siehe README fuer das Topbar-Icon).", file=sys.stderr)
        client.headless = True
    signal.signal(signal.SIGINT, lambda *a: client.quit())
    return client.run(once=args.once)


if __name__ == "__main__":
    sys.exit(main())
