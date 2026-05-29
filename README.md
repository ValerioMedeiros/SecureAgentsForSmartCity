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

### Identity & Access Management (IAM — Keycloak)
- Keycloak authenticates operators and issues scoped credentials (realm roles).
- The Citizen-Facing Interface enforces the authorization boundary between plan
  generation and execution: a human approval can only be granted by an
  authenticated operator whose role permits the requested actuation.
- Realm roles map to permitted actions:
  - `pump_admin` → `turnOnPump`, `turnOffPump`, `notifyUser`
  - `pump_operator` → `turnOnPump`, `turnOffPump`
  - `viewer` → `notifyUser`
- The authenticated operator identity is recorded in the hash-chained audit log
  (`APPROVAL_DECIDED`/`APPROVAL_FORBIDDEN` events carry `actor=<username>`),
  closing the end-to-end accountability loop.
- Entry points: `src/smartcity/infra/keycloak_auth.py` (authentication and JWT
  validation), `src/smartcity/core/security.py` (role → action mapping).
- The realm (`smartcity`), the confidential client (`smartcity-poc`), the three
  roles and the test users are provisioned automatically on startup from
  `keycloak/realm-export.json` (`start-dev --import-realm`).


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

## Licensing

The project source code is released under the MIT License. The Docker Compose
demonstration uses third-party runtime components under their own licenses,
including MongoDB Server under SSPL-1.0, FIWARE Orion Context Broker under
AGPL-3.0, and Open Policy Agent under Apache-2.0.

This Compose setup is intended for local reproduction of the scientific
demonstration. It should not be described as a stack whose every component is
OSI-approved open source. See `THIRD_PARTY_NOTICES.md` for the component-level
notices and suggested wording for publication.

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
Keycloak takes ~20–40s on first start to import the `smartcity` realm; it is ready once
<http://localhost:8090/realms/smartcity> responds.

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

## Authentication & IAM (Keycloak)

Operator authentication is delegated to **Keycloak**, the IAM Generic Enabler of
the proposed architecture. Keycloak comes up as part of `docker compose` and the
realm is provisioned automatically on startup — no manual setup required.

### What is provisioned

`keycloak/realm-export.json` is imported on startup (`start-dev --import-realm`)
and creates:

- realm `smartcity`;
- confidential client `smartcity-poc` (direct access grant enabled);
- realm roles `pump_admin`, `pump_operator`, `viewer`;
- three test users:
  - **admin** / `admin123` → `pump_admin`
  - **operator** / `operator123` → `pump_operator`
  - **viewer** / `viewer123` → `viewer`

The Keycloak admin console is available at <http://localhost:8090> (admin/admin).

### How it is enforced

- The operator logs in at the Citizen-Facing Interface (`/login`,
  <http://localhost:8020/login>). The interface authenticates the credentials
  against Keycloak via the OAuth2 password grant and starts a session.
- A pending pump approval can only be granted by an authenticated operator whose
  realm role permits the requested action (`pump_operator`/`pump_admin` for pump
  actuation). A `viewer` attempting to approve a pump action receives **403
  Forbidden**, and an unauthenticated request is redirected to `/login`.
- Bearer tokens are validated locally against the realm JWKS (RS256 signature,
  issuer and expiry). The authenticated identity is written to the hash-chained
  audit log, so every approval is attributable to a named operator.
- Set `KEYCLOAK_ENABLED=false` to run the PoC in open mode (no authentication),
  in which case the static fallback tokens below apply.

### Helper

Fetch an access token from the command line (defaults to operator):

```bash
./keycloak/get_token.sh operator operator123
```

### Other credentials

- **OPENAI_API_KEY**: required when the LLM agent is enabled. Set it in `.env`
  (see `.env.example`). Docker Compose wires it into the `monitor` service.
- **Static fallback tokens** (used only when `KEYCLOAK_ENABLED=false`):
  admin → `token123`, operator → `token456`, viewer → `token789`.
- **MCP / service auth**: MCP servers run without token-based auth by default in
  this repository. Production deployments should add transport-level security
  (TLS, mTLS) or API tokens and configure the clients accordingly.


The repository now supports the main experiment categories:

- guardrail effectiveness,
- latency impact,
- scenario robustness.

Recommended next additions for publication-grade depth:

- strict ablation toggles (`no OPA`, `no structured plan`, `no trace correlation`),
- multi-model sensitivity (same scenarios with different LLMs/prompts),
- automated test suite with pytest.
