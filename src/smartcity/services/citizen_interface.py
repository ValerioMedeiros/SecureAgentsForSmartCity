"""Citizen-Facing Interface — notification and human approval UI.

Endpoints:
  GET  /                        — approval dashboard (HTML)
  GET  /runs                    — execution report history (HTML)
  POST /notify                  — receive agent notification
  POST /runs                    — agent posts structured execution report
  POST /approvals               — agent creates a pending approval request
  GET  /approvals/{id}          — agent polls for decision
  POST /approvals/{id}/decide   — operator submits s/n decision
  GET  /notifications           — list recent notifications (JSON)
  GET  /metrics                 — Prometheus metrics
"""

from __future__ import annotations

import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

from fastapi import FastAPI, Form, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from ..infra.audit import record_event
from ..infra.logging_utils import configure_logger
from ..infra.metrics import render_latest

logger = configure_logger("citizen_interface")
app = FastAPI(title="Citizen-Facing Interface")

_MAX_NOTIFICATIONS = 200
_MAX_RUNS = 50
_notifications: Deque[Dict[str, Any]] = deque(maxlen=_MAX_NOTIFICATIONS)
_pending_approvals: Dict[str, Dict[str, Any]] = {}
_runs: Deque[Dict[str, Any]] = deque(maxlen=_MAX_RUNS)


# ── Models ────────────────────────────────────────────────────────────

class NotifyRequest(BaseModel):
    message: str
    level: str = "info"
    trace_id: Optional[str] = None
    plan_id: Optional[str] = None


class ApprovalRequest(BaseModel):
    pump_id: str
    action: str
    risk_level: str
    reason: str
    trace_id: Optional[str] = None


class DecisionRequest(BaseModel):
    decision: str


class ObservationReport(BaseModel):
    station_id: str
    precipitation_mm: float
    humidity_pct: float
    pressure_hpa: float
    pump_id: Optional[str] = None


class ExecutionReport(BaseModel):
    trace_id: str
    timestamp: str
    source: str                             # llm_agent | rule_based
    observations: List[ObservationReport]
    forecast_risk: Optional[str] = None     # baixo | médio | alto | crítico
    forecast_precipitation_mm: Optional[float] = None
    risk_level: Optional[str] = None        # low | medium | high
    policy_decision: Optional[str] = None   # auto | human | deny
    approval_status: Optional[str] = None   # approved | denied | timeout | n/a
    action_taken: Optional[str] = None      # turnOnPump | turnOffPump | none
    pump_id: Optional[str] = None
    tool_calls: List[str] = []
    agent_summary: Optional[str] = None
    duration_ms: Optional[float] = None


# ── Shared CSS / layout ────────────────────────────────────────────────

_CSS = """
  body{font-family:sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:24px}
  h1{color:#38bdf8;margin-bottom:4px}
  h2{color:#94a3b8;font-size:1rem;font-weight:normal;margin-bottom:20px}
  nav{margin-bottom:28px}
  nav a{color:#7dd3fc;text-decoration:none;margin-right:20px;font-size:.95rem}
  nav a:hover{text-decoration:underline}
  nav a.active{color:#f0abfc;font-weight:bold}
  .section{margin-bottom:32px}
  .section h3{color:#7dd3fc;border-bottom:1px solid #1e3a5f;padding-bottom:6px;margin-bottom:14px}
  .card{background:#1e293b;border-radius:8px;padding:16px;margin-bottom:12px;border-left:4px solid #334155}
  .card.high,.card.alert{border-left-color:#ef4444}
  .card.medium,.card.warning{border-left-color:#f59e0b}
  .card.low,.card.info{border-left-color:#22c55e}
  .card.activated{border-left-color:#38bdf8}
  .card.denied,.card.timeout{border-left-color:#dc2626}
  .card.no-action{border-left-color:#475569}
  .meta{font-size:.75rem;color:#64748b;margin-bottom:8px}
  .msg{font-size:.95rem;margin-bottom:10px}
  .badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:.75rem;font-weight:bold;margin-right:4px}
  .badge-high,.badge-alert{background:#7f1d1d;color:#fca5a5}
  .badge-medium,.badge-warning{background:#78350f;color:#fcd34d}
  .badge-low,.badge-info{background:#14532d;color:#86efac}
  .badge-activated,.badge-approved,.badge-auto{background:#0c4a6e;color:#7dd3fc}
  .badge-denied,.badge-timeout,.badge-deny{background:#450a0a;color:#fca5a5}
  .badge-human{background:#4a1d96;color:#d8b4fe}
  .badge-no-action{background:#1e293b;color:#64748b}
  .badge-rule-based{background:#1c1917;color:#a8a29e}
  .kv{display:flex;flex-wrap:wrap;gap:8px 20px;margin-bottom:10px}
  .kv span{font-size:.82rem;color:#94a3b8}.kv span b{color:#e2e8f0}
  .chain{display:flex;flex-wrap:wrap;align-items:center;gap:4px;margin:8px 0}
  .step{background:#0f172a;border:1px solid #334155;border-radius:4px;padding:2px 8px;font-size:.78rem;color:#94a3b8}
  .step.key{border-color:#7dd3fc;color:#7dd3fc}
  .arrow{color:#475569;font-size:.8rem}
  .summary{font-size:.85rem;color:#94a3b8;margin-top:10px;border-top:1px solid #1e3a5f;padding-top:8px;line-height:1.5}
  .actions{display:flex;gap:10px}
  form{margin:0}
  button{padding:8px 20px;border:none;border-radius:6px;font-size:.9rem;font-weight:bold;cursor:pointer}
  .btn-approve{background:#16a34a;color:white}.btn-approve:hover{background:#15803d}
  .btn-deny{background:#dc2626;color:white}.btn-deny:hover{background:#b91c1c}
  .pump{font-family:monospace;color:#f0abfc}
  .empty{color:#475569;font-style:italic}
"""

_BASE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">{refresh}
  <title>Smart City — {title}</title>
  <style>{css}</style>
</head>
<body>
  <h1>🏙️ Smart City — Painel do Operador</h1>
  <h2>{subtitle}</h2>
  <nav>
    <a href="/" class="{home_active}">⏳ Aprovações</a>
    <a href="/runs" class="{runs_active}">📋 Execuções</a>
  </nav>
  {body}
</body>
</html>"""


def _page(body: str, title: str, subtitle: str, active: str, refresh: int = 0) -> str:
    refresh_tag = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return _BASE.format(
        refresh=refresh_tag, title=title, subtitle=subtitle, css=_CSS, body=body,
        home_active="active" if active == "home" else "",
        runs_active="active" if active == "runs" else "",
    )


# ── Dashboard (approvals) ──────────────────────────────────────────────

_PENDING_CARD = """
<div class="card {risk_level}">
  <div class="meta">{timestamp} &nbsp;|&nbsp; trace: <code>{trace_short}</code></div>
  <div class="msg">
    <span class="badge badge-{risk_level}">{risk_level_upper}</span>
    Ação: <strong>{action}</strong> &nbsp;|&nbsp; Bomba: <span class="pump">{pump_id}</span>
    <br><small style="color:#94a3b8">{reason}</small>
  </div>
  <div class="actions">
    <form method="post" action="/approvals/{id}/decide">
      <input type="hidden" name="decision" value="s">
      <button type="submit" class="btn-approve">✓ Aprovar (S)</button>
    </form>
    <form method="post" action="/approvals/{id}/decide">
      <input type="hidden" name="decision" value="n">
      <button type="submit" class="btn-deny">✗ Negar (N)</button>
    </form>
  </div>
</div>"""

_NOTIF_CARD = """
<div class="card {level}">
  <div class="meta">{timestamp}</div>
  <div class="msg"><span class="badge badge-{level}">{level_upper}</span> {message}</div>
</div>"""


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    pending = [v for v in _pending_approvals.values() if v["status"] == "pending"]
    pending.sort(key=lambda x: x["created_at"], reverse=True)

    if pending:
        pending_html = "".join(
            _PENDING_CARD.format(
                id=a["id"],
                risk_level=a["risk_level"],
                risk_level_upper=a["risk_level"].upper(),
                action=a["action"],
                pump_id=a["pump_id"],
                reason=a["reason"],
                timestamp=a["created_at"][:19].replace("T", " "),
                trace_short=(a.get("trace_id") or "")[:8],
            )
            for a in pending
        )
    else:
        pending_html = '<p class="empty">Nenhuma aprovação pendente.</p>'

    notifs = list(_notifications)[:10]
    notifs_html = "".join(
        _NOTIF_CARD.format(
            level=n["level"], level_upper=n["level"].upper(),
            message=n["message"], timestamp=n["timestamp"][:19].replace("T", " "),
        )
        for n in notifs
    ) or '<p class="empty">Sem notificações.</p>'

    body = f"""
    <div class="section">
      <h3>⏳ Aprovações Pendentes ({len(pending)})</h3>
      {pending_html}
    </div>
    <div class="section">
      <h3>🔔 Últimas Notificações</h3>
      {notifs_html}
    </div>"""
    return _page(body, "Aprovações", "Atualização automática a cada 5 segundos",
                 active="home", refresh=5)


# ── Runs page ──────────────────────────────────────────────────────────

_KEY_TOOLS = {"get_weather_forecast", "check_policy", "request_human_approval",
              "activate_pump", "deactivate_pump"}

_OUTCOME_LABELS = {
    "pump_activated":   ("activated", "✅ Bomba acionada"),
    "pump_deactivated": ("activated", "✅ Bomba desligada"),
    "approval_denied":  ("denied",    "🚫 Negado pelo operador"),
    "approval_timeout": ("timeout",   "⏱️ Timeout — sem resposta"),
    "no_action":        ("no-action", "ℹ️ Sem ação necessária"),
    "rule_based":       ("no-action", "⚙️ Modo regras (sem LLM)"),
}


def _run_card(r: Dict[str, Any]) -> str:
    outcome_key = r.get("outcome", "no_action")
    card_cls, outcome_label = _OUTCOME_LABELS.get(outcome_key, ("no-action", outcome_key))

    # observations summary
    obs_parts = []
    for o in r.get("observations", []):
        obs_parts.append(
            f"<b>{o['station_id']}</b>: "
            f"{o['precipitation_mm']}mm chuva · "
            f"{o['humidity_pct']}% umidade · "
            f"{o['pressure_hpa']}hPa"
        )
    obs_html = " &nbsp;|&nbsp; ".join(obs_parts) or "—"

    # forecast
    fr = r.get("forecast_risk") or "—"
    fp = r.get("forecast_precipitation_mm")
    forecast_html = (
        f'<span class="badge badge-{fr}">{fr.upper()}</span> '
        f'{fp}mm previstos (6h)' if fp is not None else "—"
    )

    # badges row
    risk = r.get("risk_level", "—")
    policy = r.get("policy_decision", "—")
    approval = r.get("approval_status", "—")
    pump_id = r.get("pump_id") or "—"
    action = r.get("action_taken") or "—"

    # tool chain
    steps_html = ""
    calls = r.get("tool_calls", [])
    for i, t in enumerate(calls):
        cls = "key" if t in _KEY_TOOLS else ""
        steps_html += f'<span class="step {cls}">{t}</span>'
        if i < len(calls) - 1:
            steps_html += '<span class="arrow">→</span>'

    # summary
    summary = r.get("agent_summary") or ""
    summary_html = (
        f'<div class="summary"><strong>Raciocínio do agente:</strong><br>{summary[:600]}'
        + ("…" if len(summary) > 600 else "")
        + "</div>"
    ) if summary else ""

    duration = r.get("duration_ms")
    duration_str = f"{duration/1000:.1f}s" if duration else "—"

    return f"""
<div class="card {card_cls}">
  <div class="meta">
    {r['timestamp'][:19].replace('T',' ')} &nbsp;|&nbsp;
    trace: <code>{r['trace_id'][:8]}</code> &nbsp;|&nbsp;
    {duration_str} &nbsp;|&nbsp;
    <span class="badge badge-{'activated' if r.get('source')=='llm_agent' else 'rule-based'}">{r.get('source','?')}</span>
  </div>

  <div class="msg"><strong>{outcome_label}</strong></div>

  <div class="kv">
    <span>📡 Estação: <b>{obs_html}</b></span>
  </div>
  <div class="kv">
    <span>🌧️ Previsão: <b>{forecast_html}</b></span>
    <span>⚠️ Risco avaliado: <b><span class="badge badge-{risk}">{risk.upper()}</span></b></span>
  </div>
  <div class="kv">
    <span>🔒 Política: <b><span class="badge badge-{policy}">{policy.upper()}</span></b></span>
    <span>👤 Aprovação: <b><span class="badge badge-{approval}">{approval.upper()}</span></b></span>
    <span>🔧 Ação: <b>{action}</b> em <span class="pump">{pump_id}</span></b></span>
  </div>

  <div class="chain">{steps_html}</div>
  {summary_html}
</div>"""


@app.get("/runs", response_class=HTMLResponse)
async def runs_page() -> str:
    items = list(_runs)
    if items:
        cards = "".join(_run_card(r) for r in items)
    else:
        cards = '<p class="empty">Nenhuma execução registrada ainda.</p>'

    body = f'<div class="section"><h3>📋 Histórico de Execuções ({len(items)})</h3>{cards}</div>'
    return _page(body, "Execuções", "Atualização automática a cada 10 segundos",
                 active="runs", refresh=10)


@app.post("/runs")
async def receive_run(report: ExecutionReport) -> Dict[str, Any]:
    """Agent posts a structured execution report at the end of each run."""
    record = report.model_dump()
    _runs.appendleft(record)
    logger.info("Execution report received [%s] outcome=%s",
                report.trace_id[:8], report.outcome or "?",
                extra={"traceId": report.trace_id})
    return {"status": "recorded"}


# ── Notify ─────────────────────────────────────────────────────────────

@app.post("/notify")
async def notify(req: NotifyRequest) -> Dict[str, Any]:
    notification = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": req.level,
        "message": req.message,
        "trace_id": req.trace_id,
        "plan_id": req.plan_id,
    }
    _notifications.appendleft(notification)

    log_fn = logger.warning if req.level == "warning" else (
        logger.error if req.level == "alert" else logger.info
    )
    log_fn("Citizen notification [%s]: %s", req.level.upper(), req.message,
           extra={"traceId": req.trace_id or ""})

    record_event(
        component="citizen_interface",
        event_type="NOTIFICATION_RECEIVED",
        trace_id=req.trace_id or str(uuid.uuid4()),
        plan_id=req.plan_id,
        actor="weather_agent",
        outcome=req.level,
        payload={"message": req.message, "level": req.level},
    )
    return {"status": "received", "id": notification["id"]}


# ── Approvals ──────────────────────────────────────────────────────────

@app.post("/approvals")
async def create_approval(req: ApprovalRequest) -> Dict[str, Any]:
    approval_id = str(uuid.uuid4())
    record = {
        "id": approval_id,
        "pump_id": req.pump_id,
        "action": req.action,
        "risk_level": req.risk_level,
        "reason": req.reason,
        "trace_id": req.trace_id,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "decided_at": None,
    }
    _pending_approvals[approval_id] = record

    logger.warning("Approval requested — waiting for operator [%s]", approval_id[:8],
                   extra={"traceId": req.trace_id or ""})
    record_event(
        component="citizen_interface",
        event_type="APPROVAL_REQUESTED",
        trace_id=req.trace_id or approval_id,
        actor="weather_agent",
        outcome="pending",
        payload={"pump_id": req.pump_id, "action": req.action, "risk_level": req.risk_level},
    )
    return {"approval_id": approval_id, "status": "pending"}


@app.get("/approvals/{approval_id}")
async def get_approval(approval_id: str) -> Dict[str, Any]:
    record = _pending_approvals.get(approval_id)
    if not record:
        raise HTTPException(status_code=404, detail="Approval not found")
    return {"approval_id": approval_id, "status": record["status"]}


@app.post("/approvals/{approval_id}/decide")
async def decide_approval(approval_id: str, decision: str = Form(default="n")) -> Any:
    record = _pending_approvals.get(approval_id)
    if not record:
        raise HTTPException(status_code=404, detail="Approval not found")
    if record["status"] != "pending":
        raise HTTPException(status_code=409, detail="Already decided")

    record["status"] = "approved" if decision.lower() == "s" else "denied"
    record["decided_at"] = datetime.now(timezone.utc).isoformat()

    logger.info("Approval %s by operator: %s", record["status"], approval_id[:8],
                extra={"traceId": record.get("trace_id") or ""})
    record_event(
        component="citizen_interface",
        event_type="APPROVAL_DECIDED",
        trace_id=record.get("trace_id") or approval_id,
        actor="human_operator",
        outcome=record["status"],
        payload={"pump_id": record["pump_id"], "action": record["action"]},
    )
    return RedirectResponse(url="/", status_code=303)


# ── Other ──────────────────────────────────────────────────────────────

@app.get("/notifications")
async def list_notifications(limit: int = 50) -> Dict[str, Any]:
    return {"count": len(_notifications), "notifications": list(_notifications)[:limit]}


@app.get("/metrics")
def metrics() -> Response:
    body, content_type = render_latest()
    return Response(content=body, media_type=content_type)
