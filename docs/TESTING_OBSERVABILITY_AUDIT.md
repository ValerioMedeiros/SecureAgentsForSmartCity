# Testing Observability and Audit

Reproducible runbook to validate the Prometheus instrumentation and the
hash-chained audit log introduced in the `observability` branch.

What this document covers:

1. Start the stack (infra + services).
2. Generate traffic to populate metrics and the audit log.
3. Validate observability (Prometheus `/metrics`).
4. Validate the audit log (`/audit/entries`, `/audit/verify`).
5. Prove the **tamper-evidence** property of the hash chain.
6. Check the Streamlit dashboard.
7. Sanity-test the audit module in isolation.

Prerequisites: `uv`, `docker`, `docker compose`, `curl`, `jq`.

---

## 1) Start the stack

### 1.1 Infrastructure (Orion + Mongo + OPA)

```bash
docker compose up -d
```

### 1.2 Environment variables

```bash
cp .env.example .env
```

Minimal adjustments in `.env`:

- `USER_TOKEN=user-token` (same value used by the executor).
- `OPA_URL=http://localhost:8181` if you want to exercise the OPA path
  (without it, the policy engine uses the deterministic fallback).
- `OPENAI_API_KEY=...` only if you intend to use the LLM planner. The tests
  below work with the deterministic planner.

### 1.3 Python dependencies

```bash
uv sync
```

### 1.4 Services (three terminals)

```bash
# T1 — MCP server (port 8000)
uv run uvicorn src.smartcity.services.mcp_server:app --host 0.0.0.0 --port 8000

# T2 — Monitor (port 8010)
uv run uvicorn src.smartcity.services.monitor:app --host 0.0.0.0 --port 8010

# T3 — Dashboard (http://localhost:8501)
uv run streamlit run src/smartcity/ui/dashboard.py
```

Quick check that the services are up:

```bash
curl -s http://localhost:8000/metrics | head -1
curl -s http://localhost:8010/metrics | head -1
```

---

## 2) Generate traffic

Two equivalent options. Choose **A** for quick HTTP tests; choose **B** to
reproduce the paper's scenarios.

### 2.1 Option A — NGSI directly to the monitor

Each call generates a `traceId` and runs through `monitor → plan → policy → execute → mcp`.

```bash
# Scenario A — ambulance (expected: risk=low, approval=auto, executed)
curl -s -X POST http://localhost:8010/monitor/notify \
  -H "Content-Type: application/json" \
  -d '{"data":[{"eventType":"ambulance","ambulanceDetected":true,"location":"corridor-A","crowd":"normal","weather":"normal"}]}'

# Scenario B — flood (expected: risk=medium, approval=human)
curl -s -X POST http://localhost:8010/monitor/notify \
  -H "Content-Type: application/json" \
  -d '{"data":[{"eventType":"flood","floodRisk":true,"weather":"storm","location":"zone-3","crowd":"normal"}]}'

# Scenario C — combined (expected: risk=high, approval=deny, NOT executed)
curl -s -X POST http://localhost:8010/monitor/notify \
  -H "Content-Type: application/json" \
  -d '{"data":[{"eventType":"combined","ambulanceDetected":true,"floodRisk":true,"weather":"storm","crowd":"high","location":"corridor-A"}]}'
```

Each response returns `traceId`, `planId`, `executed` and the `policy`. **Save
one `traceId`** — we will use it in the audit section.

### 2.2 Option B — scenario runner

```bash
SCENARIO=A uv run -m src.smartcity.app.host_simulator
SCENARIO=B uv run -m src.smartcity.app.host_simulator
SCENARIO=C uv run -m src.smartcity.app.host_simulator
```

---

## 3) Validate observability (Prometheus)

### 3.1 Check the exposed series

```bash
# Monitor (stages monitor/plan/policy/policy_opa/execute/mcp_call_client)
curl -s http://localhost:8010/metrics \
  | grep -E '^smartcity_(plans|policy_decisions|executions|errors|stage_duration)' \
  | head -40

# MCP server (stages mcp_call_server + auth/tool errors)
curl -s http://localhost:8000/metrics \
  | grep -E '^smartcity_(mcp_calls|errors|stage_duration)' \
  | head -40
```

Acceptance criteria:

- `smartcity_plans_total{scenario,risk_level,source}` increments for each scenario.
- `smartcity_policy_decisions_total{approval_mode,allowed}` separates
  `auto` / `human` / `deny`.
- `smartcity_executions_total{status}` shows `executed` in scenario A and
  something different (e.g. `blocked_by_policy`) in scenario C.
- `smartcity_mcp_calls_total{method,status}` records `200` for successful calls.
- `smartcity_stage_duration_seconds_bucket{stage=...}` covers the stages
  `monitor`, `plan`, `policy`, `policy_opa`, `execute`, `mcp_call_server`,
  `mcp_call_client`.

### 3.2 Error / 401 test on the MCP

It should increment `smartcity_errors_total{component="mcp_server",kind="unauthorized"}`
and `smartcity_mcp_calls_total{status="401"}`.

```bash
curl -s -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"method":"getTrafficSignalState","params":{"entity_id":"TrafficSignal:001"},"traceId":"bogus","token":"WRONG"}'

curl -s http://localhost:8000/metrics | grep -E 'mcp_calls_total.*status="401"|errors_total.*unauthorized'
```

---

## 4) Validate the audit log

### 4.1 List entries

```bash
# Last 20 entries
curl -s 'http://localhost:8010/audit/entries?limit=20' | jq

# Useful filters
curl -s 'http://localhost:8010/audit/entries?event_type=MCP_CALL&limit=10' | jq
curl -s 'http://localhost:8010/audit/entries?component=policy_engine' | jq
```

### 4.2 Reconstruct an end-to-end trace

Use a `traceId` returned by `/monitor/notify`:

```bash
TRACE=<returned-traceId>
curl -s "http://localhost:8010/audit/entries?trace_id=$TRACE" \
  | jq '.entries[] | {component,event_type,outcome,timestamp}'
```

Expected sequence for an approved scenario:

```
EVENT_RECEIVED  →  PLAN_CREATED  →  POLICY_DECISION
                →  EXECUTOR_VERDICT  →  MCP_CALL (×N)  →  LOOP_COMPLETED
```

### 4.3 Verify the hash chain

```bash
curl -s http://localhost:8010/audit/verify | jq
```

Expected output (intact chain):

```json
{
  "path": "logs/audit.jsonl",
  "valid": true,
  "entries": N,
  "issues": [],
  "last_hash": "…"
}
```

---

## 5) Tamper-evidence test (core property)

This is the test that demonstrates the core property of the audit log:
any mutation is detectable.

### 5.1 Editing an entry

```bash
# Backup
cp logs/audit.jsonl logs/audit.jsonl.bak

# Edit a line in the middle of the file (e.g. change a value inside "payload")
# using your preferred editor.

curl -s http://localhost:8010/audit/verify | jq
```

Expected:

- `valid: false`.
- `issues` contains `hash_mismatch` at the edited index.
- `issues` contains `broken_chain` in every subsequent entry
  (because `prev_hash` no longer matches).

Restore:

```bash
mv logs/audit.jsonl.bak logs/audit.jsonl
curl -s http://localhost:8010/audit/verify | jq   # valid: true
```

### 5.2 Other variations that prove additional properties

- **Reorder** two adjacent lines → `broken_chain`.
- **Delete** a line in the middle → `broken_chain` from the removed index on.
- **Truncate** the end of the file → `valid: true`, but with a smaller
  `entries`; combine with `/audit/entries` to detect an expected event missing
  (e.g. `LOOP_COMPLETED` absent for a known `trace_id`).

---

## 6) Dashboard

Open `http://localhost:8501`. For each `traceId`, the dashboard should show:

- per-stage timeline (latencies captured via `stage_timer`);
- policy verdict (`auto` / `human` / `deny`);
- associated MCP calls;
- hash-chain verification status for that trace.

---

## 7) Sanity-test the audit module in isolation

Useful if you want to prove tamper-evidence without starting the services:

```bash
uv run python - <<'PY'
import os
from src.smartcity.infra import audit

os.makedirs("logs", exist_ok=True)
path = "logs/_tmp_audit.jsonl"
open(path, "w").close()

audit.record_event("test", "A", trace_id="t1", payload={"x": 1}, path=path)
audit.record_event("test", "B", trace_id="t1", payload={"x": 2}, path=path)
print("verify ok ->", audit.verify_chain(path))

# Tamper: change the payload of the first entry
with open(path, "r+") as f:
    lines = f.readlines()
    lines[0] = lines[0].replace('"x":1', '"x":99')
    f.seek(0); f.writelines(lines); f.truncate()

print("verify tampered ->", audit.verify_chain(path))
PY
```

Expected:

- 1st call: `valid=True`.
- 2nd call: `valid=False`, with `hash_mismatch` at index 0 and
  `broken_chain` at index 1.

---

## What each test proves

| Test                                       | Property demonstrated                                       |
|--------------------------------------------|-------------------------------------------------------------|
| `/metrics` on 8010 and 8000                | Instrumentation of every MAPE-K stage (latency + count).    |
| 401 on the MCP                             | Errors propagate in `errors_total` and `mcp_calls_total{status="401"}`. |
| `/audit/entries?trace_id=…`                | End-to-end correlation via `trace_id`.                      |
| `/audit/verify` before/after editing       | Hash-chain tamper-evidence (SHA-256, prev-hash linked).     |
| Dashboard                                  | Visual trace reconstruction with chain verification.        |
| Isolated sanity test                       | Audit module correctly implements hash-chain and detection. |
