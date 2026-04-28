# Secure Decision-Making with Auditable LLM Agents for Smart Cities

Research-oriented proof-of-concept implementing a minimal and explainable MAPE-K loop for smart-city traffic adaptation.

## Core Claims Demonstrated

- Separation of reasoning and execution:
  - Planner generates structured candidate plans only.
  - Executor performs side effects only after policy approval.
- Policy guardrails:
  - OPA/Rego controls whether plans are auto-approved, require human approval, or denied.
- End-to-end auditability:
  - Every stage is correlated by trace ID and persisted as JSON logs.

## Architecture

### Monitor
- Receives NGSI notifications and normalizes events.
- Entry point: `src.smartcity.services.monitor:app` (`/monitor/notify`).

### Analyze/Plan
- Converts event context into a candidate plan using strict schema.
- Entry point: `src/smartcity/core/planner.py`.

### Policy
- Evaluates candidate plan with OPA policy-as-code.
- Falls back to deterministic local rules if OPA is unavailable.
- Entry point: `src/smartcity/core/policy_engine.py`.

### Execute
- Executes approved plan steps through MCP tools.
- Entry point: `src/smartcity/core/executor.py`.

### Knowledge/Audit
- Structured JSON logs in stdout and file (`logs/traces.jsonl`).
- Dashboard for trace reconstruction: `src/smartcity/ui/dashboard.py`.

## Repository Layout

The project now follows a layered package structure under `src/smartcity`.
Root-level Python files are kept as compatibility wrappers, so existing commands still work.

### Core package (`src/smartcity`)

- `src/smartcity/core/plan_schema.py` - typed schemas and validators
- `src/smartcity/core/planner.py` - candidate plan generation
- `src/smartcity/core/policy_engine.py` - OPA client and fallback guardrails
- `src/smartcity/core/executor.py` - policy-gated execution
- `src/smartcity/infra/logging_utils.py` - JSON logging utilities
- `src/smartcity/infra/ngsi_client.py` - NGSI-v2 entity and subscription helpers
- `src/smartcity/services/mcp_server.py` - MCP API surface
- `src/smartcity/services/monitor.py` - monitor endpoint and event loop trigger
- `src/smartcity/app/host_simulator.py` - scenario runner
- `src/smartcity/app/experiments.py` - experiment routines
- `src/smartcity/app/init_traffic_signal.py` - seed helper
- `src/smartcity/app/inspect_traffic_signal.py` - inspection helper
- `src/smartcity/ui/dashboard.py` - Streamlit trace dashboard

### Other folders

- `policies/traffic_policy.rego` - Rego policy
- `docs/IMPLEMENTATION_NOTES.md` - implementation notes

## Quickstart (Windows PowerShell)

### 1) One-time setup

```bash
uv python install 3.11
uv sync # Install uv for easier
```

### 2) Start infrastructure

```bash
docker compose up -d
```

### 3) Set environment variables on .env file (session)

```bash
USER_TOKEN="user-token"
HUMAN_APPROVAL_TOKEN="human-approval-token"
OPA_URL="http://localhost:8181"
OPA_POLICY_PATH="v1/data/smartcity/allow"
JSON_LOG_FILE="logs/traces.jsonl"
```

### 4) Run core flow (minimal)

Terminal 1:
```bash
uv run uvicorn src.smartcity.services.mcp_server:app --host 0.0.0.0 --port 8000
```

Terminal 2:
```bash
uv run -m src.smartcity.app.init_traffic_signal
$env:SCENARIO="A"
uv run -m src.smartcity.app.host_simulator
```

### 5) Optional: monitor, dashboard, experiments

```bash
uv run uvicorn src.smartcity.services.monitor:app --host 0.0.0.0 --port 8010
uv run streamlit run src/smartcity/ui/dashboard.py
uv run -m src.smartcity.app.experiments
```

Scenarios:
- `A`: ambulance-only
- `B`: flood-only
- `C`: combined-flood-corridor

Note: root files are compatibility wrappers. Prefer `python -m src.smartcity...` and `uvicorn src.smartcity...` commands to avoid path/cwd issues.

## Plan Schema and Explainability

Candidate plans are validated with Pydantic before policy and execution. Required action parameters are enforced and malformed plans are rejected early. This provides:

- deterministic structure for paper review,
- explicit risk level and approval mode,
- repeatable policy decisions.

## Policy Mapping

Implemented in Rego and fallback logic:

- `low` -> `auto` -> green
- `medium` -> `human` -> yellow (requires human token)
- `high` -> `deny` -> red

## Notes for Evaluation

The repository now supports the main experiment categories:

- guardrail effectiveness,
- latency impact,
- scenario robustness.

Recommended next additions for publication-grade depth:

- strict ablation toggles (`no OPA`, `no structured plan`, `no trace correlation`),
- multi-model sensitivity (same scenarios with different LLMs/prompts),
- automated test suite with pytest.
