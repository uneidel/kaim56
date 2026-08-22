// UI wiring: pick a directory, connect to the host node via iroh (wasm),
// and serve the katfs/0 provider protocol.

import { KatfsProvider } from './katfs-provider.js';
import { wrapWasmStream } from './iroh-transport.js';

const ALPN = new TextEncoder().encode('katfs/0');

const $ = (id) => document.getElementById(id);
const logEl = $('log');

function log(msg) {
  const ts = new Date().toISOString().slice(11, 19);
  logEl.textContent += `[${ts}] ${msg}\n`;
  logEl.scrollTop = logEl.scrollHeight;
}

function setConn(state, text) {
  const dot = $('conn-dot');
  dot.className = 'dot' + (state ? ' ' + state : '');
  $('conn-text').textContent = text;
}

// Id of this share. Stays in localStorage so a reload or reconnect is the
// same share again — an instance pointing at this id would otherwise run into
// nothing after every reload.
function shareId() {
  let v = null;
  try { v = localStorage.getItem('katfs-share-id'); } catch (_) { /* private mode */ }
  if (!v) {
    const b = new Uint8Array(6);
    crypto.getRandomValues(b);
    v = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
    try { localStorage.setItem('katfs-share-id', v); } catch (_) { /* ignore */ }
  }
  return v;
}

function deviceName() {
  const p = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '';
  return String(p).slice(0, 40);
}

let dirHandle = null;
let endpoint = null;   // wasm KatfsEndpoint
let connection = null; // wasm KatfsConn
let serving = false;

// Lazily import the wasm bindings. The build step (build-wasm.sh) emits
// ./wasm/katfs_web.js + ./wasm/katfs_web_bg.wasm. If missing, we report it.
let wasmMod = null;
async function loadWasm() {
  if (wasmMod) return wasmMod;
  try {
    const mod = await import('./wasm/katfs_web.js');
    await mod.default(); // init()
    wasmMod = mod;
    return mod;
  } catch (e) {
    log('Could not load WASM bindings (./wasm/katfs_web.js). Please run build-wasm.sh.');
    log('Detail: ' + (e && e.message ? e.message : e));
    throw e;
  }
}

// Why is showDirectoryPicker missing? Two quite different reasons that must
// not be confused: an insecure context (fixable with HTTPS) or a browser
// without the picker half of the File System Access API (not fixable).
function pickerDiagnosis() {
  const bits = [];
  if (!window.isSecureContext) {
    bits.push(
      'no secure context: you opened ' + location.protocol + '//' + location.host +
      ' — the API only exists over HTTPS or on localhost'
    );
  }
  const brands = ((navigator.userAgentData && navigator.userAgentData.brands) || [])
    .map((b) => b.brand)
    .join(', ');
  if (!/Chromium|Google Chrome|Microsoft Edge/i.test(brands)) {
    bits.push(
      'browser does not look Chromium-based' +
      (brands ? ' (' + brands + ')' : ' (' + navigator.userAgent.slice(0, 90) + ')') +
      ' — Safari and Firefox ship only the origin-private part of the File System ' +
      'Access API, without showDirectoryPicker. On macOS: Chrome or Edge.'
    );
  }
  return bits.length ? bits.join(' · ') : 'the API is simply absent (browser version too old?)';
}

$('pick').addEventListener('click', async () => {
  if (!window.showDirectoryPicker) {
    log('Cannot choose a folder — ' + pickerDiagnosis());
    return;
  }
  try {
    dirHandle = await window.showDirectoryPicker({ mode: 'readwrite' });
    $('dir-text').textContent = 'Folder: ' + dirHandle.name;
    log('Folder selected: ' + dirHandle.name);
  } catch (e) {
    log('Folder selection cancelled: ' + (e && e.message ? e.message : e));
  }
});

let running = false; // Auto-Reconnect-Schleife aktiv?

function withTimeout(p, ms, msg) {
  return Promise.race([
    p,
    new Promise((_, rej) => setTimeout(() => rej(new Error(msg)), ms)),
  ]);
}

async function connectAndServeLoop(nodeId) {
  running = true;
  $('connect').disabled = true;
  $('disconnect').disabled = false;
  const mod = await loadWasm();
  let fails = 0;
  let round = 0;
  while (running) {
    round++;
    try {
      if (!endpoint) {
        log('creating iroh endpoint…');
        endpoint = await mod.KatfsEndpoint.spawn();
        log('own endpoint-id: ' + endpoint.endpointId());
      }
      setConn('wait', 'connecting… (#' + round + ')');
      log('connecting to host ' + nodeId + ' (ALPN katfs/0)… (#' + round + ')');
      connection = await withTimeout(endpoint.connect(nodeId, ALPN), 15000, 'connect timeout');
      fails = 0;
      setConn('ok', 'connected');
      const stream = wrapWasmStream(await connection.acceptBi());
      const provider = new KatfsProvider(dirHandle, log, { share: shareId(), device: deviceName() });
      serving = true;
      log('Stream open — serving requests.');
      await provider.serve(stream); // runs until the stream closes
      serving = false;
      log('Stream closed.');
    } catch (e) {
      serving = false;
      fails++;
      log('Connection lost (' + fails + '): ' + (e && e.message ? e.message : e));
      // After 2 failures, set up the endpoint from scratch.
      if (fails >= 2) {
        endpoint = null;
        log('resetting endpoint…');
      }
    }
    if (!running) break;
    setConn('wait', 'reconnecting in 1s…');
    await new Promise((r) => setTimeout(r, 1000));
  }
  setConn('', 'disconnected');
  $('connect').disabled = false;
  $('disconnect').disabled = true;
}

$('connect').addEventListener('click', () => {
  if (!dirHandle) { log('Please choose a folder first.'); return; }
  const nodeId = $('nodeid').value.trim();
  if (!nodeId || nodeId.startsWith('%%')) { log('Please enter a valid host node-id.'); return; }
  if (running) { log('Already running.'); return; }
  connectAndServeLoop(nodeId);
});

$('disconnect').addEventListener('click', () => {
  running = false;
  connection = null;
  serving = false;
  setConn('', 'disconnected (stops after next drop; reload to stop now)');
  $('disconnect').disabled = true;
  log('Auto-reconnect stopped.');
});

log('Page loaded. 1) Choose folder  2) Connect & serve.');
{
  // If the host templated index.html, the input holds a real node-id and no
  // longer starts with the "%%" placeholder marker.
  const v = $('nodeid').value.trim();
  if (v && !v.startsWith('%%')) log('Host node-id injected by server: ' + v);
  else log('No node-id injected — please enter it manually.');
  log('This share: ' + shareId() + ' (' + deviceName() + ') — pick it by that id when creating an instance.');
  // Say right on load whether "pick folder" can work at all — instead of
  // making the user click first.
  if (!window.showDirectoryPicker) {
    log('⚠ Folder sharing will not work here — ' + pickerDiagnosis());
  } else {
    log('Folder picker available (secure context: ' + window.isSecureContext + ').');
  }
}
