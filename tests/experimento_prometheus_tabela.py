#!/usr/bin/env python3
"""Coleta métricas Prometheus e dados do log de auditoria para gerar a tabela
de resultados do artigo.

Pré-requisitos:
    docker compose up -d
    python -m src.smartcity.app.init_pumps
    python -m src.smartcity.app.init_weather_station

Uso:
    uv run python tests/experimento_prometheus_tabela.py [--runs N] [--with-opa] [--full]

    # Experimento completo do artigo (100 iterações por cenário, ~1.5-2 h):
    uv run python tests/experimento_prometheus_tabela.py --full --runs 100

Opções:
    --runs N      Iterações por cenário (padrão: 10)
    --with-opa    Habilita OPA se disponível em OPA_URL (padrão: desabilitado)
    --full        Pipeline completo: OPA + Keycloak + aprovação pump_operator + MCP real
                  Requer: docker compose up -d opa keycloak citizen-interface pump-mcp

Saída:
    paper/tabela_resultados.md  — tabela Markdown pronta para o artigo
    paper/metricas_raw.json     — dados brutos para o apêndice
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import dotenv_values, load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

ENV_FILE = ROOT_DIR / ".env"
PAPER_DIR = ROOT_DIR / "paper"

_OPA_DEFAULT = "http://localhost:8181"
_CITIZEN_URL = os.getenv("CITIZEN_INTERFACE_URL", "http://localhost:8020")
_KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://localhost:8090")
_KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "smartcity")
_KEYCLOAK_CLIENT = os.getenv("KEYCLOAK_CLIENT_ID", "smartcity-client")
_PUMP_MCP_URL = os.getenv("PUMP_MCP_URL", "http://localhost:8002")


def _load_env() -> None:
    if not ENV_FILE.exists():
        return
    load_dotenv(ENV_FILE, override=False)
    for key, value in dotenv_values(ENV_FILE).items():
        if value is None:
            continue
        if not os.getenv(key, "").strip():
            os.environ[key] = value


# ── Prometheus helpers ────────────────────────────────────────────────

def _snapshot() -> str:
    """Captura o estado atual de todos os contadores/histogramas do processo."""
    from prometheus_client import generate_latest
    from smartcity.infra.metrics import REGISTRY
    return generate_latest(REGISTRY).decode("utf-8")


def _parse_prom(text: str) -> Dict[Tuple[str, frozenset], float]:
    """Converte texto Prometheus em {(nome_métrica, frozenset(labels)): valor}."""
    result: Dict[Tuple[str, frozenset], float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Suporta: metric_name{labels} value [timestamp]
        m = re.match(r"^([^\s{]+)(\{[^}]*\})?\s+([\S]+)(?:\s+\d+)?$", line)
        if not m:
            continue
        metric_name = m.group(1)
        label_str = (m.group(2) or "{}")[1:-1]
        raw_val = m.group(3)
        try:
            value = float(raw_val)
        except ValueError:
            continue
        pairs = frozenset(re.findall(r'(\w+)="([^"]*)"', label_str))
        result[(metric_name, pairs)] = value
    return result


def _counter_delta(
    before: Dict,
    after: Dict,
    metric: str,
    label_filter: Optional[Dict[str, str]] = None,
) -> float:
    """Soma delta positivo de amostras que casam com label_filter."""
    total = 0.0
    for (name, labels), val in after.items():
        if name != metric:
            continue
        if label_filter:
            d = dict(labels)
            if not all(d.get(k) == v for k, v in label_filter.items()):
                continue
        delta = val - before.get((name, labels), 0.0)
        if delta > 0:
            total += delta
    return total


def _hist_pct_ms(
    before: Dict,
    after: Dict,
    metric: str,
    label_filter: Dict[str, str],
    p: float,
) -> Optional[float]:
    """Percentil p (0–1) em ms via delta de buckets de histograma."""
    bname = metric + "_bucket"
    buckets: List[Tuple[float, float]] = []
    for (name, labels), val in after.items():
        if name != bname:
            continue
        d = dict(labels)
        if not all(d.get(k) == v for k, v in label_filter.items()):
            continue
        le_str = d.get("le", "")
        if not le_str:
            continue
        le = float("inf") if le_str == "+Inf" else float(le_str)
        buckets.append((le, val - before.get((name, labels), 0.0)))

    if not buckets:
        return None
    buckets.sort(key=lambda x: x[0])
    total = buckets[-1][1]  # count at +Inf == total
    if total <= 0:
        return None

    target = total * p
    for i, (le, cnt) in enumerate(buckets):
        if cnt >= target:
            prev_le = 0.0 if i == 0 else buckets[i - 1][0]
            prev_cnt = 0.0 if i == 0 else buckets[i - 1][1]
            if le == float("inf"):
                s = prev_le
            elif cnt == prev_cnt:
                s = prev_le
            else:
                frac = (target - prev_cnt) / (cnt - prev_cnt)
                s = prev_le + frac * (le - prev_le)
            return round(s * 1000.0, 2)
    return None


def _percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    rank = (len(ordered) - 1) * p
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo), 2)


# ── Service detection ─────────────────────────────────────────────────

def _reachable(url: str, timeout: float = 2.0) -> bool:
    try:
        import requests
        requests.get(url, timeout=timeout)
        return True
    except Exception:
        return False


# ── Cenários controlados ──────────────────────────────────────────────

def _build_scenarios():
    """Retorna lista de (label, nome_interno, MonitorEvent)."""
    from smartcity.core.models import MonitorEvent, WeatherObserved

    return [
        (
            "A",
            "baseline-low-risk",
            MonitorEvent(
                event_type="weather-baseline",
                weather_observations=[
                    WeatherObserved(
                        station_id="WeatherStation:001",
                        precipitation=0.0,
                        humidity=61.0,
                        atmospheric_pressure=1014.0,
                        wind_speed=8.0,
                        rainfall_risk="baixo",
                        forecast_precipitation_mm=1.5,
                        forecast_hours=6,
                        location="Avenue 1",
                        notes="Condicoes estaveis para controle.",
                    )
                ],
            ),
        ),
        (
            "B",
            "forecast-high-risk",
            MonitorEvent(
                event_type="forecast-heavy-rain",
                weather_observations=[
                    WeatherObserved(
                        station_id="WeatherStation:002",
                        precipitation=12.0,
                        humidity=86.0,
                        atmospheric_pressure=1004.0,
                        wind_speed=19.0,
                        rainfall_risk="alto",
                        forecast_precipitation_mm=55.0,
                        forecast_hours=6,
                        pump_id="PumpDevice:001",
                        location="Avenue 2",
                        notes="Previsao indica ativacao preventiva.",
                    )
                ],
            ),
        ),
        (
            "adversarial",
            "current-flood-risk",
            MonitorEvent(
                event_type="flood-risk",
                weather_observations=[
                    WeatherObserved(
                        station_id="WeatherStation:003",
                        precipitation=68.0,
                        humidity=93.0,
                        atmospheric_pressure=998.0,
                        wind_speed=27.0,
                        rainfall_risk="alto",
                        forecast_precipitation_mm=72.0,
                        forecast_hours=3,
                        pump_id="PumpDevice:002",
                        location="Avenue 3",
                        notes="Risco atual de alagamento.",
                    )
                ],
            ),
        ),
    ]


# ── Execução dos cenários ─────────────────────────────────────────────

def _progress(done: int, total: int) -> None:
    """Feedback de progresso a cada 10 runs (lotes longos parecem travados sem isso)."""
    if done % 10 == 0 and done < total:
        print(f" {done}/{total}", end="", flush=True)


def _run_batch(event: Any, n: int) -> Tuple[List[str], List[float]]:
    """Executa n iterações da pipeline regra-base. Retorna (trace_ids, e2e_ms)."""
    from smartcity.core.executor import execute_candidate_plan
    from smartcity.core.planner import build_candidate_plan

    trace_ids: List[str] = []
    e2e_samples: List[float] = []

    for i in range(n):
        tid = str(uuid.uuid4())
        t0 = time.perf_counter()
        try:
            plan = build_candidate_plan(event, tid)
            execute_candidate_plan(plan)
        except Exception:
            pass
        e2e_samples.append((time.perf_counter() - t0) * 1000.0)
        trace_ids.append(tid)
        _progress(i + 1, n)

    return trace_ids, e2e_samples


# ── Pipeline completo (--full): helpers de aprovação humana ──────────

def _operator_login(citizen_url: str) -> Any:
    """Autentica como pump_operator na Citizen Interface. Retorna requests.Session."""
    import requests

    session = requests.Session()
    try:
        session.post(
            f"{citizen_url}/login",
            data={"username": "operator", "password": "operator123"},
            allow_redirects=True,
            timeout=10,
        )
    except Exception as exc:
        print(f"\n[aviso] Login como pump_operator falhou: {exc}")
    return session


def _create_ci_approval(
    citizen_url: str,
    pump_id: str,
    action: str,
    risk_level: str,
    reason: str,
    trace_id: str,
) -> Optional[str]:
    """Cria pedido de aprovação no Citizen Interface. Retorna approval_id ou None."""
    import requests

    try:
        r = requests.post(
            f"{citizen_url}/approvals",
            json={
                "pump_id": pump_id,
                "action": action,
                "risk_level": risk_level,
                "reason": reason,
                "trace_id": trace_id,
            },
            timeout=5,
        )
        if r.status_code == 200:
            return r.json().get("approval_id")
    except Exception:
        pass
    return None


def _approve_request(operator_session: Any, citizen_url: str, approval_id: str) -> None:
    """Aprova um pedido pendente como pump_operator."""
    try:
        operator_session.post(
            f"{citizen_url}/approvals/{approval_id}/decide",
            data={"decision": "s"},
            allow_redirects=False,
            timeout=5,
        )
    except Exception:
        pass


def _run_batch_full(
    event: Any,
    n: int,
    operator_session: Any,
    citizen_url: str,
) -> Tuple[List[str], List[float]]:
    """Pipeline completo: plan → OPA → bloqueio → aprovação pump_operator → MCP.

    Para cada run que resulta em approval_mode=human:
      1. Cria pedido de aprovação no Citizen Interface com trace_id do plano.
      2. Aprova imediatamente como pump_operator (RBAC validado pelo CI).
      3. Executa a ação de bomba via MCP client, registrando métricas.
    """
    from smartcity.core.executor import _invoke_step, execute_candidate_plan
    from smartcity.core.models import ApprovalMode
    from smartcity.core.planner import build_candidate_plan
    from smartcity.infra.audit import record_event
    from smartcity.infra.metrics import EXECUTIONS_TOTAL, MCP_CALLS_TOTAL, stage_timer

    trace_ids: List[str] = []
    e2e_samples: List[float] = []

    for i in range(n):
        tid = str(uuid.uuid4())
        t0 = time.perf_counter()
        try:
            plan = build_candidate_plan(event, tid)
            report = execute_candidate_plan(plan)

            if (
                not report.executed
                and report.policy.approval_mode == ApprovalMode.HUMAN
            ):
                mcp_ok = 0
                risk_val = (
                    report.policy.risk_level.value
                    if report.policy.risk_level
                    else "medium"
                )
                for step in plan.steps:
                    action = step.action.value
                    if action not in ("turnOnPump", "turnOffPump"):
                        continue
                    pump_id = (
                        step.params.get("pump_id")
                        or step.params.get("entity_id")
                        or "PumpDevice:001"
                    )
                    # a. Criar pedido no CI com trace_id rastreável
                    approval_id = _create_ci_approval(
                        citizen_url, pump_id, action, risk_val,
                        f"Experimento --full: aprovação automática {action} em {pump_id}",
                        tid,
                    )
                    if not approval_id:
                        continue
                    # b. Aprovar como pump_operator (RBAC enforced pelo CI)
                    _approve_request(operator_session, citizen_url, approval_id)
                    # c. Executar via MCP com instrumentação de métricas
                    with stage_timer("mcp_call_client", "executor"):
                        try:
                            _invoke_step(action, step.params)
                            MCP_CALLS_TOTAL.labels(
                                method=action, status="success"
                            ).inc()
                            mcp_ok += 1
                        except Exception:
                            MCP_CALLS_TOTAL.labels(
                                method=action, status="error"
                            ).inc()

                if mcp_ok > 0:
                    EXECUTIONS_TOTAL.labels(status="completed").inc()
                    record_event(
                        component="executor",
                        event_type="EXECUTION_COMPLETED",
                        trace_id=tid,
                        plan_id=plan.plan_id,
                        actor="full_pipeline",
                        outcome="completed",
                        payload={
                            "source": "full_pipeline",
                            "mcp_calls": mcp_ok,
                            "approved_by": "pump_operator",
                        },
                    )
        except Exception:
            pass
        e2e_samples.append((time.perf_counter() - t0) * 1000.0)
        trace_ids.append(tid)
        _progress(i + 1, n)

    return trace_ids, e2e_samples


# ── Métricas do log de auditoria ──────────────────────────────────────

def _plan_validity_rate(trace_ids: List[str]) -> float:
    """Fração de execuções que geraram um plano válido (evento PLAN_CREATED presente)."""
    if not trace_ids:
        return 0.0
    from smartcity.infra.audit import read_entries

    valid = sum(
        1
        for tid in trace_ids
        if any(e.get("event_type") == "PLAN_CREATED" for e in read_entries(trace_id=tid))
    )
    return round(valid / len(trace_ids), 4)


def _trace_completeness(trace_ids: List[str]) -> float:
    """Fração de trace_ids com todos os eventos esperados no log de auditoria."""
    if not trace_ids:
        return 0.0
    from smartcity.infra.audit import read_entries

    complete = 0
    for tid in trace_ids:
        entries = read_entries(trace_id=tid)
        event_types = {e.get("event_type") for e in entries}
        has_plan = "PLAN_CREATED" in event_types
        has_outcome = bool(event_types & {"EXECUTION_COMPLETED", "EXECUTION_BLOCKED"})
        if has_plan and has_outcome:
            complete += 1
    return round(complete / len(trace_ids), 4)


# ── Simulação de rejeição de aprovação não-autorizada ─────────────────

def _simulate_unauth_rejections(n_attempts: int = 6) -> Tuple[int, int]:
    """
    Tenta aprovar uma ação de bomba com credenciais não-autorizadas via
    Citizen Interface HTTP. Retorna (rejeições, total_tentativas).

    Tentativas:
      metade sem sessão (não autenticado) → espera-se redirecionamento 303
      metade com papel viewer (sem permissão de bomba) → espera-se 403
    """
    import requests

    if not _reachable(_CITIZEN_URL):
        return 0, 0

    # Cria uma solicitação de aprovação de ação de bomba
    try:
        r = requests.post(
            f"{_CITIZEN_URL}/approvals",
            json={
                "pump_id": "PumpDevice:001",
                "action": "turnOnPump",
                "risk_level": "high",
                "reason": "Teste adversarial: simulação de aprovação não-autorizada",
            },
            timeout=5,
        )
        if r.status_code != 200:
            return 0, 0
        approval_id = r.json().get("approval_id")
    except Exception:
        return 0, 0

    rejections = 0
    total = 0
    half = max(1, n_attempts // 2)

    # Tentativa 1: sem sessão (não autenticado)
    for _ in range(half):
        total += 1
        try:
            resp = requests.post(
                f"{_CITIZEN_URL}/approvals/{approval_id}/decide",
                data={"decision": "s"},
                allow_redirects=False,
                timeout=5,
            )
            # 303 → redireciona para login; 401/403 → explicitamente rejeitado
            if resp.status_code in (303, 401, 403):
                rejections += 1
        except Exception:
            total -= 1

    # Tentativa 2: papel viewer (autenticado mas sem permissão de bomba)
    if _reachable(_KEYCLOAK_URL):
        session = requests.Session()
        try:
            session.post(
                f"{_CITIZEN_URL}/login",
                data={"username": "viewer", "password": "viewer123"},
                allow_redirects=True,
                timeout=5,
            )
            for _ in range(half):
                total += 1
                try:
                    resp = session.post(
                        f"{_CITIZEN_URL}/approvals/{approval_id}/decide",
                        data={"decision": "s"},
                        allow_redirects=False,
                        timeout=5,
                    )
                    # 403 → autenticado mas papel insuficiente
                    if resp.status_code in (303, 401, 403):
                        rejections += 1
                except Exception:
                    total -= 1
        except Exception:
            pass

    return rejections, total


# ── Resultado por cenário ─────────────────────────────────────────────

@dataclass
class ScenarioResult:
    label: str
    scenario_name: str
    n_runs: int
    plan_validity_rate: Optional[float] = None
    opa_p50_ms: Optional[float] = None
    opa_p95_ms: Optional[float] = None
    opa_p99_ms: Optional[float] = None
    e2e_p50_ms: Optional[float] = None
    e2e_p95_ms: Optional[float] = None
    e2e_p99_ms: Optional[float] = None
    e2e_min_ms: Optional[float] = None
    e2e_mean_ms: Optional[float] = None
    e2e_max_ms: Optional[float] = None
    e2e_stdev_ms: Optional[float] = None
    mcp_per_plan: Optional[float] = None
    human_approval_rate: Optional[float] = None
    unauth_rejection_rate: Optional[float] = None
    trace_completeness: Optional[float] = None
    policy_block_rate: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)


# ── Formatação da tabela ──────────────────────────────────────────────

def _pct(v: Optional[float]) -> str:
    return f"{v * 100:.1f}%" if v is not None else "N/A"


def _lat(p50: Optional[float], p95: Optional[float], p99: Optional[float] = None) -> str:
    if p50 is None or p95 is None:
        return "N/A"
    if p99 is None:
        return f"{p50:.1f}/{p95:.1f} ms"
    return f"{p50:.1f}/{p95:.1f}/{p99:.1f} ms"


def _dist(
    vmin: Optional[float],
    mean: Optional[float],
    stdev: Optional[float],
    vmax: Optional[float],
) -> str:
    if vmin is None or mean is None or vmax is None:
        return "N/A"
    std = f"±{stdev:.1f}" if stdev is not None else ""
    return f"{vmin:.1f} / {mean:.1f}{std} / {vmax:.1f} ms"


def _num(v: Optional[float]) -> str:
    return f"{v:.2f}" if v is not None else "N/A"


_ROWS = [
    ("Plan validity rate",              lambda r: _pct(r.plan_validity_rate)),
    ("OPA decision latency p50/p95/p99",
     lambda r: _lat(r.opa_p50_ms, r.opa_p95_ms, r.opa_p99_ms)),
    ("End-to-end latency p50/p95/p99",
     lambda r: _lat(r.e2e_p50_ms, r.e2e_p95_ms, r.e2e_p99_ms)),
    ("End-to-end latency min/mean±std/max",
     lambda r: _dist(r.e2e_min_ms, r.e2e_mean_ms, r.e2e_stdev_ms, r.e2e_max_ms)),
    ("MCP calls per plan",              lambda r: _num(r.mcp_per_plan)),
    ("Human approval rate",             lambda r: _pct(r.human_approval_rate)),
    ("Unauthorized approval rejection", lambda r: _pct(r.unauth_rejection_rate)),
    ("Trace completeness",              lambda r: _pct(r.trace_completeness)),
    ("Policy block rate (OPA)",         lambda r: _pct(r.policy_block_rate)),
]


def _print_table(results: List[ScenarioResult]) -> None:
    headers = ["Métrica"] + [f"Cenário {r.label}" for r in results]
    col_w = 38
    val_w = 34

    def _row(cells: List[str]) -> str:
        parts = [f"{cells[0]:<{col_w}}"]
        parts += [f"{c:^{val_w}}" for c in cells[1:]]
        return " | ".join(parts)

    sep = "-+-".join(["-" * col_w] + ["-" * val_w] * (len(headers) - 1))
    print("\n" + _row(headers))
    print(sep)
    for label, fmt_fn in _ROWS:
        cells = [label] + [fmt_fn(r) for r in results]
        print(_row(cells))
    print()


def _markdown_table(results: List[ScenarioResult]) -> str:
    headers = ["Métrica"] + [f"Cenário {r.label}" for r in results]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for label, fmt_fn in _ROWS:
        cells = [label] + [fmt_fn(r) for r in results]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


# ── Main ──────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--runs", type=int, default=10,
        help="Iterações por cenário (padrão: 10)",
    )
    parser.add_argument(
        "--with-opa", action="store_true",
        help="Habilita OPA se disponível em OPA_URL",
    )
    parser.add_argument(
        "--full", action="store_true",
        help=(
            "Pipeline completo: OPA + Keycloak + aprovação pump_operator + MCP real. "
            "Requer: docker compose up -d opa keycloak citizen-interface pump-mcp"
        ),
    )
    args = parser.parse_args()

    _load_env()

    # --full implica --with-opa
    if args.full:
        args.with_opa = True

    # Detecção de OPA
    opa_url = os.getenv("OPA_URL", _OPA_DEFAULT).strip()
    use_opa = args.with_opa and bool(opa_url) and _reachable(opa_url)
    if not use_opa:
        os.environ["OPA_URL"] = ""
        if args.with_opa:
            print(f"[aviso] OPA não alcançável em {opa_url} — usando política de fallback.")
        else:
            print("[info]  OPA desabilitado. Use --with-opa para medir latência OPA real.")
    else:
        print(f"[info]  OPA detectado em {opa_url}.")

    # Verificação dos serviços para --full
    operator_session = None
    if args.full:
        full_services = {
            "OPA": opa_url,
            "Keycloak": _KEYCLOAK_URL,
            "Citizen Interface": _CITIZEN_URL,
            "Pump MCP": _PUMP_MCP_URL,
        }
        missing = [name for name, url in full_services.items() if not _reachable(url)]
        if missing:
            print(
                f"[aviso] --full desabilitado: {', '.join(missing)} "
                f"{'está' if len(missing) == 1 else 'estão'} indisponível(eis). "
                "Usando modo padrão."
            )
            args.full = False
        else:
            print(
                "[info]  Modo --full ativo: OPA, Keycloak, Citizen Interface e "
                "Pump MCP detectados."
            )
            operator_session = _operator_login(_CITIZEN_URL)

    print(f"[info]  Executando {args.runs} iterações por cenário.\n")

    results: List[ScenarioResult] = []
    raw_export: Dict[str, Any] = {}

    for label, scenario_name, event in _build_scenarios():
        print(f"  [{label}] {scenario_name}...", end="", flush=True)

        snap_before = _parse_prom(_snapshot())
        if args.full and operator_session is not None:
            # Sessão renovada por cenário: lotes longos (ex.: 100 runs) podem
            # exceder o tempo de vida da sessão Keycloak do operador.
            operator_session = _operator_login(_CITIZEN_URL)
            trace_ids, e2e_ms = _run_batch_full(
                event, args.runs, operator_session, _CITIZEN_URL
            )
        else:
            trace_ids, e2e_ms = _run_batch(event, args.runs)
        snap_after = _parse_prom(_snapshot())

        # Métricas do log de auditoria
        pvr = _plan_validity_rate(trace_ids)
        tc = _trace_completeness(trace_ids)

        # Deltas dos contadores Prometheus
        plans_delta = _counter_delta(snap_before, snap_after, "smartcity_plans_total")
        decisions_total = _counter_delta(
            snap_before, snap_after, "smartcity_policy_decisions_total"
        )
        decisions_human = _counter_delta(
            snap_before, snap_after, "smartcity_policy_decisions_total",
            {"approval_mode": "human"},
        )
        mcp_total = _counter_delta(
            snap_before, snap_after, "smartcity_mcp_calls_total"
        )
        exec_total = _counter_delta(
            snap_before, snap_after, "smartcity_executions_total"
        )
        exec_blocked = _counter_delta(
            snap_before, snap_after, "smartcity_executions_total",
            {"status": "blocked"},
        )

        # Percentis do histograma OPA (apenas se OPA ativo)
        opa_p50 = _hist_pct_ms(
            snap_before, snap_after,
            "smartcity_stage_duration_seconds",
            {"stage": "policy_opa", "component": "opa"},
            0.50,
        ) if use_opa else None
        opa_p95 = _hist_pct_ms(
            snap_before, snap_after,
            "smartcity_stage_duration_seconds",
            {"stage": "policy_opa", "component": "opa"},
            0.95,
        ) if use_opa else None
        opa_p99 = _hist_pct_ms(
            snap_before, snap_after,
            "smartcity_stage_duration_seconds",
            {"stage": "policy_opa", "component": "opa"},
            0.99,
        ) if use_opa else None

        # Estatísticas de latência ponta-a-ponta (amostras in-process)
        e2e_p50 = _percentile(e2e_ms, 0.50)
        e2e_p95 = _percentile(e2e_ms, 0.95)
        e2e_p99 = _percentile(e2e_ms, 0.99)
        e2e_min = round(min(e2e_ms), 2) if e2e_ms else None
        e2e_mean = round(statistics.mean(e2e_ms), 2) if e2e_ms else None
        e2e_max = round(max(e2e_ms), 2) if e2e_ms else None
        e2e_stdev = round(statistics.stdev(e2e_ms), 2) if len(e2e_ms) > 1 else None

        # Taxas derivadas
        mcp_per_plan = round(mcp_total / plans_delta, 2) if plans_delta > 0 else None
        human_rate = (
            round(decisions_human / decisions_total, 4) if decisions_total > 0 else None
        )
        block_rate = (
            round(exec_blocked / exec_total, 4) if exec_total > 0 else None
        )

        # Rejeição de acesso não-autorizado (apenas cenário adversarial)
        if label == "adversarial":
            unauth_rej, unauth_total = _simulate_unauth_rejections()
            unauth_rate = (
                round(unauth_rej / unauth_total, 4) if unauth_total > 0 else None
            )
        else:
            unauth_rate = None

        result = ScenarioResult(
            label=label,
            scenario_name=scenario_name,
            n_runs=args.runs,
            plan_validity_rate=pvr,
            opa_p50_ms=opa_p50,
            opa_p95_ms=opa_p95,
            opa_p99_ms=opa_p99,
            e2e_p50_ms=e2e_p50,
            e2e_p95_ms=e2e_p95,
            e2e_p99_ms=e2e_p99,
            e2e_min_ms=e2e_min,
            e2e_mean_ms=e2e_mean,
            e2e_max_ms=e2e_max,
            e2e_stdev_ms=e2e_stdev,
            mcp_per_plan=mcp_per_plan,
            human_approval_rate=human_rate,
            unauth_rejection_rate=unauth_rate,
            trace_completeness=tc,
            policy_block_rate=block_rate,
            raw={
                "trace_ids": trace_ids,
                "e2e_ms_samples": [round(v, 2) for v in e2e_ms],
                "e2e_stats": {
                    "min": e2e_min,
                    "mean": e2e_mean,
                    "max": e2e_max,
                    "stdev": e2e_stdev,
                    "p50": e2e_p50,
                    "p95": e2e_p95,
                    "p99": e2e_p99,
                },
                "opa_stats": {
                    "p50": opa_p50,
                    "p95": opa_p95,
                    "p99": opa_p99,
                },
                "counter_deltas": {
                    "plans": plans_delta,
                    "decisions_total": decisions_total,
                    "decisions_human": decisions_human,
                    "mcp_calls": mcp_total,
                    "executions_total": exec_total,
                    "executions_blocked": exec_blocked,
                },
            },
        )
        results.append(result)
        raw_export[label] = result.raw

        print(
            f" ok  (e2e mean={e2e_mean:.0f} ms, p50={e2e_p50:.0f} ms,"
            f" p95={e2e_p95:.0f} ms, planos={plans_delta:.0f})"
        )

    _print_table(results)

    # ── Persistência ──────────────────────────────────────────────────
    PAPER_DIR.mkdir(parents=True, exist_ok=True)

    md_path = PAPER_DIR / "tabela_resultados.md"
    md_path.write_text(_markdown_table(results), encoding="utf-8")
    print(f"[ok] Markdown salvo em {md_path.relative_to(ROOT_DIR)}")

    json_path = PAPER_DIR / "metricas_raw.json"
    json_path.write_text(
        json.dumps(raw_export, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[ok] JSON bruto salvo em {json_path.relative_to(ROOT_DIR)}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
