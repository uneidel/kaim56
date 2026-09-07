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
