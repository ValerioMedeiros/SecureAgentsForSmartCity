[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/valeriomedeiros-secureagentsforsmartcity-badge.png)](https://mseep.ai/app/valeriomedeiros-secureagentsforsmartcity)

# Secure Decision-Making with Auditable LLM Agents for Smart Cities

Research-oriented proof-of-concept implementing a minimal and explainable MAPE-K loop for smart-city traffic adaptation.

## Core Claims Demonstrated

- Separation of reasoning and execution:
  - Planner generates structured candidate plans only.
  - Executor performs side effects only after policy approval.
- Policy enforcement:
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
- `src/smartcity/core/policy_engine.py` - OPA client and fallback policy rules
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

## Prometheus Metrics Experiment

The `tests/experimento_prometheus_tabela.py` script automatically collects metrics
exposed at `/metrics` (port 8010) and from the audit log `logs/audit.jsonl` to
produce the results table for the paper.

### Prerequisites

No external service is required for basic execution — the fallback policy is used
when OPA is unavailable. To measure real OPA latency, bring up the service with
`docker compose up -d opa`.

```bash
# Basic execution (fallback policy, no Docker required)
uv run python tests/experimento_prometheus_tabela.py --runs 10

# With real OPA (requires docker compose up -d opa)
uv run python tests/experimento_prometheus_tabela.py --runs 10 --with-opa

# Full pipeline: OPA + Keycloak + pump_operator approval + real MCP
docker compose up -d opa keycloak citizen-interface pump-mcp
uv run python tests/experimento_prometheus_tabela.py --full --runs 100
```

The `--full` flag enables the end-to-end pipeline: OPA decides → executor blocks →
the script creates an approval request on the Citizen Interface as an anonymous user →
automatically approves it with the `operator/operator123` account (role `pump_operator`,
RBAC validated by Keycloak) → executes the pump action via MCP client with full
instrumentation. If any of the four services is unavailable, the mode gracefully
degrades to the standard mode with a warning.

### Outputs

| File                         | Contents                              |
|------------------------------|---------------------------------------|
| `paper/tabela_resultados.md` | Markdown table ready for the paper    |
| `paper/metricas_raw.json`    | Raw latency samples and counter deltas |

### Collected Metrics

| Metric | Source |
| --- | --- |
| Plan validity rate | Audit log — `PLAN_CREATED` event present per trace_id |
| OPA decision latency p50/p95/p99 | Histogram `stage_duration_seconds{stage=policy_opa}` (delta before/after) |
| End-to-end latency p50/p95/p99, min/mean±std/max | In-process `perf_counter` timing |
| MCP calls per plan | Δ`mcp_calls_total` / Δ`plans_total` |
| Human approval rate | Δ`policy_decisions{approval_mode=human}` / Δ`policy_decisions_total` |
| Unauthorized approval rejection | POST to `/approvals/{id}/decide` with viewer role → expected 403 |
| Trace completeness | Fraction of trace_ids with `PLAN_CREATED` + `EXECUTION_*` in log |
| Policy block rate (OPA) | Δ`executions{status=blocked}` / Δ`executions_total` |

### Results

#### Execution Conditions

| Parameter | Value |
| --- | --- |
| Date | 2026-06-12 |
| Runs per scenario | 100 |
| Mode | `--full` (OPA + Keycloak + CI + Pump MCP) |
| LLM model | `gpt-4o-mini` (request timeout 60 s, ≤ 2 retries) |
| Authorization policy | OPA (`http://localhost:8181`) |
| Simulated human approval | `operator/operator123` (`pump_operator`) via Citizen Interface |
| Planner | LLM with automatic rule-based fallback — 235/300 runs LLM-planned; 65 fallbacks, all in scenario A (63 LLM-as-judge guardrail rejections, 2 schema-validation failures) |

#### Main Results Table

> *Terminology: throughout this section, "guardrail" refers exclusively to the
> model-level LLM-as-judge safety check applied to candidate plans; plan blocking
> performed by OPA is reported as policy enforcement ("policy block").*

| Metric | Scenario A | Scenario B | Adversarial scenario |
| --- | --- | --- | --- |
| Plan validity rate | 100.0% | 100.0% | 100.0% |
| OPA decision latency p50/p95/p99 | 7.8 / 19.2 / 23.9 ms | 7.7 / 20.4 / 25.0 ms | 7.6 / 21.7 / 25.0 ms |
| End-to-end latency p50/p95/p99 | 4,475.1 / 5,957.3 / 7,764.6 ms | 4,979.1 / 6,080.6 / 7,652.9 ms | 4,596.6 / 6,192.2 / 7,928.2 ms |
| MCP calls per plan | 0.46 | 1.00 | 1.00 |
| Human approval rate | 35.0% | 100.0% | 100.0% |
| Unauthorized approval rejection | N/A | N/A | 100.0% |
| Trace completeness | 100.0% | 100.0% | 100.0% |
| Policy block rate (OPA) | 25.9% | 50.0% | 50.0% |

#### Prometheus Counters — Cumulative Deltas per Scenario

| Counter | Scenario A | Scenario B | Adversarial |
| --- | --- | --- | --- |
| Plans generated (`smartcity_plans_total`) | 100 | 100 | 100 |
| Policy decisions (`policy_decisions_total`) | 100 | 100 | 100 |
| Decisions with `approval_mode=human` | 35 | 100 | 100 |
| Total executions (`executions_total`) | 135¹ | 200¹ | 200¹ |
| Blocked executions (`executions{status=blocked}`) | 35 | 100 | 100 |
| MCP calls (`mcp_calls_total`) | 46 | 100 | 100 |

> ¹ In `--full` mode, each run with `approval_mode=human` generates two events in `executions_total`:
> `blocked` (OPA rejects) + `completed` (approval → MCP). Hence `executions_total > runs` in
> scenarios with blocking.

#### End-to-End Latency Distribution (ms, N = 100 runs per scenario)

| Statistic | Scenario A | Scenario B | Adversarial |
| --- | --- | --- | --- |
| Min | 3,666.4 | 3,951.6 | 3,374.8 |
| Mean ± std dev | 4,652.5 ± 837.3 | 5,042.6 ± 667.4 | 4,855.8 ± 874.5 |
| p50 (median) | 4,475.1 | 4,979.1 | 4,596.6 |
| p95 | 5,957.3 | 6,080.6 | 6,192.2 |
| p99 | 7,764.6 | 7,652.9 | 7,928.2 |
| Max | 8,506.5 | 8,957.9 | 8,863.5 |

The 300 raw per-run latency samples are preserved in `paper/metricas_raw.json`
(`e2e_ms_samples`), together with per-scenario aggregated statistics (`e2e_stats`,
`opa_stats`) and Prometheus counter deltas, for independent verification.

### Results Analysis

**Policy enforcement effectiveness with full pipeline.** In `--full` mode, OPA blocks
100% of pump control actions in scenarios B and adversarial before any MCP call is
made. After approval by the `pump_operator` via Citizen Interface, the executor invokes
the real MCP client — reflected in `MCP calls per plan = 1.00` for those scenarios. In
scenario A (low risk), the defense operated in two independent layers: the LLM-as-judge
safety guardrail rejected 63 of 100 LLM-generated plans before policy evaluation
(replaced by the deterministic rule-based fallback), and 2 further plans failed schema
validation. The 35 LLM plans that passed contained an unexpected pump action ("eager"
behavior); all 35 were blocked by OPA and subsequently approved, producing 46 MCP calls
over 100 plans (0.46 — some plans contain two pump steps). This empirically
demonstrates that the model-level guardrail (LLM-as-judge) and the policy enforcement
layer (OPA) act as filters independent of the LLM.

**Measured OPA latency.** With OPA active, the real policy decision latency is
p50 = 7.6–7.8 ms, p95 = 19.2–21.7 ms and p99 = 23.9–25.0 ms across the three
scenarios — negligible overhead relative to LLM plan generation time (≈ 3.4–9.0 s).
With N = 100 runs per scenario, the tail estimates are stable and consistent across
scenarios, eliminating the single-sample artifacts observed in the earlier N = 5 batch.

**Policy block rate with composite executions.** The 50% policy block rate in
scenarios B and adversarial is expected and correct: each run generates a `blocked`
event (OPA) followed by a `completed` event (post-approval). The sum
`blocked / (blocked + completed) = 100/200 = 50%` reflects the real human approval
flow — the policy is not bypassed, but rather completed after explicit authorization.
In scenario A the same arithmetic yields `35/135 = 25.9%`, driven by the 35 eager
LLM plans.

**Auditability and access control.** The 100% trace completeness over all 300 traces
confirms that the hash-chained log covers the full cycle — from policy blocking to MCP
execution confirmation recorded with `actor=full_pipeline`. The unauthorized rejection
test (100% in adversarial) validates that `viewer` receives **403 Forbidden** and
unauthenticated users are redirected to `/login` (303), both tracked in the audit log.

**Statistical robustness.** Each scenario was executed 100 times (300 runs in total),
and the latency tables report dispersion (standard deviation of 667–875 ms) and tail
percentiles (p99 < 8 s) rather than individual runs. End-to-end latency is dominated
by LLM inference in all runs — including fallback runs, which still incur the LLM
generation and safety-check round-trips before reverting to the rule-based planner —
yielding a homogeneous, unimodal latency distribution across scenarios.

---

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

- guardrail and policy enforcement effectiveness,
- latency impact,
- scenario robustness.

Recommended next additions for publication-grade depth:

- strict ablation toggles (`no OPA`, `no structured plan`, `no trace correlation`),
- multi-model sensitivity (same scenarios with different LLMs/prompts),
- automated test suite with pytest.
