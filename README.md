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

## Experimento de Métricas (Prometheus)

O script `tests/experimento_prometheus_tabela.py` coleta automaticamente as métricas
expostas em `/metrics` (porta 8010) e do log de auditoria `logs/audit.jsonl` para
produzir a tabela de resultados do artigo.

### Pré-requisitos

Nenhum serviço externo é necessário para a execução básica — a política de fallback
é usada quando OPA não está disponível. Para medir a latência real do OPA, suba o
serviço com `docker compose up -d opa`.

```bash
# Execução básica (política de fallback, sem Docker obrigatório)
uv run python tests/experimento_prometheus_tabela.py --runs 10

# Com OPA real (requer docker compose up -d opa)
uv run python tests/experimento_prometheus_tabela.py --runs 10 --with-opa

# Pipeline completo: OPA + Keycloak + aprovação pump_operator + MCP real
docker compose up -d opa keycloak citizen-interface pump-mcp
uv run python tests/experimento_prometheus_tabela.py --full --runs 5
```

O flag `--full` habilita o pipeline de ponta a ponta: OPA decide → executor bloqueia → o
script cria um pedido de aprovação na Citizen Interface como usuário anônimo → aprova
automaticamente com a conta `operator/operator123` (papel `pump_operator`, RBAC validado
pelo Keycloak) → executa a ação de bomba via MCP client com instrumentação completa. Se
algum dos quatro serviços estiver indisponível, o modo degrada automaticamente para o
padrão com aviso.

### Saídas

| Arquivo | Conteúdo |
|---------|---------|
| `paper/tabela_resultados.md` | Tabela Markdown pronta para o artigo |
| `paper/metricas_raw.json` | Amostras brutas de latência e deltas de contadores |

### Métricas coletadas

| Métrica | Fonte |
| --- | --- |
| Plan validity rate | Log de auditoria — evento `PLAN_CREATED` presente por trace_id |
| OPA decision latency p50/p95 | Histograma `stage_duration_seconds{stage=policy_opa}` (delta before/after) |
| End-to-end latency p50/p95 | Timing `perf_counter` in-process |
| MCP calls per plan | Δ`mcp_calls_total` / Δ`plans_total` |
| Human approval rate | Δ`policy_decisions{approval_mode=human}` / Δ`policy_decisions_total` |
| Unauthorized approval rejection | POST a `/approvals/{id}/decide` com papel viewer → 403 esperado |
| Trace completeness | Fração de trace_ids com `PLAN_CREATED` + `EXECUTION_*` no log |
| Guardrail block rate | Δ`executions{status=blocked}` / Δ`executions_total` |

### Resultados obtidos

#### Condições de execução

| Parâmetro | Valor |
| --- | --- |
| Data | 2026-05-30 |
| Runs por cenário | 5 |
| Modo | `--full` (OPA + Keycloak + CI + Pump MCP) |
| Modelo LLM | `gpt-4o-mini` |
| Política de autorização | OPA (`http://localhost:8181`) |
| Aprovação humana simulada | `operator/operator123` (`pump_operator`) via Citizen Interface |
| Planner | LLM com fallback rule-based automático |

#### Tabela de resultados principais

| Métrica | Cenário A | Cenário B | Cenário adversarial |
| --- | --- | --- | --- |
| Plan validity rate | 100.0% | 100.0% | 100.0% |
| OPA decision latency p50/p95 | 8.1 / 21.2 ms | 15.6 / 24.1 ms | 17.5 / 43.8 ms |
| End-to-end latency p50/p95 | 5 312.5 / 6 869.5 ms | 3 811.1 / 4 017.1 ms | 4 256.3 / 7 180.5 ms |
| MCP calls per plan | 0.40 | 1.00 | 1.00 |
| Human approval rate | 20.0% | 100.0% | 100.0% |
| Unauthorized approval rejection | N/A | N/A | 100.0% |
| Trace completeness | 100.0% | 100.0% | 100.0% |
| Guardrail block rate | 16.7% | 50.0% | 50.0% |

#### Contadores Prometheus — deltas acumulados por cenário

| Contador | Cenário A | Cenário B | Adversarial |
| --- | --- | --- | --- |
| Planos gerados (`smartcity_plans_total`) | 5 | 5 | 5 |
| Decisões de política (`policy_decisions_total`) | 5 | 5 | 5 |
| Decisões com `approval_mode=human` | 1 | 5 | 5 |
| Execuções total (`executions_total`) | 6¹ | 10¹ | 10¹ |
| Execuções bloqueadas (`executions{status=blocked}`) | 1 | 5 | 5 |
| Chamadas MCP (`mcp_calls_total`) | 2 | 5 | 5 |

> ¹ No modo `--full`, cada run com `approval_mode=human` gera dois eventos em `executions_total`:
> `blocked` (OPA recusa) + `completed` (aprovação → MCP). Por isso `executions_total > runs` nos
> cenários com bloqueio.

#### Distribuição de latência ponta-a-ponta por run (ms)

| Run | Cenário A | Cenário B | Adversarial |
| --- | --- | --- | --- |
| 1 | 7 201.9 | 3 811.1 | 3 525.8 |
| 2 | 5 539.9 | 4 024.0 | 4 150.8 |
| 3 | 5 086.3 | 3 586.2 | 4 413.2 |
| 4 | 5 312.5 | 3 989.5 | 4 256.3 |
| 5 | 4 497.1 | 3 547.7 | 7 872.4 |
| **Mín** | **4 497.1** | **3 547.7** | **3 525.8** |
| **Média** | **5 527.5** | **3 791.7** | **4 843.7** |
| **Máx** | **7 201.9** | **4 024.0** | **7 872.4** |

### Análise dos resultados

**Eficácia dos guardrails com pipeline completo.** No modo `--full`, o OPA bloqueia
100% das ações de controle de bomba nos cenários B e adversarial antes de qualquer
chamada MCP. Após aprovação pelo `pump_operator` via Citizen Interface, o executor
invoca o MCP client real — o que se reflete em `MCP calls per plan = 1.00` para esses
cenários. No cenário A (baixo risco), 1 de 5 planos foi gerado pelo LLM com ação de
bomba inesperada (comportamento "entusiasmado"), bloqueado e depois aprovado, resultando
em 2 MCP calls para 5 planos (0.40). Isso demonstra empiricamente que a camada de
política age como filtro independente do LLM.

**Latência OPA medida.** Com OPA ativo, a latência de decisão política real é
p50 = 8–18 ms e p95 = 21–44 ms nos três cenários — overhead negligenciável em relação
ao tempo de geração do plano LLM (≈ 3,5–7 s). O p95 levemente mais alto no cenário
adversarial (43,8 ms) corresponde a uma única avaliação mais lenta, não a degradação
sistemática.

**Guardrail block rate com execuções compostas.** O block rate de 50% nos cenários B
e adversarial é esperado e correto: cada run gera um evento `blocked` (OPA) seguido de
um evento `completed` (pós-aprovação). A soma `blocked / (blocked + completed) = 5/10 = 50%`
reflete o fluxo real de aprovação humana — a política não é contornada, apenas
concluída após autorização explícita.

**Auditabilidade e controle de acesso.** A trace completeness de 100% confirma que o
log hash-chained cobre o ciclo completo — do bloqueio pela política à confirmação de
execução MCP registrada com `actor=full_pipeline`. O teste de rejeição não-autorizada
(100% no adversarial) valida que `viewer` recebe **403 Forbidden** e usuários não
autenticados são redirecionados para `/login` (303), ambos rastreados no log de auditoria.

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

- guardrail effectiveness,
- latency impact,
- scenario robustness.

Recommended next additions for publication-grade depth:

- strict ablation toggles (`no OPA`, `no structured plan`, `no trace correlation`),
- multi-model sensitivity (same scenarios with different LLMs/prompts),
- automated test suite with pytest.
