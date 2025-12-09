# Secure Decision-Making with Auditable LLM Agents (Demo)

Minimal, reproducible demo for the article **"Secure Decision-Making with Auditable LLM Agents for Smart Cities via MCP and NGSI"**. It wires together an MCP server (FastAPI), FIWARE Orion (NGSI-v2), a simple policy engine that simulates OPA, and a host process that stands in for an LLM executing plans with audit-friendly trace IDs.

## Stack
- Python 3.11 (FastAPI + Uvicorn)
- FIWARE Orion Context Broker + MongoDB (docker-compose)
- HTTP NGSI-v2 client using `requests`
- Policy engine shim (`policy_engine.py`) to emulate OPA decisions
- Static IAM tokens: `USER_TOKEN`, `HUMAN_APPROVAL_TOKEN`
- JSON logging with `timestamp`, `level`, `traceId`, `component`

## Quickstart
1. **Install dependencies**
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. **Start Orion + MongoDB**
   ```bash
   docker-compose up -d
   ```
3. **Set static tokens** (matches defaults used in code)
   ```bash
   export USER_TOKEN="user-token"
   export HUMAN_APPROVAL_TOKEN="human-approval-token"
   ```
4. **Run the MCP server** (HTTP/JSON-RPC style endpoint at `/mcp`)
   ```bash
   uvicorn mcp_server:app --host 0.0.0.0 --port 8000
   ```
5. **Create the initial TrafficSignal entity**
   ```bash
   python init_traffic_signal.py
   ```
6. **Run Scenario A (Ambulance Corridor, autonomy level 1 - automatic)**
   ```bash
   SCENARIO=A python host_simulator.py
   ```
7. **Run Scenario B (Heavy Rain / Critical Infrastructure, autonomy level 3 - supervised)**
   ```bash
   SCENARIO=B python host_simulator.py
   ```
8. **Inspect TrafficSignal state**
   ```bash
   python inspect_traffic_signal.py
   ```

## What the code demonstrates
- **Separation of reasoning vs execution**: `host_simulator.py` builds a JSON plan (reasoning) with `plan_id`, `goal`, `steps`, `approval`, and `telemetry.traceId`. Side effects occur only when MCP tools run the steps.
- **Guardrails via policy**: `policy_engine.py` simulates OPA; autonomy level 3 requires `HUMAN_APPROVAL_TOKEN`, while level 1/2 auto-approve with `USER_TOKEN`.
- **End-to-end traceability**: every component logs JSON with the same `traceId` so actions can be correlated across host, MCP server, and NGSI client.

## Repository layout
- `docker-compose.yml` – Orion + MongoDB
- `mcp_server.py` – FastAPI MCP endpoint exposing tools: `getTrafficSignalState`, `setPriorityCorridor`, `notifyTrafficAgents`
- `host_simulator.py` – LLM host that creates plans, calls the policy engine, and executes approved steps via MCP
- `policy_engine.py` – OPA emulator deciding per autonomy level
- `ngsi_client.py` – NGSI-v2 HTTP helpers for Orion
- `logging_utils.py` – JSON logger
- `init_traffic_signal.py` – seeds the `TrafficSignal`
- `inspect_traffic_signal.py` – reads the current `TrafficSignal`
- `requirements.txt` – Python dependencies

## Scenario details (matching the article)
- **Scenario A – Ambulance Corridor (autonomy 1 / automatic)**
  - Plan uses tools: `getTrafficSignalState` → `setPriorityCorridor` (to `emergency`) → `notifyTrafficAgents`.
  - No human token needed; policy auto-approves.
- **Scenario B – Heavy Rain / Critical Infrastructure (autonomy 3 / supervised)**
  - Plan uses tools: `getTrafficSignalState` → `setPriorityCorridor` (to `critical-infra`) → `notifyTrafficAgents`.
  - Requires `HUMAN_APPROVAL_TOKEN`; otherwise the policy rejects execution.

## Orion validation commands
You can validate changes directly against Orion (service `openiot`, service-path `/`):
```bash
# Read the TrafficSignal
curl -s "http://localhost:1026/v2/entities/TrafficSignal:001" \
  -H "Fiware-Service: openiot" \
  -H "Fiware-ServicePath: /" | jq

# Update priorityCorridor manually (shows what the MCP tool does)
curl -X PUT "http://localhost:1026/v2/entities/TrafficSignal:001/attrs/priorityCorridor/value" \
  -H "Fiware-Service: openiot" \
  -H "Fiware-ServicePath: /" \
  -H "Content-Type: text/plain" \
  -d "manual-test"
```

## How to reproduce the audit trail
1. Note the `traceId` printed by `host_simulator.py` when it builds the plan.
2. Search the logs emitted by `host`, `mcp_server`, and `ngsi_client` for that `traceId` to see reasoning, policy decision, MCP calls, and Orion responses.
3. The `traceId` also appears in failure cases (e.g., missing human token) to keep rejection paths auditable.

## Configuration
- `ORION_BASE_URL` (default `http://localhost:1026`)
- `ORION_FIWARE_SERVICE` (default `openiot`)
- `ORION_FIWARE_SERVICE_PATH` (default `/`)
- `MCP_SERVER_URL` (default `http://localhost:8000/mcp`)
- `TRAFFIC_SIGNAL_ID` (default `TrafficSignal:001`)
- `USER_TOKEN`, `HUMAN_APPROVAL_TOKEN` (static IAM simulation)

## Notes
- The policy engine clearly marks where a real OPA call would be placed.
- Notifications to traffic agents are simulated via logs for auditability.
- Logs are JSON-only to keep ingestion and correlation straightforward.
