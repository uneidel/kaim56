#!/usr/bin/env bash
# Code flow app tests: the Python checker, then the JS checker in parity with it, and lib.js.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m unittest -q test_validate.py
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
cp ../flowcheck.js ../lib.js ../machine.js test.mjs fixtures.json py-results.json "$T/"
echo '{"type": "module"}' > "$T/package.json"      # the app's .js files are ES modules
if command -v node >/dev/null; then node "$T/test.mjs"
else docker run --rm -v "$T":/w node:22-alpine node /w/test.mjs 2>/dev/null || docker run --rm -v "$T":/w kaim56-mcp-hub node /w/test.mjs; fi
