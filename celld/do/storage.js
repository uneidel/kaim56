// kAIm56 — a Durable Object's whole storage as JSON, out and back in. Moves an
// app's state between celld and Cloudflare (mgr/cloud.py): GET <app>/_api/export,
// POST <app>/_api/import {entries, replace}. Values are what the apps store:
// plain JSON (objects, strings, numbers).
// SPDX-License-Identifier: AGPL-3.0-or-later
const STORAGE_PAGE = 1000;
const STORAGE_PUT_MAX = 128;                 // keys per put()

export async function storageExport(ctx) {
  const out = [];
  let startAfter;
  for (;;) {
    const opts = { limit: STORAGE_PAGE };
    if (startAfter !== undefined) opts.startAfter = startAfter;
    const page = await ctx.storage.list(opts);
    for (const [k, v] of page) { out.push([k, v]); startAfter = k; }
    if (page.size < STORAGE_PAGE) break;
  }
  return out;
}

export async function storageImport(ctx, body) {
  const entries = Array.isArray(body?.entries) ? body.entries : null;
  if (!entries || !entries.every(e => Array.isArray(e) && e.length === 2 && typeof e[0] === "string")) {
    return { error: "entries must be [[key, value], …]" };
  }
  if (body.replace) await ctx.storage.deleteAll();
  for (let i = 0; i < entries.length; i += STORAGE_PUT_MAX) {
    await ctx.storage.put(Object.fromEntries(entries.slice(i, i + STORAGE_PUT_MAX)));
  }
  return { ok: true, imported: entries.length, total: (await storageExport(ctx)).length };
}
