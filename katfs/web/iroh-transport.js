// Adapts a WASM KatfsStream (from katfs-web wasm bindings) to the framed
// { readExact(n), write(bytes) } interface expected by KatfsProvider.
//
// iroh RecvStream.read() returns UP TO `max` bytes per call, so we buffer.

export function wrapWasmStream(wasmStream, chunkSize = 65536) {
  let buf = new Uint8Array(0);

  function append(chunk) {
    const nb = new Uint8Array(buf.length + chunk.length);
    nb.set(buf, 0);
    nb.set(chunk, buf.length);
    buf = nb;
  }

  async function fill() {
    const chunk = await wasmStream.read(chunkSize); // Uint8Array; empty => EOF
    if (!chunk || chunk.length === 0) return false;
    append(chunk);
    return true;
  }

  async function readExact(n) {
    while (buf.length < n) {
      const more = await fill();
      if (!more) throw new Error('EOF before ' + n + ' bytes');
    }
    const out = buf.slice(0, n);
    buf = buf.slice(n);
    return out;
  }

  async function write(bytes) {
    await wasmStream.write(bytes);
  }

  return { readExact, write };
}
