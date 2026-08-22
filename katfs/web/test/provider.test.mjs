// Node test for the katfs/0 PROVIDER core (framing + File System Access logic),
// using an in-memory mock directory and an in-memory byte pipe. No browser,
// no iroh needed. Run: node test/provider.test.mjs
//
// Acts as the HOST (consumer): writes framed requests, reads framed responses.

import { KatfsProvider } from '../katfs-provider.js';

const te = new TextEncoder();
const td = new TextDecoder();
let failures = 0;
function assert(cond, msg) {
  if (cond) { console.log('  ok  - ' + msg); }
  else { console.error('  FAIL- ' + msg); failures++; }
}
function u32be(n) { return new Uint8Array([(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255]); }
function readU32be(b) { return ((b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]) >>> 0; }

// ---- mock File System Access API ----------------------------------------
class MockFile {
  constructor(bytes) { this._b = bytes; this.size = bytes.length; }
  async arrayBuffer() { return this._b.buffer.slice(this._b.byteOffset, this._b.byteOffset + this._b.byteLength); }
}
class MockWritable {
  constructor(fh) { this._fh = fh; this._chunks = []; }
  async write(data) { this._chunks.push(data instanceof Uint8Array ? data : new Uint8Array(data)); }
  async close() {
    let len = 0; for (const c of this._chunks) len += c.length;
    const out = new Uint8Array(len); let o = 0;
    for (const c of this._chunks) { out.set(c, o); o += c.length; }
    this._fh._bytes = out;
  }
}
class MockFileHandle {
  constructor(name, bytes) { this.kind = 'file'; this.name = name; this._bytes = bytes || new Uint8Array(0); }
  async getFile() { return new MockFile(this._bytes); }
  async createWritable() { return new MockWritable(this); }
}
class MockDirHandle {
  constructor(name) { this.kind = 'directory'; this.name = name; this._children = new Map(); }
  async getDirectoryHandle(name, opts = {}) {
    let h = this._children.get(name);
    if (!h) { if (!opts.create) throw new Error('NotFoundError: ' + name); h = new MockDirHandle(name); this._children.set(name, h); }
    if (h.kind !== 'directory') throw new Error('TypeMismatchError: ' + name);
    return h;
  }
  async getFileHandle(name, opts = {}) {
    let h = this._children.get(name);
    if (!h) { if (!opts.create) throw new Error('NotFoundError: ' + name); h = new MockFileHandle(name); this._children.set(name, h); }
    if (h.kind !== 'file') throw new Error('TypeMismatchError: ' + name);
    return h;
  }
  // Like FileSystemDirectoryHandle.removeEntry: without recursive a non-empty
  // directory fails (InvalidModificationError).
  async removeEntry(name, opts = {}) {
    const h = this._children.get(name);
    if (!h) throw new Error('NotFoundError: ' + name);
    if (h.kind === 'directory' && h._children.size > 0 && !opts.recursive) {
      throw new Error('InvalidModificationError: directory not empty: ' + name);
    }
    this._children.delete(name);
  }
  async *values() { for (const h of this._children.values()) yield h; }
}

// ---- in-memory framed pipe ----------------------------------------------
// host <-> provider. Two byte queues with async readExact.
class Pipe {
  constructor() { this.buf = new Uint8Array(0); this.waiters = []; this.closed = false; }
  write(bytes) {
    const nb = new Uint8Array(this.buf.length + bytes.length);
    nb.set(this.buf, 0); nb.set(bytes, this.buf.length); this.buf = nb;
    while (this.waiters.length && this.waiters[0].n <= this.buf.length) {
      const w = this.waiters.shift();
      const out = this.buf.slice(0, w.n); this.buf = this.buf.slice(w.n); w.resolve(out);
    }
  }
  close() { this.closed = true; while (this.waiters.length) this.waiters.shift().reject(new Error('EOF')); }
  readExact(n) {
    if (this.buf.length >= n) { const out = this.buf.slice(0, n); this.buf = this.buf.slice(n); return Promise.resolve(out); }
    if (this.closed) return Promise.reject(new Error('EOF'));
    return new Promise((resolve, reject) => this.waiters.push({ n, resolve, reject }));
  }
}

// Provider reads from h2b, writes to b2h. Host does the opposite.
const h2b = new Pipe();
const b2h = new Pipe();
const providerStream = { readExact: (n) => h2b.readExact(n), write: (b) => { b2h.write(b); return Promise.resolve(); } };
const hostSend = (b) => { h2b.write(b); };
const hostRead = (n) => b2h.readExact(n);

function frameJson(obj) {
  const body = te.encode(JSON.stringify(obj));
  const f = new Uint8Array(4 + body.length); f.set(u32be(body.length), 0); f.set(body, 4); return f;
}
function frameBytes(bytes) {
  const f = new Uint8Array(4 + bytes.length); f.set(u32be(bytes.length), 0); f.set(bytes, 4); return f;
}
async function hostRecvJson() {
  const lh = await hostRead(4); const len = readU32be(lh);
  const jb = await hostRead(len); return JSON.parse(td.decode(jb));
}
async function hostRecvBytes() {
  const lh = await hostRead(4); const len = readU32be(lh);
  return len > 0 ? await hostRead(len) : new Uint8Array(0);
}

// ---- build a mock share --------------------------------------------------
const root = new MockDirHandle('share');
const sub = await root.getDirectoryHandle('sub', { create: true });
await (async () => {
  const fh = await root.getFileHandle('hello.txt', { create: true });
  const w = await fh.createWritable(); await w.write(te.encode('hallo welt')); await w.close();
})();
await (async () => {
  const fh = await sub.getFileHandle('inner.bin', { create: true });
  const w = await fh.createWritable(); await w.write(new Uint8Array([1, 2, 3, 4, 5])); await w.close();
})();

// ---- run provider in background -----------------------------------------
const provider = new KatfsProvider(root, () => {}, { share: 'abc123def456', device: 'Linux' });
const serveDone = provider.serve(providerStream).catch((e) => { console.error('serve error', e); });

// ---- scripted host requests ---------------------------------------------
async function main() {
  // 1) list root
  hostSend(frameJson({ id: 1, op: 'list', path: '' }));
  let r = await hostRecvJson();
  assert(r.id === 1 && r.ok === true, 'list root ok');
  const names = (r.entries || []).map((e) => e.name).sort();
  assert(JSON.stringify(names) === JSON.stringify(['hello.txt', 'sub']), 'list root names = hello.txt,sub');
  const helloEntry = r.entries.find((e) => e.name === 'hello.txt');
  assert(helloEntry && helloEntry.dir === false && helloEntry.size === 10, 'hello.txt size 10, not dir');
  const subEntry = r.entries.find((e) => e.name === 'sub');
  assert(subEntry && subEntry.dir === true, 'sub is dir');

  // 2) stat file
  hostSend(frameJson({ id: 2, op: 'stat', path: 'hello.txt' }));
  r = await hostRecvJson();
  assert(r.ok && r.exists && r.dir === false && r.size === 10, 'stat hello.txt exists size 10');

  // 3) stat missing
  hostSend(frameJson({ id: 3, op: 'stat', path: 'nope.txt' }));
  r = await hostRecvJson();
  assert(r.ok && r.exists === false, 'stat missing -> exists false');

  // 4) stat nested dir
  hostSend(frameJson({ id: 4, op: 'stat', path: 'sub' }));
  r = await hostRecvJson();
  assert(r.ok && r.exists && r.dir === true, 'stat sub -> dir');

  // 5) read file
  hostSend(frameJson({ id: 5, op: 'read', path: 'hello.txt' }));
  r = await hostRecvJson();
  assert(r.ok && r.size === 10, 'read hello.txt size 10');
  const payload = await hostRecvBytes();
  assert(td.decode(payload) === 'hallo welt', 'read payload = "hallo welt"');

  // 6) read nested binary
  hostSend(frameJson({ id: 6, op: 'read', path: 'sub/inner.bin' }));
  r = await hostRecvJson();
  const p2 = await hostRecvBytes();
  assert(r.ok && r.size === 5 && JSON.stringify([...p2]) === JSON.stringify([1, 2, 3, 4, 5]), 'read sub/inner.bin bytes');

  // 7) write new nested file (creates dirs)
  const wdata = te.encode('neuer inhalt');
  hostSend(frameJson({ id: 7, op: 'write', path: 'deep/new.txt', size: wdata.length }));
  hostSend(frameBytes(wdata));
  r = await hostRecvJson();
  assert(r.ok === true, 'write deep/new.txt ok');

  // 8) read it back
  hostSend(frameJson({ id: 8, op: 'read', path: 'deep/new.txt' }));
  r = await hostRecvJson();
  const p3 = await hostRecvBytes();
  assert(r.ok && td.decode(p3) === 'neuer inhalt', 'read-back written file');

  // 9) overwrite existing
  const over = te.encode('X');
  hostSend(frameJson({ id: 9, op: 'write', path: 'hello.txt', size: 1 }));
  hostSend(frameBytes(over));
  r = await hostRecvJson();
  assert(r.ok, 'overwrite hello.txt ok');
  hostSend(frameJson({ id: 10, op: 'stat', path: 'hello.txt' }));
  r = await hostRecvJson();
  assert(r.ok && r.size === 1, 'hello.txt now size 1');

  // 11) path escape rejected
  hostSend(frameJson({ id: 11, op: 'read', path: '../secret' }));
  r = await hostRecvJson();
  assert(r.ok === false && /\.\./.test(r.error || ''), 'path escape ".." rejected');

  // 12) escape via nested ..
  hostSend(frameJson({ id: 12, op: 'list', path: 'sub/../..' }));
  r = await hostRecvJson();
  assert(r.ok === false, 'nested ".." rejected');

  // 13) unknown op
  hostSend(frameJson({ id: 13, op: 'frobnicate', path: '' }));
  r = await hostRecvJson();
  assert(r.ok === false && /unknown op/.test(r.error || ''), 'unknown op error');

  // 14) read missing -> error
  hostSend(frameJson({ id: 14, op: 'read', path: 'ghost.txt' }));
  r = await hostRecvJson();
  assert(r.ok === false, 'read missing -> error');

  // 15) hello: identifies this share to the host (multi-share selection)
  hostSend(frameJson({ id: 15, op: 'hello', path: '.' }));
  r = await hostRecvJson();
  assert(r.ok === true && r.share === 'abc123def456', 'hello reports the share id');
  assert(r.name === 'share' && r.device === 'Linux', 'hello reports folder name + device');

  // 16) a provider without ident still answers hello (host then assigns an id)
  {
    const bare = new KatfsProvider(root, () => {});
    const res = await bare.dispatch({ id: 16, op: 'hello', path: '.' });
    assert(res.json.ok === true && res.json.share === '', 'hello without ident -> empty share id');
  }

  // 17) delete: file gone, root protected, non-empty folder only with recursive
  hostSend(frameJson({ id: 17, op: 'write', path: 'weg.txt', size: 3 }));
  hostSend(frameBytes(te.encode('abc')));
  await hostRecvJson();
  hostSend(frameJson({ id: 18, op: 'delete', path: 'weg.txt' }));
  r = await hostRecvJson();
  assert(r.ok === true, 'delete file ok');
  hostSend(frameJson({ id: 19, op: 'stat', path: 'weg.txt' }));
  r = await hostRecvJson();
  assert(r.ok === true && r.exists === false, 'deleted file is gone');

  hostSend(frameJson({ id: 20, op: 'delete', path: '' }));
  r = await hostRecvJson();
  assert(r.ok === false && /share root/.test(r.error || ''), 'refuses to delete the share root');

  hostSend(frameJson({ id: 21, op: 'delete', path: '../etc' }));
  r = await hostRecvJson();
  assert(r.ok === false, 'delete rejects ".." escape');

  hostSend(frameJson({ id: 22, op: 'delete', path: 'sub' }));
  r = await hostRecvJson();
  assert(r.ok === false, 'non-empty dir needs recursive');
  hostSend(frameJson({ id: 23, op: 'delete', path: 'sub', recursive: true }));
  r = await hostRecvJson();
  assert(r.ok === true, 'recursive dir delete ok');
  hostSend(frameJson({ id: 24, op: 'stat', path: 'sub' }));
  r = await hostRecvJson();
  assert(r.ok === true && r.exists === false, 'directory is gone');

  h2b.close(); // signal EOF to provider
  await serveDone;

  console.log(failures === 0 ? '\nALL TESTS PASSED' : `\n${failures} TEST(S) FAILED`);
  process.exit(failures === 0 ? 0 : 1);
}

main().catch((e) => { console.error(e); process.exit(1); });
