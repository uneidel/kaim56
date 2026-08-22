#!/usr/bin/env bash
# kAIm56 E2E-Tests. Laeuft die stdlib-unittest-Suite (Agent + Manager).
# Offline tests always; HTTP/live tests only when manager/orchestrator run
# (otherwise cleanly skipped). Exit != 0 on failures -> usable as a gate.
cd "$(dirname "$0")" || exit 1
exec python3 tests/e2e.py "$@"
