# kAIm56 — Tests

Complete E2E/unit tests, stdlib-only (`unittest`, no dependency).

```bash
./run-tests.sh                 # everything
python3 tests/e2e.py AgentLogic        # just one class
python3 tests/e2e.py -v                 # verbose
```

## Three stages (they skip themselves when the environment is missing)

- **OFFLINE** (always): imports `openrouter-agent/agent.py` and `manager.py`
  directly and tests the core logic without VM/network:
  - Agent: backend selection (openrouter/orcarouter/llama), context offloader +
    `offload_read`, summarizing `_trim_history` (incl. folding in the old
    summary), tool-hook denylist, `/goal`, `or_chat` omits an empty
    tools list.
  - Manager: `set_model` provider switch (incl. the `:free` pitfall), HITL store,
    `katfs_zip` recursion, `PROVIDER_MODEL_KEY ⊆ MODEL_KEYS`.
- **HTTP** (when the manager runs on `127.0.0.1:8700`): `/api/agents` returns
  `backend`+`model`, orchestrator reports its real backend, `/api/hitl/<id>`,
  `/api/katfs/status`, orcarouter template registered.
- **LIVE** (when the orchestrator VM is running): `/goal` roundtrip — proves that the
  new agent code lives in the VM, **without** a model call (no token cost).

Exit code ≠ 0 on failures → usable as a pre-deploy gate.
`MANAGER_URL` overrides the target for the HTTP/LIVE stage.
