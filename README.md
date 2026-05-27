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

### Security
- Requests user token for approval when necessary
- Entry point: `src/smartcity/core/security.py`.


### Knowledge/Audit
- Structured JSON logs in stdout and file (`logs/traces.jsonl`).
- Tamper-evident audit log with SHA-256 hash chain (`logs/audit.jsonl`) covering plan creation, policy decisions, executor verdicts, and every MCP tool invocation.
- Dashboard for trace reconstruction with audit-chain verification and per-stage latency: `src/smartcity/ui/dashboard.py`.

### Observability

- Prometheus metrics exposed at `GET /metrics` on the monitor service (port 8010).
- Metrics include: `smartcity_plans_total`, `smartcity_policy_decisions_total`, `smartcity_executions_total`, `smartcity_mcp_calls_total`, `smartcity_errors_total`, and `smartcity_stage_duration_seconds` (histogram per stage/component for the `monitor`, `plan`, `policy`, `policy_opa`, `execute`, `mcp_call_server`, and `mcp_call_client` stages).
- Audit query endpoints on the monitor service:
  - `GET /audit/entries?trace_id=&plan_id=&component=&event_type=&limit=`
  - `GET /audit/entries/{id}`
  - `GET /audit/verify` — walks the hash chain and reports integrity issues.

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
- `src/smartcity/infra/pump_mcp/server.py` - Pump MCP server
- `src/smartcity/infra/pump_mcp_client.py` - Pump MCP client helpers
- `src/smartcity/services/monitor.py` - monitor endpoint and event loop trigger
- `src/smartcity/app/examples_llm_planner.py` - interactive planner examples (with optional execution)
- `src/smartcity/app/host_simulator.py` - scenario runner (alternative, parametrized by SCENARIO env var)
- `src/smartcity/app/experiments.py` - experiment routines
- `src/smartcity/app/init_traffic_signal.py` - seed helper
- `src/smartcity/app/inspect_traffic_signal.py` - inspection helper
- `src/smartcity/ui/dashboard.py` - Streamlit trace dashboard

### Other folders

- `policies/traffic_policy.rego` - Rego policy
- `docs/IMPLEMENTATION_NOTES.md` - implementation notes

## Quickstart (Windows PowerShell)

### 1) One-time setup

Create and activate a virtual environment and install the package (uses `pyproject.toml`):

```bash
uv sync
```

or

```powershell
python -m venv .venv
. .\.venv\Scripts\Activate.ps1
pip install -e .
```

### 2) Start infrastructure

Start all services with Docker Compose (will build images defined in `docker-compose.yml`):

```powershell
docker compose up -d --build
```

Wait until the MCP servers (pump/fiware/weather) and the monitor are healthy before proceeding.

### 4) Initialize MCP-backed resources (required)

Before running scenarios or examples you MUST seed the pump and weather entities so the system can operate. Run these once after the infrastructure is up:

```powershell
# from project root (with venv activated)
python -m src.smartcity.app.init_pumps
python -m src.smartcity.app.init_weather_station
```

These scripts register pump endpoints and the weather station with the MCP/NGSI backends used by the executor and monitor.

### 5) Set environment variables for a session

Copy the example env and edit as needed:

```powershell
cp .env.example .env
```

### 6) Run core flow

Run the example planner:

```powershell
# Generate plans only
python -m src.smartcity.app.examples_llm_planner

```

When run interactively the `examples_llm_planner` module will prompt you to choose which example to run (1–4) or `a` to run all examples in order. Use the `EXECUTE_PLANS` environment variable to enable execution of generated plans.

### 7) Optional: dashboard, experiments

```powershell

streamlit run src/smartcity/ui/dashboard.py
python -m src.smartcity.app.experiments

```

## Running Plans

### Option 1: Interactive Examples with Plan Execution

The `examples_llm_planner.py` script demonstrates 4 planning scenarios, prints the generated plan JSON, and can optionally execute them:

```bash
# Generate plans only (no execution)
uv run -m src.smartcity.app.examples_llm_planner

# Generate plans AND execute them (with policy evaluation)
$env:EXECUTE_PLANS="true"
uv run -m src.smartcity.app.examples_llm_planner
```

This is useful for:
- Exploring plan generation across different event types
- Testing policy decisions and approval flows
- Verifying end-to-end execution in a controlled manner

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

## Authentication

Authentication in this project is intentionally minimal and handled via environment
variables and the operator UI:

- **OPENAI_API_KEY**: required when `LLM_PLANNER_ENABLED=true`. Set this in your
  `.env` file or environment; an example is provided in `.env.example`:
  `OPENAI_API_KEY=sk-your-openai-api-key-here`.
- **Human approvals**: operator decisions (for `human` approval mode) are
  performed through the citizen-facing dashboard (`src/smartcity/services/citizen_interface.py`).
  We made available de following users/tokens:
  - admin: token123
  - operator: token456
  - viewer: token789
- **MCP / service auth**: MCP servers run without token-based auth by default in
  this repository. Production deployments should add transport-level security
  (TLS, mTLS) or API tokens and configure the clients accordingly.

Docker Compose wires the `OPENAI_API_KEY` into the `monitor` service; set it
before `docker compose up` or copy `.env.example` to `.env` and edit as needed.


The repository now supports the main experiment categories:

- guardrail effectiveness,
- latency impact,
- scenario robustness.

Recommended next additions for publication-grade depth:

- strict ablation toggles (`no OPA`, `no structured plan`, `no trace correlation`),
- multi-model sensitivity (same scenarios with different LLMs/prompts),
- automated test suite with pytest.
