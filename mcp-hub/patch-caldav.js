// kAIm56 — build-time patches for caldav-mcp 0.10.0 (run once in the mcp-hub image).
// Each patch anchors on an exact source line and FAILS the build if the anchor
// moved — a newer caldav-mcp then has to be re-checked, not silently unpatched.
const fs = require("fs");
const root = require("child_process").execSync("npm root -g").toString().trim() + "/caldav-mcp/dist/tools/";
function patch(file, anchor, replacement, why) {
  const p = root + file;
  let s = fs.readFileSync(p, "utf8");
  if (!s.includes(anchor)) throw new Error(`caldav-mcp patch anchor not found in ${file} (${why}) — check the version`);
  fs.writeFileSync(p, s.replace(anchor, replacement));
  console.log(`caldav-mcp: ${file}: ${why}`);
}
// 1) list-calendars returns RELATIVE hrefs from sabre/Nextcloud, the other
//    tools accept only absolute URLs -> "Failed to retrieve vevents".
patch("list-calendars.js",
  'const calendars = await client.getCalendars();',
  'const calendars = (await client.getCalendars()).map((c) => ({ ...c, url: new URL(c.url, process.env.CALDAV_BASE_URL || "http://localhost/").href }));',
  "absolute calendar URLs");
// 2) list-events serialized Date objects as UTC ISO ("...Z"); the model read
//    them out as local time (11:30 became 9:30). Format in the process TZ
//    (the manager passes the host zone), whole-day events as a plain date.
patch("list-events.js",
  `        const data = allEvents.map((e) => ({
            uid: e.uid,
            summary: e.summary,
            start: e.start,
            end: e.end,`,
  `        const tz = process.env.TZ || "UTC";
        const local = new Intl.DateTimeFormat("sv-SE", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
        const dayOnly = new Intl.DateTimeFormat("sv-SE", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" });
        const fmt = (d, whole) => {
            if (!(d instanceof Date) || Number.isNaN(d.getTime())) return d;
            // whole-day: the library parses DATE values as local midnight — the
            // calendar DAY must be read in the same zone, not via toISOString (UTC).
            if (whole) return dayOnly.format(d);
            return local.format(d) + " " + tz;
        };
        const data = allEvents.map((e) => ({
            uid: e.uid,
            summary: e.summary,
            start: fmt(e.start, e.wholeDay),
            end: fmt(e.end, e.wholeDay),
            ...(e.wholeDay && { wholeDay: true }),
            timezone: tz,`,
  "event times in local time, whole-day as date");

// 3) create/update-event and the todo tools validate start/end/due/until
//    with zod `datetime({ offset: true })` and REJECT anything else with
//    "Invalid ISO datetime". The model writes what it sees — after (2) that is
//    "2026-09-08 18:30 Europe/Berlin", or a bare "2026-09-08T18:30:00" — and
//    every event creation failed. Normalize such inputs to ISO with the
//    zone's offset before validation; real ISO strings pass through untouched.
const helper = `
const __normDt = (v) => {
  if (typeof v !== "string") return v;
  const m = v.trim().match(/^(\\d{4})-(\\d{2})-(\\d{2})(?:[T ](\\d{2}):(\\d{2})(?::(\\d{2}))?)?(?:\\s+([A-Za-z_]+\\/[A-Za-z_]+))?$/);
  if (!m) return v;
  const tz = m[7] || process.env.TZ || "UTC";
  const Y = +m[1], Mo = +m[2], D = +m[3], h = +(m[4] || 0), mi = +(m[5] || 0), s = +(m[6] || 0);
  const wallUtc = Date.UTC(Y, Mo - 1, D, h, mi, s);
  const f = new Intl.DateTimeFormat("sv-SE", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const wallOf = (t) => { const p = f.format(new Date(t)).match(/\\d+/g).map(Number); return Date.UTC(p[0], p[1] - 1, p[2], p[3] % 24, p[4], p[5]); };
  let inst = wallUtc;
  for (let i = 0; i < 2; i++) inst += wallUtc - wallOf(inst);
  const off = Math.round((wallUtc - inst) / 60000), sign = off >= 0 ? "+" : "-", a = Math.abs(off);
  const pad = (n) => String(n).padStart(2, "0");
  return pad(Y).padStart(4, "0") + "-" + pad(Mo) + "-" + pad(D) + "T" + pad(h) + ":" + pad(mi) + ":" + pad(s) + sign + pad(Math.floor(a / 60)) + ":" + pad(a % 60);
};
`;
for (const file of ["create-event.js", "update-event.js", "create-todo.js", "update-todo.js", "list-todos.js"]) {
  const p = root + file;
  let s = fs.readFileSync(p, "utf8");
  const re = /z\s*\.string\(\)\s*\.datetime\(\{ offset: true \}\)/g;
  const n = (s.match(re) || []).length;
  if (!n) throw new Error(`caldav-mcp patch anchor not found in ${file} (datetime normalization) — check the version`);
  s = s.replace(re, 'z.preprocess(__normDt, z.string().datetime({ offset: true }))');
  const imp = 'import { z } from "zod";';
  if (!s.includes(imp)) throw new Error(`caldav-mcp: zod import not found in ${file}`);
  s = s.replace(imp, imp + helper);
  fs.writeFileSync(p, s);
  console.log(`caldav-mcp: ${file}: ${n} datetime field(s) accept local/zone forms`);
}
