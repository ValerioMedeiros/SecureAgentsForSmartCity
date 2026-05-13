# Implementation Notes (Roadmap Execution)

This document records the implementation changes made to support an explainable and research-ready PoC for secure self-adaptation in smart-city traffic scenarios.

## 1) Architecture Refactor (MAPE-K)

### Added modules
- `plan_schema.py`
  - Formal Pydantic schema for candidate plans.
  - Enumerations for action types, risk levels, and approval modes.
  - Validation rules for required parameters per action.
  - Execution report schema and policy decision schema.
- `planner.py`
  - Deterministic planner that generates structured candidate plans from monitored events.
  - Optional hook for LLM-generated JSON plans via environment variable.
  - Malformed plan fixture for guardrail experiments.
- `executor.py`
  - Executes plans only after policy decision.
  - Emits detailed execution reports with per-step status.
- `monitor.py`
  - FastAPI service receiving NGSI notifications.
  - Converts notifications into normalized events and triggers planner + executor.
  - Supports default subscription registration.
- `dashboard.py`
  - Streamlit dashboard that reconstructs trace timelines from JSON logs.
- `experiments.py`
  - Runs guardrail, latency, and scenario robustness experiments.

### Updated modules
- `host_simulator.py`
  - Replaced legacy mixed flow with modular orchestration (`MonitorEvent -> Planner -> Policy -> Executor`).
  - Supports scenarios A/B/C with end-to-end trace IDs.
- `policy_engine.py`
  - Added optional OPA integration (`OPA_URL` + policy path).
  - Added deterministic fallback policy when OPA is unavailable.
  - Policy outputs now include `risk_level`, `approval_mode`, `verdict_color`, `reason`, and `source`.
- `ngsi_client.py`
  - Added NGSI subscription helper methods (`create_subscription`, `list_subscriptions`).
- `logging_utils.py`
  - Added JSON file output sink (`JSON_LOG_FILE`) while preserving stdout logs.
- `docker-compose.yml`
  - Added OPA service with mounted Rego policies.
- `requirements.txt`
  - Added `streamlit` and `pandas` for dashboard and analysis.

## 2) Policy as Code

### Added policy
- `policies/traffic_policy.rego`
  - Encodes low/medium/high risk handling:
    - low -> auto (green)
    - medium -> human (yellow; requires human token)
    - high -> deny (red)
  - Also handles invalid user token path.

## 3) Explainability and Auditability

### Traceability behavior
- Every plan and execution carries a `traceId`.
- Logs persist in JSONL format (default: `logs/traces.jsonl`).
- Dashboard can reconstruct full timeline for one trace:
  - monitored event
  - candidate plan
  - policy decision
  - MCP tool calls and responses

## 4) Experimental Support

### Implemented baseline experiments
- Guardrail effectiveness:
  - validates malformed plan rejection path.
- Latency impact:
  - captures end-to-end timing samples in milliseconds.
- Scenario robustness:
  - runs flood-only, ambulance-only, and combined scenarios.

## 5) Remaining recommended enhancements

- Add explicit ablation toggles (`no OPA`, `no structured plan`, `no trace correlation`) for strict comparative study.
- Add second LLM model path for sensitivity experiments (same prompts, different model).
- Add test suite (`pytest`) for schema validation, policy mapping, and executor behavior.

## 6) Runtime Overview

1. Monitor receives event data (or host simulator injects scenario event).
2. Planner builds a candidate JSON plan using strict schema.
3. Policy engine evaluates the plan (OPA or fallback policy).
4. Executor calls MCP methods only when policy allows.
5. Logger writes correlated JSON records with one `traceId`.
6. Dashboard displays event-to-actuation causality.
