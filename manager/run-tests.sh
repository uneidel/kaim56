#!/usr/bin/env bash
# kAIm56 E2E-Tests. Laeuft die stdlib-unittest-Suite (Agent + Manager).
# Offline-Tests immer; HTTP-/Live-Tests nur, wenn Manager/Orchestrator laufen
# (sonst sauber uebersprungen). Exit != 0 bei Fehlern -> als Gate nutzbar.
cd "$(dirname "$0")" || exit 1

# Schritt 0: Undefined-Name-Gate. Ein fehlender Import faellt erst beim
# AUFRUF um — zwei echte Bugs in einer Woche (uuid in store.py: Task-Anlage
# 13 Tage tot; re im Agenten) plus vier schlafende beim Einfuehren gefunden
# (save_gateway, base64 im Gateway, beide Secret-Funktionen in mcp.py).
python3 tests/check_names.py \
    manager.py chatui.py webterm.py text_unicode.py mgr/ tests/check_names.py \
    /home/ulrich/openrouter-agent/agent.py \
    /home/ulrich/openrouter-agent/run_agent.py \
    /home/ulrich/claude-signal-firecracker/web_bridge.py \
    /home/ulrich/claude-signal-firecracker/kaim56_mcp.py || exit 1

# Schritt 0b: ruff (Pyflakes-Klasse), optional — reines Dev-Werkzeug, keine
# Laufzeit-Abhaengigkeit. Faellt still aus, wo ruff nicht installiert ist.
if command -v ruff >/dev/null 2>&1 || [ -x "$HOME/.local/bin/ruff" ]; then
    RUFF=$(command -v ruff || echo "$HOME/.local/bin/ruff")
    "$RUFF" check --select F --isolated --quiet \
        manager.py chatui.py webterm.py text_unicode.py mgr/ \
        /home/ulrich/openrouter-agent/agent.py \
        /home/ulrich/openrouter-agent/run_agent.py \
        /home/ulrich/claude-signal-firecracker/web_bridge.py \
        /home/ulrich/claude-signal-firecracker/kaim56_mcp.py || exit 1
fi

# Schritt 0c: Desktop-Sprachclient (Go; lebt nur im Repo, laeuft auf dem
# Nutzer-PC — hier laufen seine mikrofonfreien Unit-Tests plus go vet).
# Uebersprungen, wo keine Go-Toolchain ist: der Client ist kein Serverteil.
VC=/home/ulrich/kaim56/voice-client
GO=$(command -v go || echo "$HOME/.local/go-toolchain/bin/go")
if [ -d "$VC" ] && [ -x "$GO" ]; then
    (cd "$VC" && "$GO" vet ./... && "$GO" test ./...) >/dev/null 2>&1 \
        || { echo "voice-client (go) FAILED:"; (cd "$VC" && "$GO" vet ./... && "$GO" test ./...); exit 1; }
fi

exec python3 tests/e2e.py "$@"
