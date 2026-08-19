#!/usr/bin/env python3
"""Sprachdienst fuer kAIm56: Erkennung (Parakeet) und Sprachausgabe (Piper).

Laeuft auf dem Host, nicht in den microVMs: die Modelle brauchen zusammen gut
700 MB und wuerden sonst pro Instanz im Speicher liegen. Gebunden auf 127.0.0.1
— erreichbar ist der Dienst nur ueber den Manager, genau wie der katfs-Knoten.
Der Manager weiss ueber die Quell-IP, wer anruft; hier gibt es keine eigene
Rechteverwaltung, weil er nie direkt erreichbar sein soll.

  POST /stt   Audio (beliebiges Format)      -> {"text": …, "seconds": …}
  POST /tts   {"text": …}                    -> audio/wav
  GET  /health                               -> {"ready": bool, …}
"""
import json
import os
import subprocess
import tempfile
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import onnx_asr

PORT = int(os.environ.get("PORT", "8770"))
# Im Container an 0.0.0.0 binden: Dockers Portweiterleitung erreicht das
# Container-Loopback NICHT. Die Beschraenkung sitzt auf der Host-Seite der
# Zuordnung (-p 127.0.0.1:8770:8770) — von aussen ist der Dienst damit
# genauso unerreichbar wie der katfs-Knoten, nur eine Ebene hoeher.
HOST = os.environ.get("HOST", "0.0.0.0")
PIPER = os.environ.get("PIPER_BIN", "/opt/piper/piper")
VOICE = os.environ.get("PIPER_VOICE", "/opt/piper/de-thorsten-medium.onnx")
ASR_NAME = os.environ.get("ASR_MODEL", "nemo-parakeet-tdt-0.6b-v3")
MAX_BODY = 32 * 1024 * 1024      # 32 MB reichen fuer mehrere Minuten Sprache
MAX_TEXT = 4000                  # laengere Antworten werden vor dem Sprechen gekuerzt

_asr = None
_asr_lock = threading.Lock()


def asr():
    """Modell beim ersten Aufruf laden (~2 s aus dem Cache) und behalten."""
    global _asr
    with _asr_lock:
        if _asr is None:
            t0 = time.time()
            _asr = onnx_asr.load_model(ASR_NAME, quantization="int8")
            print(f"[voice] ASR geladen in {time.time()-t0:.1f}s", flush=True)
    return _asr


def to_wav16k(raw):
    """Eingang beliebig (Opus/OGG von Signal, AAC/M4A von Android, WAV) ->
    16 kHz mono PCM. Ohne diesen Schritt scheitert die Erkennung an allem,
    was kein WAV ist."""
    src = tempfile.NamedTemporaryFile(suffix=".in", delete=False)
    src.write(raw)
    src.close()
    dst = src.name + ".wav"
    try:
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-i", src.name, "-ac", "1", "-ar", "16000", "-f", "wav", dst],
                       check=True, capture_output=True)
    finally:
        os.unlink(src.name)
    return dst


def list_voices():
    """Verfuegbare Piper-Stimmen: alle *.onnx im Piper-Verzeichnis."""
    d = os.path.dirname(VOICE)
    try:
        return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".onnx"))
    except OSError:
        return [os.path.basename(VOICE)[:-5]]


def _voice_path(name):
    """Stimmen-Namen path-sicher aufloesen; unbekannt/leer -> Default."""
    if not name:
        return VOICE
    base = os.path.basename(str(name))
    if not base.endswith(".onnx"):
        base += ".onnx"
    p = os.path.join(os.path.dirname(VOICE), base)
    return p if os.path.exists(p) else VOICE


def speak(text, voice="", speed=1.0):
    """Piper laeuft als Prozess je Anfrage — bei 0,07 Echtzeitfaktor ist der
    Start teurer als die Synthese, aber das haelt den Dienst zustandslos.
    speed >1 = schneller (Piper: length_scale = 1/speed), geklemmt 0.5–2.0."""
    try:
        speed = min(2.0, max(0.5, float(speed or 1.0)))
    except (TypeError, ValueError):
        speed = 1.0
    out = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    out.close()
    cmd = [PIPER, "--model", _voice_path(voice), "--output_file", out.name]
    if abs(speed - 1.0) > 0.01:
        cmd += ["--length_scale", f"{1.0 / speed:.3f}"]
    p = subprocess.run(cmd, input=text.encode(), capture_output=True)
    if p.returncode != 0:
        os.unlink(out.name)
        raise RuntimeError(p.stderr.decode()[:300])
    return out.name


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/health"):
            return self._json(200, {"ready": _asr is not None,
                                    "voice": os.path.basename(VOICE)[:-5],
                                    "voices": list_voices(), "asr": ASR_NAME})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0) or 0)
        if ln > MAX_BODY:
            return self._json(413, {"error": "body too large"})
        raw = self.rfile.read(ln) if ln else b""

        if self.path.startswith("/stt"):
            if not raw:
                return self._json(400, {"error": "no audio"})
            wav = None
            try:
                t0 = time.time()
                wav = to_wav16k(raw)
                with wave.open(wav) as w:
                    secs = w.getnframes() / w.getframerate()
                text = asr().recognize(wav)
                return self._json(200, {"text": text, "seconds": round(secs, 2),
                                        "took": round(time.time() - t0, 2)})
            except subprocess.CalledProcessError as e:
                return self._json(415, {"error": "audio not decodable",
                                        "detail": e.stderr.decode()[:200]})
            except Exception as e:
                return self._json(500, {"error": repr(e)[:300]})
            finally:
                if wav and os.path.exists(wav):
                    os.unlink(wav)

        if self.path.startswith("/tts"):
            try:
                b = json.loads(raw or b"{}")
                text = (b.get("text") or "").strip()[:MAX_TEXT]
            except json.JSONDecodeError:
                return self._json(400, {"error": "bad json"})
            if not text:
                return self._json(400, {"error": "no text"})
            out = None
            try:
                t0 = time.time()
                out = speak(text, b.get("voice", ""), b.get("speed", 1.0))
                data = open(out, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-Took", f"{time.time()-t0:.2f}")
                self.end_headers()
                self.wfile.write(data)
                return
            except Exception as e:
                return self._json(500, {"error": repr(e)[:300]})
            finally:
                if out and os.path.exists(out):
                    os.unlink(out)

        self._json(404, {"error": "not found"})


if __name__ == "__main__":
    threading.Thread(target=asr, daemon=True).start()   # im Hintergrund vorladen
    print(f"[voice] hoert auf {HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
