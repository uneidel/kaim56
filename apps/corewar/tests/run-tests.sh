#!/usr/bin/env bash
# Core War app tests: coach.js (task text, answer parser, diff) and the engine's mid-round patch.
set -euo pipefail
cd "$(dirname "$0")"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
cp ../coach.js ../mars.js test.mjs "$T/"
echo '{"type": "module"}' > "$T/package.json"      # the app's .js files are ES modules
if command -v node >/dev/null; then node "$T/test.mjs"
else docker run --rm -v "$T":/w node:22-alpine node /w/test.mjs 2>/dev/null || docker run --rm -v "$T":/w kaim56-mcp-hub node /w/test.mjs; fi
