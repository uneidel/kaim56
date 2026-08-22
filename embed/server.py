#!/usr/bin/env python3
"""Embedding service for kAIm56's semantic memory.

Runs on the host, not in the VMs: the model (~120 MB) would otherwise sit in
memory per instance, and embeddings are the one ML step that is genuinely fast
on the CPU. Bound to 0.0.0.0 in the container, restricted to 127.0.0.1 on the
host side (-p 127.0.0.1:8772:8772), just like the voice service. Reachable only
through the manager.

  POST /embed  {"texts": [...], "kind": "query"|"passage"} -> {"vectors": [...]}
  GET  /health -> {"ready": bool, "model": ..., "dim": ...}

The model (multilingual-e5) wants prefixes: search queries as "query: …",
stored texts as "passage: …". Without that the match quality drops noticeably.
The service sets them itself, the caller sends only `kind`.
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sentence_transformers import SentenceTransformer

PORT = int(os.environ.get("PORT", "8772"))
HOST = os.environ.get("HOST", "0.0.0.0")
MODEL_NAME = os.environ.get("EMBED_MODEL", "intfloat/multilingual-e5-small")
MAX_BODY = 8 * 1024 * 1024
MAX_TEXTS = 128

_model = None
_lock = threading.Lock()


def model():
    global _model
    with _lock:
        if _model is None:
            _model = SentenceTransformer(MODEL_NAME, device="cpu")
            print(f"[embed] {MODEL_NAME} geladen, dim={_model.get_sentence_embedding_dimension()}",
                  flush=True)
    return _model


def _prefix(texts, kind):
    tag = "query: " if kind == "query" else "passage: "
    return [tag + (t or "") for t in texts]


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/health"):
            m = _model
            return self._json(200, {"ready": m is not None, "model": MODEL_NAME,
                                    "dim": m.get_sentence_embedding_dimension() if m else None})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/embed"):
            return self._json(404, {"error": "not found"})
        ln = int(self.headers.get("Content-Length", 0) or 0)
        if ln > MAX_BODY:
            return self._json(413, {"error": "body too large"})
        try:
            b = json.loads(self.rfile.read(ln) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "bad json"})
        texts = b.get("texts")
        if isinstance(texts, str):
            texts = [texts]
        if not isinstance(texts, list) or not texts:
            return self._json(400, {"error": "no texts"})
        if len(texts) > MAX_TEXTS:
            return self._json(413, {"error": f"max {MAX_TEXTS} texts"})
        kind = "query" if b.get("kind") == "query" else "passage"
        try:
            m = model()
            vecs = m.encode(_prefix([str(t) for t in texts], kind),
                            normalize_embeddings=True)   # -> Cosinus = Skalarprodukt
            out = [v.tolist() for v in vecs]
            return self._json(200, {"vectors": out, "dim": len(out[0]), "model": MODEL_NAME})
        except Exception as e:
            return self._json(500, {"error": repr(e)[:300]})


if __name__ == "__main__":
    threading.Thread(target=model, daemon=True).start()   # im Hintergrund vorladen
    print(f"[embed] hoert auf {HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
