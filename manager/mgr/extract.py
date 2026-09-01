# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Document text extraction for chat attachments.

The app can attach images (they go to the model as vision input), but a PDF or
DOCX is not an image: what the model needs is the TEXT. The app therefore
uploads the file here, and the extracted text travels into the chat message.

Standard library only, like everything in the manager:
- DOCX/ODT are ZIP containers with XML inside — zipfile + a tag strip.
- PDF tries three ways, best first: ``pdftotext`` when the host has
  poppler-utils; the ``kaim56-pdftotext`` Docker image (alpine + poppler,
  built once — the host already runs Docker services, and this covers the
  CID/subset-font PDFs a real inbox is full of); and finally a small built-in
  extractor (zlib + text operators) for simply-encoded PDFs. When all three
  fail, the caller gets an honest error instead of gibberish (a heuristic
  checks readability).
- Plain text/Markdown/CSV pass through with decoding.

Part of the mgr package: no imports from manager.py (no cycles).
"""
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
import zlib

MAX_CHARS = 80_000        # cap for the extracted text that goes into a chat turn


def extract_document(name, data):
    """(text, note) for an uploaded file; raises ValueError with a readable
    message when the format is unsupported or the content unreadable."""
    ext = (name.rsplit(".", 1)[-1].lower() if "." in name else "")
    if ext == "pdf" or data[:5] == b"%PDF-":
        text = _pdf_text(data)
    elif ext in ("docx", "odt") or data[:2] == b"PK":
        text = _zip_xml_text(data, ext or "docx")
    elif ext in ("txt", "md", "csv", "log", "json", "xml", "html", "htm", ""):
        text = data.decode("utf-8", "replace")
        if ext in ("html", "htm"):
            text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
    else:
        raise ValueError(f"unsupported file type '.{ext}' — "
                         "supported: pdf, docx, odt, txt, md, csv, html")
    text = re.sub(r"[ \t]+\n", "\n", text).strip()
    if not text:
        raise ValueError("no extractable text found in the document")
    note = ""
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
        note = f"truncated to {MAX_CHARS} characters"
    return text, note


# ---- DOCX / ODT ------------------------------------------------------------

def _zip_xml_text(data, ext):
    import io
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("not a valid Office file (broken ZIP container)")
    # DOCX: word/document.xml — ODT: content.xml. Fall back to any XML that
    # looks like a document body.
    for member in ("word/document.xml", "content.xml"):
        if member in zf.namelist():
            return _xml_to_text(zf.read(member))
    raise ValueError("no document body found — is this really a DOCX/ODT?")


def _xml_to_text(xml_bytes):
    """Paragraphs as lines, tags gone. Namespace-agnostic on purpose: the
    Word and ODT namespaces differ, the local names (p, tab, br) do not."""
    root = ET.fromstring(xml_bytes)
    out = []

    def local(tag):
        return tag.rsplit("}", 1)[-1]

    def walk(el, buf):
        t = local(el.tag)
        if t == "tab":
            buf.append("\t")
        elif t == "br":
            buf.append("\n")
        if el.text:
            buf.append(el.text)
        for child in el:
            walk(child, buf)
            if child.tail:
                buf.append(child.tail)

    for p in root.iter():
        if local(p.tag) == "p":
            buf = []
            walk(p, buf)
            out.append("".join(buf))
    if not out:                       # no paragraph structure -> all text nodes
        buf = []
        walk(root, buf)
        out = ["".join(buf)]
    return "\n".join(out)


# ---- PDF -------------------------------------------------------------------

PDF_IMAGE = "kaim56-pdftotext"       # alpine + poppler-utils, ENTRYPOINT pdftotext
_docker_image_ok = None              # cached probe: is Docker + image available?


def _pdf_text_docker(data):
    """pdftotext from the container — no network, read-only mount, hard cap."""
    global _docker_image_ok
    if _docker_image_ok is None:
        _docker_image_ok = bool(shutil.which("docker")) and subprocess.run(
            ["docker", "image", "inspect", PDF_IMAGE],
            capture_output=True, timeout=15).returncode == 0
    if not _docker_image_ok:
        return None
    with tempfile.NamedTemporaryFile(suffix=".pdf") as fh:
        fh.write(data)
        fh.flush()
        r = subprocess.run(
            ["docker", "run", "--rm", "--network", "none", "--memory", "512m",
             "-v", f"{fh.name}:/in.pdf:ro", PDF_IMAGE, "-q", "/in.pdf", "-"],
            capture_output=True, timeout=120)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.decode("utf-8", "replace")
    return None


def _pdf_text(data):
    if shutil.which("pdftotext"):
        with tempfile.NamedTemporaryFile(suffix=".pdf") as fh:
            fh.write(data)
            fh.flush()
            r = subprocess.run(["pdftotext", "-q", fh.name, "-"],
                               capture_output=True, timeout=60)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.decode("utf-8", "replace")
    via_docker = _pdf_text_docker(data)
    if via_docker is not None:
        return via_docker
    text = _pdf_text_builtin(data)
    # Honesty check: with CID/subset fonts the operators carry glyph indices,
    # not characters — the result LOOKS like text but is noise. Better to say
    # so than to feed the model garbage.
    printable = sum(1 for c in text if c.isprintable() or c in "\n\t ")
    letters = sum(1 for c in text if c.isalpha())
    if len(text) < 20 or printable / max(len(text), 1) < 0.85 \
            or letters / max(len(text), 1) < 0.3:
        raise ValueError(
            "PDF text extraction failed — this PDF likely uses subset/CID "
            "fonts the built-in extractor cannot decode. Fix: install "
            "poppler-utils on the host, or build the helper image once: "
            "docker build -t kaim56-pdftotext - <<< "
            "'FROM alpine\nRUN apk add --no-cache poppler-utils\n"
            "ENTRYPOINT [\"pdftotext\"]'")
    return text


def _pdf_text_builtin(data):
    """Minimal extractor: decompress streams, read Tj/TJ/' operators.

    Handles FlateDecode and uncompressed streams with simple encodings
    (the output of many generators). No xref parsing — streams are found by
    scanning, which also survives mildly damaged files.
    """
    chunks = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        raw = m.group(1)
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass                          # uncompressed stream — use as is
        if b"Tj" in raw or b"TJ" in raw or b"'" in raw:
            chunks.append(raw)
    out = []
    for chunk in chunks:
        for sm in re.finditer(
                rb"\((?P<s>(?:\\.|[^\\()])*)\)\s*(?P<op>Tj|')"
                rb"|\[(?P<arr>(?:\((?:\\.|[^\\()])*\)|[^\]])*)\]\s*TJ"
                rb"|(?P<nl>T\*|TD|Td)", chunk):
            if sm.group("nl"):
                if out and out[-1] != "\n":
                    out.append("\n")
                continue
            if sm.group("s") is not None:
                out.append(_pdf_decode(sm.group("s")))
            else:
                for lit in re.finditer(rb"\((?P<s>(?:\\.|[^\\()])*)\)",
                                       sm.group("arr")):
                    out.append(_pdf_decode(lit.group("s")))
        if out and out[-1] != "\n":
            out.append("\n")
    return "".join(out)


_PDF_ESC = {b"n": "\n", b"r": "\r", b"t": "\t", b"b": "\b", b"f": "\f",
            b"(": "(", b")": ")", b"\\": "\\"}


def _pdf_decode(raw):
    """PDF string literal -> text (escapes, octal codes, latin-1)."""
    out = []
    i = 0
    while i < len(raw):
        c = raw[i:i + 1]
        if c == b"\\" and i + 1 < len(raw):
            nxt = raw[i + 1:i + 2]
            if nxt in _PDF_ESC:
                out.append(_PDF_ESC[nxt])
                i += 2
                continue
            m = re.match(rb"\\([0-7]{1,3})", raw[i:])
            if m:
                out.append(chr(int(m.group(1), 8)))
                i += 1 + len(m.group(1))
                continue
            i += 1
            continue
        out.append(c.decode("latin-1"))
        i += 1
    return "".join(out)
