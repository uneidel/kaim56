# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""OpenAI speech (security boundary: audio and text leave the host): the
optional cloud side of /api/stt and /api/tts, per direction by Settings
STT_BACKEND / TTS_BACKEND = "openai". The key stays here, no client or VM sees
it. stt() and tts() answer None when off, rate-limited or failed — the caller
then uses the local voice service (Parakeet, Piper), so voice never depends on
the internet. VMs may call these routes too: the rate cap bounds what that costs.

TTS asks for raw PCM and adds the WAV header here: the clients parse WAV, and a
streamed header carries no sizes. Note: the current /v1/audio/speech models
shut down on 2027-01-06 (OpenAI names no successor on this endpoint).

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import io
import json
import urllib.request
import uuid
import wave

from mgr import settings as _settings
from mgr import util as _util

API = "https://api.openai.com/v1/audio/"
TIMEOUT = 60
MAX_CALLS, WINDOW = 120, 600           # per direction, then the local service answers
TEXT_MAX = 4096                        # /v1/audio/speech's limit
PCM_RATE = 24000                       # its pcm: 24 kHz, 16 bit signed, mono
EXT = {"audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav", "audio/webm": "webm", "audio/ogg": "ogg",
       "audio/mp4": "m4a", "audio/m4a": "m4a", "audio/mpeg": "mp3", "audio/flac": "flac"}


def _key(direction):
    """The API key when this direction is on and within its rate, else ''."""
    st = _settings.load_settings()
    key = str(st.get("OPENAI_API_KEY") or "").strip()
    if st.get(f"{direction}_BACKEND") != "openai" or not key:
        return ""
    return key if _util.rate_ok(("voicecloud", direction), MAX_CALLS, WINDOW) else ""


def _call(path, key, data, ctype):
    req = urllib.request.Request(API + path, data=data, method="POST",
                                 headers={"Authorization": "Bearer " + key, "Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def _multipart(fields, ext, audio):
    b = uuid.uuid4().hex
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items()]
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="audio.{ext}"\r\n'
                 f'Content-Type: application/octet-stream\r\n\r\n'.encode() + audio + b"\r\n")
    return b"".join(parts) + f"--{b}--\r\n".encode(), f"multipart/form-data; boundary={b}"


def stt(audio, ctype):
    """{"text", "seconds"} of the audio, or None (a format OpenAI takes no: None too, ffmpeg handles it locally)."""
    ext = EXT.get(str(ctype or "").split(";")[0].strip().lower())
    key = _key("STT") if ext and audio else ""
    if not key:
        return None
    model = _settings.load_settings().get("OPENAI_STT_MODEL") or "gpt-transcribe"
    try:
        body, mp = _multipart({"model": model}, ext, audio)
        return {"text": str(json.loads(_call("transcriptions", key, body, mp)).get("text") or ""), "seconds": None}
    except Exception as e:
        print(f"[voicecloud] stt failed, using the local service: {e!r}"[:300], flush=True)
        return None


def tts(text, speed=None):
    """WAV bytes of the text, or None."""
    key = _key("TTS")
    if not key:
        return None
    st = _settings.load_settings()
    b = {"model": st.get("OPENAI_TTS_MODEL") or "gpt-4o-mini-tts", "voice": st.get("OPENAI_TTS_VOICE") or "marin",
         "input": str(text)[:TEXT_MAX], "response_format": "pcm"}
    if speed:
        b["speed"] = max(0.25, min(float(speed), 4.0))
    try:
        pcm = _call("speech", key, json.dumps(b).encode(), "application/json")
    except Exception as e:
        print(f"[voicecloud] tts failed, using the local service: {e!r}"[:300], flush=True)
        return None
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(PCM_RATE)
        w.writeframes(pcm)
    return out.getvalue()
