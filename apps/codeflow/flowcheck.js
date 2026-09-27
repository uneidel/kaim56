// kAIm56 - self-hosted Firecracker AI-agent platform
// Copyright (C) 2026 the kAIm56 authors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The graph checker of validate_flow.py, in the browser: the app decides with
// it whether an analysis round is done (the agent runs the Python twin in its
// VM). Same rules, same messages - tests/ runs both on the same fixtures.
//
//   readJSON(name)  -> Promise<object | null | {__invalid: message}>   flow/<name>
//   readLines(file) -> Promise<string[] | null>                         src/<file>

export const KINDS = ['entry', 'process', 'store', 'external', 'output'];
export const ID_RE = /^[a-z0-9][a-z0-9_-]{0,48}$/;
const MAX_NODES = { overview: 45, detail: 60 };
const MAX_SPAN = 80, MAX_REFS = 4;

export function symbolToken(sym) {
  const parts = String(sym).split(/[.:/#\s()[\]<>,]+/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : '';
}
const escRe = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const isInt = v => typeof v === 'number' && Number.isInteger(v);

export async function validateGraph(g, level, readLines, hasDetail) {
  const err = [];
  if (!g || typeof g !== 'object' || Array.isArray(g)) return ['the file must hold one JSON object'];
  const nodes = g.nodes, edges = g.edges;
  if (!Array.isArray(nodes) || !nodes.length) return ["'nodes' must be a non-empty list"];
  if (!Array.isArray(edges)) return ["'edges' must be a list"];
  if (nodes.length > MAX_NODES[level])
    err.push(`${nodes.length} nodes - at most ${MAX_NODES[level]} at the ${level} level; group the minor steps`);
  if (!String(g.title || '').trim()) err.push("'title' is missing");
  const ids = new Set();
  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i];
    if (!n || typeof n !== 'object') { err.push(`node #${i}: not an object`); continue; }
    const nid = String(n.id || '');
    const where = `node '${nid || i}'`;
    if (!ID_RE.test(nid)) err.push(`${where}: id must match ${ID_RE.source}`);
    else if (ids.has(nid)) err.push(`${where}: duplicate id`);
    ids.add(nid);
    const label = String(n.label || '');
    if (!label.trim() || label.length > 60) err.push(`${where}: 'label' must be 1-60 characters`);
    if (!KINDS.includes(n.kind)) err.push(`${where}: 'kind' must be one of ${KINDS.join(', ')}`);
    if (String(n.summary || '').length > 400) err.push(`${where}: 'summary' over 400 characters`);
    let code = n.code || [];
    if (!Array.isArray(code) || code.length > MAX_REFS) { err.push(`${where}: 'code' must be a list of at most ${MAX_REFS} references`); code = []; }
    if (!code.length && !['external', 'output'].includes(n.kind))
      err.push(`${where}: needs at least one code reference (only external/output nodes may have none)`);
    for (const r of code) {
      if (!r || typeof r !== 'object') { err.push(`${where}: a code reference is not an object`); continue; }
      const f = String(r.file || ''), s = r.start, e = r.end;
      if (!f || f.startsWith('/') || f.split('/').includes('..')) { err.push(`${where}: code file '${f}' must be a path relative to src/`); continue; }
      const lines = await readLines(f);
      if (!lines) {
        const hint = f.startsWith('src/') ? " - paths are relative to src/, drop the 'src/' prefix" : '';
        err.push(`${where}: code file '${f}' does not exist in src/${hint}`); continue;
      }
      if (!isInt(s) || !isInt(e)) { err.push(`${where}: ${f}: 'start'/'end' must be integers`); continue; }
      if (!(1 <= s && s <= e && e <= lines.length)) { err.push(`${where}: ${f}:${s}-${e} is outside the file (1-${lines.length})`); continue; }
      if (e - s + 1 > MAX_SPAN) err.push(`${where}: ${f}:${s}-${e} spans ${e - s + 1} lines - at most ${MAX_SPAN}; point at the part that moves the data`);
      const sym = String(r.symbol || '').trim(), tok = symbolToken(sym);
      if (!tok) err.push(`${where}: ${f}:${s}-${e} needs a 'symbol' - the function/class/key those lines define or use, as written in the code`);
      else if (!new RegExp(`(?<![A-Za-z0-9_$])${escRe(tok)}(?![A-Za-z0-9_$])`).test(lines.slice(s - 1, e).join('\n')))
        err.push(`${where}: symbol '${sym}' does not occur in ${f}:${s}-${e} - read the file (python3 validate_flow.py find ${tok}) and point at the real lines`);
    }
    const d = n.detail;
    if (d !== undefined && d !== null) {
      if (level !== 'overview') err.push(`${where}: 'detail' only on overview nodes`);
      else if (!ID_RE.test(String(d))) err.push(`${where}: 'detail' must be an id like the node ids`);
      else if (!(await hasDetail(String(d)))) err.push(`${where}: detail graph flow/${d}.json does not exist`);
    }
  }
  const seen = new Set(), linked = new Set();
  edges.forEach((e, i) => {
    if (!e || typeof e !== 'object') { err.push(`edge #${i}: not an object`); return; }
    const s = String(e.source || ''), t = String(e.target || ''), data = String(e.data || '').trim();
    if (!ids.has(s) || !ids.has(t)) { err.push(`edge ${s}->${t}: source and target must be node ids`); return; }
    if (s === t) err.push(`edge ${s}->${t}: a node cannot feed itself`);
    if (!data || data.length > 80) err.push(`edge ${s}->${t}: 'data' must name what flows, 1-80 characters`);
    const k = `${s}\u0000${t}\u0000${data}`;
    if (seen.has(k)) err.push(`edge ${s}->${t}: duplicate`);
    seen.add(k); linked.add(s); linked.add(t);
  });
  if (ids.size > 1)
    [...ids].filter(x => !linked.has(x)).sort()
      .forEach(nid => err.push(`node '${nid}': not connected by any edge - the graph follows the data, so every node is on a path`));
  return err;
}

/** {ok, graphs, nodes, errors}: flow/overview.json plus every detail graph it points to. */
export async function validateTree(readJSON, readLines) {
  const ov = await readJSON('overview.json');
  if (ov === null) return { ok: false, graphs: 0, nodes: 0, errors: ['flow/overview.json does not exist yet'] };
  if (ov && ov.__invalid) return { ok: false, graphs: 0, nodes: 0, errors: [`flow/overview.json is not valid JSON: ${ov.__invalid}`] };
  const details = new Map();
  const hasDetail = async id => { if (!details.has(id)) details.set(id, await readJSON(`${id}.json`)); return details.get(id) !== null; };
  const errors = (await validateGraph(ov, 'overview', readLines, hasDetail)).map(x => `overview: ${x}`);
  let graphs = 1, nodes = Array.isArray(ov?.nodes) ? ov.nodes.length : 0;
  for (const n of Array.isArray(ov?.nodes) ? ov.nodes : []) {
    const d = n && n.detail;
    if (!d || !ID_RE.test(String(d)) || !(await hasDetail(String(d)))) continue;
    const g = details.get(String(d));
    if (g && g.__invalid) { errors.push(`${d}: not valid JSON: ${g.__invalid}`); continue; }
    errors.push(...(await validateGraph(g, 'detail', readLines, hasDetail)).map(x => `${d}: ${x}`));
    graphs += 1; nodes += Array.isArray(g?.nodes) ? g.nodes.length : 0;
  }
  return { ok: !errors.length, graphs, nodes, errors };
}
