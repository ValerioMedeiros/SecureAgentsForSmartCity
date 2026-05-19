from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

if __package__ in (None, ""):
    _PROJECT_ROOT = Path(__file__).resolve().parents[3]
    if str(_PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(_PROJECT_ROOT))

from src.smartcity.infra.audit import iter_entries, verify_chain

LOG_FILE = os.getenv("JSON_LOG_FILE", "logs/traces.jsonl")
AUDIT_FILE = os.getenv("AUDIT_LOG_FILE", "logs/audit.jsonl")


def _resolve_log_path(configured_path: str) -> str:
    direct = Path(configured_path)
    candidates = [
        direct,
        Path.cwd() / configured_path,
        Path(__file__).resolve().parents[4] / configured_path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return str(candidates[-1])


@st.cache_data(ttl=2)
def _load_logs(path: str) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    if not os.path.exists(path):
        return pd.DataFrame()

    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return pd.DataFrame(rows)


@st.cache_data(ttl=2)
def _load_audit(path: str) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = list(iter_entries(path))
    return pd.DataFrame(rows)


st.set_page_config(page_title="Smart City MAPE-K Trace Dashboard", layout="wide")
st.title("Smart City MAPE-K Trace Dashboard")

resolved_log_file = _resolve_log_path(LOG_FILE)
resolved_audit_file = _resolve_log_path(AUDIT_FILE)
frame = _load_logs(resolved_log_file)
audit_frame = _load_audit(resolved_audit_file)

with st.sidebar:
    st.header("Sources")
    st.caption(f"Logs: `{resolved_log_file}`")
    st.caption(f"Audit: `{resolved_audit_file}`")

    st.header("Audit chain")
    chain = verify_chain(resolved_audit_file)
    if chain["valid"]:
        st.success(f"OK — {chain['entries']} entries")
    else:
        st.error(f"Integrity issues — {len(chain['issues'])} found")
        with st.expander("Show issues"):
            st.json(chain["issues"])
    if chain.get("last_hash"):
        st.caption(f"last_hash: `{chain['last_hash'][:16]}…`")

if frame.empty and audit_frame.empty:
    st.warning(f"No logs found at: {resolved_log_file} or {resolved_audit_file}")
    st.caption("Run one scenario first: python -m src.smartcity.app.host_simulator")
    st.stop()

# Order trace IDs by latest timestamp (most recent first). Combine timestamps
# from both structured logs and audit entries so audit-only traces still show.
log_ts = pd.to_datetime(
    frame.get("timestamp", pd.Series(dtype="datetime64[ns]")), errors="coerce"
)
audit_ts = pd.to_datetime(
    audit_frame.get("timestamp", pd.Series(dtype="datetime64[ns]")), errors="coerce"
)
trace_order = (
    pd.concat(
        [
            pd.DataFrame({"traceId": frame.get("traceId"), "timestamp": log_ts}),
            pd.DataFrame(
                {"traceId": audit_frame.get("trace_id"), "timestamp": audit_ts}
            ),
        ]
    )
    .dropna(subset=["traceId"])
    .groupby("traceId", as_index=False)
    .agg({"timestamp": "max"})
    .sort_values("timestamp", ascending=False)
)
trace_options = trace_order["traceId"].tolist()
if not trace_options:
    trace_options = sorted(
        frame.get("traceId", pd.Series(dtype=str)).dropna().unique().tolist()
    )
selected_trace = st.selectbox("Trace ID", options=trace_options)
subset = frame[frame.get("traceId") == selected_trace].copy()
audit_subset = (
    audit_frame[audit_frame.get("trace_id") == selected_trace].copy()
    if not audit_frame.empty
    else pd.DataFrame()
)

tab_timeline, tab_audit, tab_latency, tab_raw = st.tabs(
    ["Trace timeline", "Audit trail", "Latency", "Raw"]
)

with tab_timeline:
    st.subheader("Stage timeline (structured logs)")
    if subset.empty:
        st.info("No structured-log rows for this trace.")
    else:
        cols = [
            c
            for c in [
                "timestamp",
                "component",
                "level",
                "message",
                "risk_level",
                "approval_mode",
                "reason",
                "duration_ms",
            ]
            if c in subset.columns
        ]
        st.dataframe(subset[cols], use_container_width=True)

        st.subheader("Stage summary")
        components = (
            subset.get("component", pd.Series(dtype=str))
            .value_counts()
            .reset_index()
        )
        components.columns = ["component", "events"]
        st.table(components)

with tab_audit:
    st.subheader("Audit trail (hash-chained)")
    if audit_subset.empty:
        st.info("No audit entries for this trace.")
    else:
        audit_view = audit_subset.copy()
        if "timestamp" in audit_view.columns:
            audit_view = audit_view.sort_values("timestamp")
        cols = [
            c
            for c in [
                "timestamp",
                "component",
                "event_type",
                "actor",
                "outcome",
                "plan_id",
                "id",
                "prev_hash",
                "hash",
            ]
            if c in audit_view.columns
        ]
        st.dataframe(audit_view[cols], use_container_width=True)

        st.subheader("Audit payloads")
        for record in audit_view.to_dict(orient="records"):
            label = f"{record.get('timestamp', '')} · {record.get('component', '')} · {record.get('event_type', '')}"
            with st.expander(label):
                st.json(record.get("payload", {}))
                st.caption(
                    f"id: {record.get('id', '')} · prev_hash: {str(record.get('prev_hash', ''))[:16]}… · hash: {str(record.get('hash', ''))[:16]}…"
                )

with tab_latency:
    st.subheader("Stage durations (ms)")
    rows: List[Dict[str, Any]] = []
    if not audit_subset.empty:
        for record in audit_subset.to_dict(orient="records"):
            payload = record.get("payload") or {}
            duration = payload.get("duration_ms") if isinstance(payload, dict) else None
            if duration is None:
                continue
            rows.append(
                {
                    "component": record.get("component"),
                    "event_type": record.get("event_type"),
                    "duration_ms": duration,
                }
            )
    if not subset.empty and "duration_ms" in subset.columns:
        for record in subset.to_dict(orient="records"):
            duration = record.get("duration_ms")
            if duration is None:
                continue
            rows.append(
                {
                    "component": record.get("component"),
                    "event_type": record.get("message"),
                    "duration_ms": duration,
                }
            )
    if not rows:
        st.info("No latency samples recorded for this trace.")
    else:
        latency_df = pd.DataFrame(rows)
        st.dataframe(latency_df, use_container_width=True)
        chart = latency_df.groupby("component", as_index=False)["duration_ms"].sum()
        st.bar_chart(chart, x="component", y="duration_ms")

with tab_raw:
    st.subheader("Raw structured-log records")
    st.json(subset.to_dict(orient="records"))
    st.subheader("Raw audit entries")
    st.json(audit_subset.to_dict(orient="records") if not audit_subset.empty else [])
