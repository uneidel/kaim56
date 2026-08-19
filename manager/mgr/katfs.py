"""katfs: P2P-Ordnerfreigabe (iroh) — Proxy-Helfer zum Host-Knoten.

Teil des mgr-Pakets; spricht nur den loopback-gebundenen katfs-Knoten an.
"""
import io
import json
import os
import urllib.parse
import urllib.request
import zipfile

# ---- katfs (P2P-Ordnerfreigabe aus dem Browser) ----------------------------
# Der katfs-Host-Knoten laeuft auf dem Host (Port 8790) und haelt die iroh-
# Verbindung zum freigebenden Browser-Tab. Die Agenten greifen ueber
# remote_ls/remote_read/remote_write auf <gateway>:8790 zu — nichts davon wird
# in die VM gemountet, katfs ist kein Dateisystem.
#
# Der Manager reicht die Freigabe-Seite unter /katfs/ durch: gleiche Herkunft,
# gleiche Auth — und vor allem HTTPS, das die File System Access API im
# Browser zwingend braucht (secure context). Damit entfaellt der SSH-Tunnel
# bzw. die eigene Traefik-Route aus iroh-fs/README.md.
KATFS_HOST = os.environ.get("KATFS_HOST", "127.0.0.1")
KATFS_PORT = int(os.environ.get("KATFS_PORT", "8790"))
KATFS_BASE = f"http://{KATFS_HOST}:{KATFS_PORT}"


KATFS_MAX_WRITE = 64 * 1024 * 1024   # Deckel gegen Platten-DoS im Operator-Ordner


def katfs_share_for(inst):
    """Die Freigabe, die diese Instanz benutzen DARF. Genau die aus ihrer Config
    — nie eine vom Gast mitgegebene. Leer heisst: der Knoten entscheidet, was er
    nur kann, solange hoechstens eine Freigabe aktiv ist."""
    return (inst.get("config", {}).get("KATFS_SHARE", "") or "").strip()


def katfs_proxy_fs(op, share, path, recursive=False, body=None):
    """Eine Dateioperation an den (jetzt loopback-gebundenen) Knoten weiterreichen.
    Der Aufrufer hat die Instanz bereits per Source-IP verifiziert und die Freigabe
    aus deren Config gesetzt — der Gast kann keine fremde Freigabe adressieren."""
    q = f"?path={urllib.parse.quote(path)}"
    if share:
        q += f"&share={urllib.parse.quote(share)}"
    if op == "delete" and recursive:
        q += "&recursive=1"
    method = "POST" if op in ("write", "delete") else "GET"
    req = urllib.request.Request(KATFS_BASE + "/" + op + q,
                                 data=(body if op == "write" else b"") if method == "POST" else None,
                                 method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, r.headers.get("Content-Type", "application/octet-stream"), r.read()


# Grenzen fuer den "alles herunterladen"-ZIP: der Knoten liest jede Datei ganz
# in den Speicher, darum ein Deckel gegen versehentliche Riesen-Freigaben.
KATFS_ZIP_MAX_FILES = 2000
KATFS_ZIP_MAX_BYTES = 512 * 1024 * 1024   # 512 MB gesamt


def katfs_zip(share, root):
    """Den Teilbaum ab `root` einer Freigabe rekursiv einsammeln und als ZIP
    zurueckgeben. Laeuft ueber dieselben ls/read-Proxyaufrufe wie der Browser,
    d.h. nur, solange die Freigabe im Browser-Tab offen ist. Wirft bei zu
    grossen Baeumen, bevor er den Speicher sprengt."""
    root = (root or ".").strip() or "."
    buf = io.BytesIO()
    stats = {"files": 0, "bytes": 0}
    base = "" if root in (".", "") else root.rstrip("/")

    def walk(rel):
        st, _ct, data = katfs_proxy_fs("ls", share, rel or ".")
        if st != 200:
            raise RuntimeError(f"list {rel or '.'} -> {st}")
        for e in (json.loads(data or b"{}").get("entries") or []):
            name = e.get("name", "")
            if not name or name in (".", ".."):
                continue
            child = f"{rel}/{name}" if rel and rel != "." else name
            if e.get("dir"):
                walk(child)
                continue
            stats["files"] += 1
            if stats["files"] > KATFS_ZIP_MAX_FILES:
                raise RuntimeError(f"too many files (>{KATFS_ZIP_MAX_FILES})")
            fst, _fct, fdata = katfs_proxy_fs("read", share, child)
            if fst != 200:
                continue   # unlesbare Einzeldatei ueberspringen, Rest liefern
            stats["bytes"] += len(fdata)
            if stats["bytes"] > KATFS_ZIP_MAX_BYTES:
                raise RuntimeError("archive too large (>512 MB)")
            # Pfad im Archiv relativ zum gewaehlten Ordner.
            arc = child[len(base) + 1:] if base and child.startswith(base + "/") else child
            zf.writestr(arc, fdata)

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        walk(base if base else ".")
    return buf.getvalue(), stats


def katfs_status():
    out = {"up": False, "connected": False, "share": "", "node_id": "",
           "port": KATFS_PORT, "error": "", "shares": []}
    try:
        with urllib.request.urlopen(KATFS_BASE + "/status", timeout=3) as r:
            out.update(json.loads(r.read().decode()))
        out["up"] = True
    except Exception as e:
        out["error"] = f"{e}"
        return out
    try:
        with urllib.request.urlopen(KATFS_BASE + "/nodeid", timeout=3) as r:
            out["node_id"] = json.loads(r.read().decode()).get("node_id", "")
    except Exception:
        pass
    # /shares gibt es erst ab dem Multi-Share-Knoten; ein aelterer antwortet 404.
    try:
        with urllib.request.urlopen(KATFS_BASE + "/shares", timeout=3) as r:
            out["shares"] = json.loads(r.read().decode()).get("shares", [])
    except Exception:
        out["shares"] = []
    return out


