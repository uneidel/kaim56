# kAIm56 — Tests

Vollständige E2E-/Unit-Tests, stdlib-only (`unittest`, keine Dependency).

```bash
./run-tests.sh                 # alles
python3 tests/e2e.py AgentLogic        # nur eine Klasse
python3 tests/e2e.py -v                 # ausführlich
```

## Drei Stufen (überspringen sich selbst, wenn die Umgebung fehlt)

- **OFFLINE** (immer): importiert `openrouter-agent/agent.py` und `manager.py`
  direkt und testet die Kernlogik ohne VM/Netz:
  - Agent: Backend-Auswahl (openrouter/orcarouter/llama), Context-Offloader +
    `offload_read`, Summarizing-`_trim_history` (inkl. Einfalten alter
    Zusammenfassung), Tool-Hook-Denylist, `/goal`, `or_chat` lässt leere
    tools-Liste weg.
  - Manager: `set_model`-Provider-Switch (inkl. `:free`-Fallstrick), HITL-Store,
    `katfs_zip`-Rekursion, `PROVIDER_MODEL_KEY ⊆ MODEL_KEYS`.
- **HTTP** (wenn Manager auf `127.0.0.1:8700` läuft): `/api/agents` liefert
  `backend`+`model`, orchestrator meldet sein echtes Backend, `/api/hitl/<id>`,
  `/api/katfs/status`, orcarouter-Template registriert.
- **LIVE** (wenn Orchestrator-VM läuft): `/goal`-Roundtrip — beweist, dass der
  neue Agent-Code in der VM lebt, **ohne** Modellaufruf (keine Token-Kosten).

Exit-Code ≠ 0 bei Fehlern → als Pre-Deploy-Gate nutzbar.
`MANAGER_URL` überschreibt das Ziel für die HTTP-/LIVE-Stufe.
