#!/usr/bin/env bash
# Agents (Durable Objects) smoke test against a running celld (CELLD_URL,
# default http://127.0.0.1:9876): create an agent, one turn that has to use a
# tool, poll until done, memory written, delete. Costs one small LLM call.
set -euo pipefail
C="${CELLD_URL:-http://127.0.0.1:9876}/apps/agents/_api"
N="smoke-$$"
j() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
curl -sf -X POST "$C/create" -H 'Content-Type: application/json' -d "{\"name\":\"$N\"}" >/dev/null
ID=$(curl -sf -X POST "$C/$N/turn" -H 'Content-Type: application/json' \
  -d '{"message":"Store the key color with the value green in your memory, then answer done."}' | j "d['turn']")
for _ in $(seq 1 60); do
  S=$(curl -sf "$C/$N/turn/$ID" | j "d['status']"); [ "$S" = done ] || [ "$S" = error ] && break; sleep 2
done
curl -sf "$C/$N/turn/$ID" | j "d['status'], d['steps']"
[ "$S" = done ] || { echo "FAIL: turn $S"; exit 1; }
[ "$(curl -sf "$C/$N/memory" | j "d.get('color','')")" = green ] || { echo "FAIL: memory not written"; exit 1; }
curl -sf -X POST "$C/$N/delete" -H 'Content-Type: application/json' -d '{}' >/dev/null
curl -sf "$C/" | j "'$N' not in [a['name'] for a in d]" | grep -q True || { echo "FAIL: still listed"; exit 1; }
echo "agents smoke test: OK"
