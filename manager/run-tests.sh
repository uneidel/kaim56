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

exec python3 tests/e2e.py "$@"
