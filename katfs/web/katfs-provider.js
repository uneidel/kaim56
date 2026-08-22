// katfs/0 PROVIDER (browser side) — pure protocol + File System Access logic.
//
// This module is transport- and DOM-agnostic so it can be unit tested in Node
// with mock objects. It depends only on two injected things:
//
//   1) A `dir` handle that quacks like a FileSystemDirectoryHandle:
//        - dir.getDirectoryHandle(name, {create})  -> Promise<dirHandle>
//        - dir.getFileHandle(name, {create})       -> Promise<fileHandle>
//        - dir.values()                            -> async iterator of handles
//        - handle.kind                             -> "file" | "directory"
//        - handle.name                             -> string
//      file handles additionally:
//        - fileHandle.getFile()                    -> Promise<File> (has .size, .arrayBuffer())
//        - fileHandle.createWritable()             -> Promise<writable> (has .write(), .close())
//
//   2) A `stream` object with framed byte access:
//        - stream.readExact(n) -> Promise<Uint8Array>  (throws on EOF before n bytes)
//        - stream.write(bytes) -> Promise<void>
//
// Wire framing (see PROTOCOL.md):
//   Every message = 4-byte big-endian length + JSON.
//   For `read` response and `write` request, a SECOND frame follows:
//     4-byte big-endian length + raw bytes (file content).
//
// Roles (PROTOCOL.md): the HOST is the CONSUMER — it sends requests and reads
// responses. The BROWSER (this code) is the PROVIDER — it reads requests and
// writes responses out of the shared directory.

const te = new TextEncoder();
const td = new TextDecoder();

function u32be(n) {
  const b = new Uint8Array(4);
  b[0] = (n >>> 24) & 0xff;
  b[1] = (n >>> 16) & 0xff;
  b[2] = (n >>> 8) & 0xff;
  b[3] = n & 0xff;
  return b;
}

function readU32be(b) {
  return ((b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]) >>> 0;
}

// Split a relative path into segments, rejecting escape attempts.
export function splitPath(path) {
  const parts = String(path == null ? '' : path)
    .split('/')
    .filter((p) => p.length > 0 && p !== '.');
  for (const p of parts) {
    if (p === '..') throw new Error('path escapes share root: ".." rejected');
    if (p.includes('\\')) throw new Error('backslash not allowed in path');
    if (p.includes('\0')) throw new Error('NUL not allowed in path');
  }
  return parts;
}

// Walk to the directory that should CONTAIN the last segment.
// Returns { dir, name }. If the path is empty, name is null (path IS the root).
async function resolveParent(root, path, { create = false } = {}) {
  const parts = splitPath(path);
  if (parts.length === 0) return { dir: root, name: null };
  let dir = root;
  for (let i = 0; i < parts.length - 1; i++) {
    dir = await dir.getDirectoryHandle(parts[i], { create });
  }
  return { dir, name: parts[parts.length - 1] };
}

// Walk to the directory named by the whole path.
async function resolveDir(root, path, { create = false } = {}) {
  const parts = splitPath(path);
  let dir = root;
  for (const p of parts) dir = await dir.getDirectoryHandle(p, { create });
  return dir;
}

export class KatfsProvider {
  // root: FileSystemDirectoryHandle (or mock). log: optional (msg) => void.
  // ident: { share, device } — answered on `hello` so the host can tell several
  // simultaneous shares apart and name them. `share` must be stable across
  // reloads (app.js keeps it in localStorage), otherwise a reconnect shows up
  // as a second share and any instance pinned to the old id goes stale.
  constructor(root, log, ident) {
    this.root = root;
    this.log = log || (() => {});
    this.ident = ident || {};
  }

  // ---- protocol handlers -------------------------------------------------

  async handleList(path) {
    const dir = await resolveDir(this.root, path);
    const entries = [];
    for await (const handle of dir.values()) {
      if (handle.kind === 'directory') {
        entries.push({ name: handle.name, dir: true, size: 0 });
      } else {
        let size = 0;
        try {
          const f = await handle.getFile();
          size = f.size;
        } catch (_) {
          /* size unknown */
        }
        entries.push({ name: handle.name, dir: false, size });
      }
    }
    return { entries };
  }

  async handleStat(path) {
    const parts = splitPath(path);
    if (parts.length === 0) return { exists: true, dir: true, size: 0 };
    const { dir, name } = await resolveParent(this.root, path);
    // Try file first, then directory.
    try {
      const fh = await dir.getFileHandle(name);
      const f = await fh.getFile();
      return { exists: true, dir: false, size: f.size };
    } catch (_) {
      /* not a file */
    }
    try {
      await dir.getDirectoryHandle(name);
      return { exists: true, dir: true, size: 0 };
    } catch (_) {
      return { exists: false, dir: false, size: 0 };
    }
  }

  async handleRead(path) {
    const { dir, name } = await resolveParent(this.root, path);
    if (name == null) throw new Error('cannot read a directory');
    const fh = await dir.getFileHandle(name);
    const file = await fh.getFile();
    const buf = new Uint8Array(await file.arrayBuffer());
    return { size: buf.length, payload: buf };
  }

  async handleWrite(path, data) {
    const { dir, name } = await resolveParent(this.root, path, { create: true });
    if (name == null) throw new Error('cannot write to the share root itself');
    const fh = await dir.getFileHandle(name, { create: true });
    const w = await fh.createWritable();
    await w.write(data);
    await w.close();
    return {};
  }

  // Deletes a file or a directory. Without `recursive` the browser API rejects
  // a non-empty directory on its own — exactly right, so an agent doesn't
  // accidentally wipe a tree.
  async handleDelete(path, recursive) {
    const { dir, name } = await resolveParent(this.root, path);
    if (name == null) throw new Error('refusing to delete the share root');
    await dir.removeEntry(name, { recursive: !!recursive });
    return {};
  }

  // Dispatch one already-parsed request. `payload` is the write-frame bytes
  // (only for op === "write"). Returns { json, payload? } where payload is the
  // read-frame bytes to send after the JSON response.
  async dispatch(req, payload) {
    const id = req.id;
    try {
      switch (req.op) {
        case 'hello': {
          return {
            json: {
              id,
              ok: true,
              share: this.ident.share || '',
              name: (this.root && this.root.name) || '',
              device: this.ident.device || '',
            },
          };
        }
        case 'list': {
          const r = await this.handleList(req.path);
          return { json: { id, ok: true, entries: r.entries } };
        }
        case 'stat': {
          const r = await this.handleStat(req.path);
          return { json: { id, ok: true, exists: r.exists, dir: r.dir, size: r.size } };
        }
        case 'read': {
          const r = await this.handleRead(req.path);
          return { json: { id, ok: true, size: r.size }, payload: r.payload };
        }
        case 'write': {
          await this.handleWrite(req.path, payload || new Uint8Array(0));
          return { json: { id, ok: true } };
        }
        case 'delete': {
          await this.handleDelete(req.path, req.recursive);
          return { json: { id, ok: true } };
        }
        default:
          return { json: { id, ok: false, error: 'unknown op: ' + req.op } };
      }
    } catch (e) {
      return { json: { id, ok: false, error: String(e && e.message ? e.message : e) } };
    }
  }

  // Read exactly one request frame (+ write payload frame if op === "write").
  async readRequest(stream) {
    const lenBuf = await stream.readExact(4);
    const len = readU32be(lenBuf);
    const jsonBuf = await stream.readExact(len);
    const req = JSON.parse(td.decode(jsonBuf));
    let payload = null;
    if (req.op === 'write') {
      const pl = await stream.readExact(4);
      const plen = readU32be(pl);
      payload = plen > 0 ? await stream.readExact(plen) : new Uint8Array(0);
    }
    return req;
  }

  async writeResponse(stream, json, payload) {
    const body = te.encode(JSON.stringify(json));
    // Single concatenated write for the JSON frame.
    const frame = new Uint8Array(4 + body.length);
    frame.set(u32be(body.length), 0);
    frame.set(body, 4);
    await stream.write(frame);
    if (payload) {
      const pframe = new Uint8Array(4 + payload.length);
      pframe.set(u32be(payload.length), 0);
      pframe.set(payload, 4);
      await stream.write(pframe);
    }
  }

  // Serve requests forever on the given framed stream until EOF.
  // (The host opens the bidirectional stream and sends the first request.)
  async serve(stream) {
    for (;;) {
      let lenBuf;
      try {
        lenBuf = await stream.readExact(4);
      } catch (e) {
        this.log('stream closed: ' + (e && e.message ? e.message : e));
        return; // clean EOF
      }
      const len = readU32be(lenBuf);
      const jsonBuf = await stream.readExact(len);
      const req = JSON.parse(td.decode(jsonBuf));
      let payload = null;
      if (req.op === 'write') {
        const pl = await stream.readExact(4);
        const plen = readU32be(pl);
        payload = plen > 0 ? await stream.readExact(plen) : new Uint8Array(0);
      }
      this.log(`req #${req.id} ${req.op} ${req.path ?? ''}`);
      const res = await this.dispatch(req, payload);
      await this.writeResponse(stream, res.json, res.payload);
    }
  }
}

// Exposed for tests.
export const _internal = { u32be, readU32be, splitPath, resolveParent, resolveDir };
