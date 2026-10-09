// kAIm56 — build-time patch for @playwright/mcp 0.0.83: hide two tools from
// list AND call (the server only dispatches what filteredTools returns).
//  - browser_run_code_unsafe: arbitrary Node/Playwright code in this container.
//  - browser_file_upload: reads files of THIS container; the agent's files are
//    not here, so it can only leak container files to a website.
// Fails the build if the anchor moved — a newer version must be re-checked.
const fs = require("fs");
const path = require("path");
const root = require("child_process").execSync("npm root -g").toString().trim() + "/@playwright/mcp";
const anchor = ".filter((tool) => !tool.skillOnly).map((tool) => ({";
const repl = '.filter((tool) => !tool.skillOnly && !["browser_run_code_unsafe", "browser_file_upload"].includes(tool.schema.name)).map((tool) => ({';
let n = 0;
(function walk(d) {
  for (const e of fs.readdirSync(d, { withFileTypes: true })) {
    const p = path.join(d, e.name);
    if (e.isDirectory()) walk(p);
    else if (e.name === "coreBundle.js") {
      const s = fs.readFileSync(p, "utf8");
      if (s.split(anchor).length === 2) { fs.writeFileSync(p, s.replace(anchor, repl)); n++; console.log("patched", p); }
    }
  }
})(root);
if (!n) throw new Error("playwright-mcp tool-filter anchor not found — check the version");
