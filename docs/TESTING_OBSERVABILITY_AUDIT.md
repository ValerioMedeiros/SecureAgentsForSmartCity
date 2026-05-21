# Testing Observability and Audit

Roteiro reprodutível para validar a instrumentação Prometheus e o audit log
hash-chained introduzidos no branch `observability`.

O que este documento cobre:

1. Subir a stack (infra + serviços).
2. Gerar tráfego para popular métricas e audit log.
3. Validar observabilidade (Prometheus `/metrics`).
4. Validar audit log (`/audit/entries`, `/audit/verify`).
5. Provar a propriedade de **tamper-evidence** da hash chain.
6. Verificar o dashboard Streamlit.
7. Sanity test do módulo de audit em isolamento.

Pré-requisitos: `uv`, `docker`, `docker compose`, `curl`, `jq`.

---

## 1) Subir a stack

### 1.1 Infraestrutura (Orion + Mongo + OPA)

```bash
docker compose up -d
```

### 1.2 Variáveis de ambiente

```bash
cp .env.example .env
```

Ajustes mínimos no `.env`:

- `USER_TOKEN=user-token` (mesmo valor usado pelo executor).
- `OPA_URL=http://localhost:8181` se quiser exercitar o caminho OPA
  (sem isso, o policy engine usa o fallback determinístico).
- `OPENAI_API_KEY=...` apenas se for usar o planner LLM. Os testes
  abaixo funcionam com o planner determinístico.

### 1.3 Dependências Python

```bash
uv sync
```

### 1.4 Serviços (três terminais)

```bash
# T1 — MCP server (porta 8000)
uv run uvicorn src.smartcity.services.mcp_server:app --host 0.0.0.0 --port 8000

# T2 — Monitor (porta 8010)
uv run uvicorn src.smartcity.services.monitor:app --host 0.0.0.0 --port 8010

# T3 — Dashboard (http://localhost:8501)
uv run streamlit run src/smartcity/ui/dashboard.py
```

Verificação rápida de que os serviços estão de pé:

```bash
curl -s http://localhost:8000/metrics | head -1
curl -s http://localhost:8010/metrics | head -1
```

---

## 2) Gerar tráfego

Duas opções equivalentes. Escolha **A** para testes rápidos via HTTP;
escolha **B** para reproduzir cenários do paper.

### 2.1 Opção A — NGSI direto no monitor

Cada chamada gera um `traceId` e percorre `monitor → plan → policy → execute → mcp`.

```bash
# Cenário A — ambulância (esperado: risk=low, approval=auto, executado)
curl -s -X POST http://localhost:8010/monitor/notify \
  -H "Content-Type: application/json" \
  -d '{"data":[{"eventType":"ambulance","ambulanceDetected":true,"location":"corridor-A","crowd":"normal","weather":"normal"}]}'

# Cenário B — alagamento (esperado: risk=medium, approval=human)
curl -s -X POST http://localhost:8010/monitor/notify \
  -H "Content-Type: application/json" \
  -d '{"data":[{"eventType":"flood","floodRisk":true,"weather":"storm","location":"zone-3","crowd":"normal"}]}'

# Cenário C — combinado (esperado: risk=high, approval=deny, NÃO executado)
curl -s -X POST http://localhost:8010/monitor/notify \
  -H "Content-Type: application/json" \
  -d '{"data":[{"eventType":"combined","ambulanceDetected":true,"floodRisk":true,"weather":"storm","crowd":"high","location":"corridor-A"}]}'
```

Cada resposta retorna `traceId`, `planId`, `executed` e a `policy`. **Guarde
um `traceId`** — vamos usá-lo na seção de audit.

### 2.2 Opção B — runner de cenários

```bash
SCENARIO=A uv run -m src.smartcity.app.host_simulator
SCENARIO=B uv run -m src.smartcity.app.host_simulator
SCENARIO=C uv run -m src.smartcity.app.host_simulator
```

---

## 3) Validar observabilidade (Prometheus)

### 3.1 Conferir as séries expostas

```bash
# Monitor (stages monitor/plan/policy/policy_opa/execute/mcp_call_client)
curl -s http://localhost:8010/metrics \
  | grep -E '^smartcity_(plans|policy_decisions|executions|errors|stage_duration)' \
  | head -40

# MCP server (stages mcp_call_server + erros de auth/tool)
curl -s http://localhost:8000/metrics \
  | grep -E '^smartcity_(mcp_calls|errors|stage_duration)' \
  | head -40
```

Critérios de aceite:

- `smartcity_plans_total{scenario,risk_level,source}` incrementa a cada cenário.
- `smartcity_policy_decisions_total{approval_mode,allowed}` separa
  `auto` / `human` / `deny`.
- `smartcity_executions_total{status}` mostra `executed` no cenário A e algo
  diferente (ex.: `blocked_by_policy`) no cenário C.
- `smartcity_mcp_calls_total{method,status}` registra `200` para chamadas
  bem-sucedidas.
- `smartcity_stage_duration_seconds_bucket{stage=...}` cobre os stages
  `monitor`, `plan`, `policy`, `policy_opa`, `execute`, `mcp_call_server`,
  `mcp_call_client`.

### 3.2 Teste de erro / 401 no MCP

Deve incrementar `smartcity_errors_total{component="mcp_server",kind="unauthorized"}`
e `smartcity_mcp_calls_total{status="401"}`.

```bash
curl -s -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"method":"getTrafficSignalState","params":{"entity_id":"TrafficSignal:001"},"traceId":"bogus","token":"WRONG"}'

curl -s http://localhost:8000/metrics | grep -E 'mcp_calls_total.*status="401"|errors_total.*unauthorized'
```

---

## 4) Validar audit log

### 4.1 Listar entradas

```bash
# Últimas 20 entradas
curl -s 'http://localhost:8010/audit/entries?limit=20' | jq

# Filtros úteis
curl -s 'http://localhost:8010/audit/entries?event_type=MCP_CALL&limit=10' | jq
curl -s 'http://localhost:8010/audit/entries?component=policy_engine' | jq
```

### 4.2 Reconstruir um trace fim-a-fim

Use um `traceId` retornado pelo `/monitor/notify`:

```bash
TRACE=<traceId-retornado>
curl -s "http://localhost:8010/audit/entries?trace_id=$TRACE" \
  | jq '.entries[] | {component,event_type,outcome,timestamp}'
```

Sequência esperada para um cenário aprovado:

```
EVENT_RECEIVED  →  PLAN_CREATED  →  POLICY_DECISION
                →  EXECUTOR_VERDICT  →  MCP_CALL (×N)  →  LOOP_COMPLETED
```

### 4.3 Verificar a hash chain

```bash
curl -s http://localhost:8010/audit/verify | jq
```

Saída esperada (chain íntegro):

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

## 5) Teste de tamper-evidence (propriedade central)

Este é o teste que demonstra a propriedade central do audit log:
qualquer mutação detectável.

### 5.1 Edição de uma entrada

```bash
# Backup
cp logs/audit.jsonl logs/audit.jsonl.bak

# Edite uma linha do meio do arquivo (ex.: troque um valor dentro de "payload")
# usando seu editor preferido.

curl -s http://localhost:8010/audit/verify | jq
```

Esperado:

- `valid: false`.
- `issues` contém `hash_mismatch` no índice editado.
- `issues` contém `broken_chain` em todas as entradas seguintes
  (porque `prev_hash` deixa de bater).

Restaurar:

```bash
mv logs/audit.jsonl.bak logs/audit.jsonl
curl -s http://localhost:8010/audit/verify | jq   # valid: true
```

### 5.2 Outras variações que comprovam propriedades adicionais

- **Reordenar** duas linhas adjacentes → `broken_chain`.
- **Apagar** uma linha do meio → `broken_chain` a partir do índice removido.
- **Truncar** o final do arquivo → `valid: true`, porém com `entries` menor;
  combine com `/audit/entries` para detectar um evento esperado faltando
  (ex.: `LOOP_COMPLETED` ausente para um `trace_id` conhecido).

---

## 6) Dashboard

Abra `http://localhost:8501`. Para cada `traceId`, o dashboard deve mostrar:

- linha do tempo por stage (latências capturadas via `stage_timer`);
- veredito da policy (`auto` / `human` / `deny`);
- chamadas MCP associadas;
- status da verificação da hash chain para aquele trace.

---

## 7) Sanity test do módulo de audit em isolamento

Útil se quiser provar o tamper-evidence sem subir os serviços:

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

# Tamper: altera o payload da primeira entrada
with open(path, "r+") as f:
    lines = f.readlines()
    lines[0] = lines[0].replace('"x":1', '"x":99')
    f.seek(0); f.writelines(lines); f.truncate()

print("verify tampered ->", audit.verify_chain(path))
PY
```

Esperado:

- 1ª chamada: `valid=True`.
- 2ª chamada: `valid=False`, com `hash_mismatch` no índice 0 e
  `broken_chain` no 1.

---

## Resumo do que cada teste prova

| Teste                                      | Propriedade demonstrada                                     |
|--------------------------------------------|-------------------------------------------------------------|
| `/metrics` em 8010 e 8000                  | Instrumentação de todos os stages do MAPE-K (latência + contagem). |
| 401 no MCP                                 | Erros propagam em `errors_total` e `mcp_calls_total{status="401"}`. |
| `/audit/entries?trace_id=…`                | Correlação ponta-a-ponta via `trace_id`.                    |
| `/audit/verify` antes/depois de editar     | Tamper-evidence da hash chain (SHA-256, prev-hash linked).  |
| Dashboard                                  | Reconstrução visual do trace com verificação do chain.      |
| Sanity test isolado                        | Audit module corretamente implementa hash-chain e detecção. |
