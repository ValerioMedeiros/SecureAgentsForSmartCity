"""Tamper-evident audit log for policy and execution events.

Each entry is appended as a single JSON line and carries a SHA-256 hash that
covers the entry payload plus the hash of the previous record. Truncating,
reordering, or mutating an entry breaks the chain and is detectable by
:func:`verify_chain`.

The shape is intentionally minimal so the same writer can be used from the
MAPE-K loop, the MCP server, and ad-hoc experiment scripts without coupling
them to a database.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

GENESIS_HASH = "0" * 64

_DEFAULT_PATH = os.getenv("AUDIT_LOG_FILE", "logs/audit.jsonl")
_LOCK = threading.Lock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve_path(path: Optional[str] = None) -> Path:
    target = Path(path or _DEFAULT_PATH)
    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    return target


@dataclass
class AuditEntry:
    """A single hash-chained audit record."""

    id: str
    timestamp: str
    trace_id: Optional[str]
    plan_id: Optional[str]
    component: str
    event_type: str
    actor: Optional[str]
    outcome: Optional[str]
    payload: Dict[str, Any] = field(default_factory=dict)
    prev_hash: str = GENESIS_HASH
    hash: str = ""

    def canonical_bytes(self) -> bytes:
        """Bytes used to compute :attr:`hash` — excludes the hash itself."""
        data = asdict(self)
        data.pop("hash", None)
        return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def compute_hash(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


def _last_hash(path: Path) -> str:
    if not path.exists() or path.stat().st_size == 0:
        return GENESIS_HASH
    last_line = ""
    with path.open("rb") as handle:
        # Read the file tail; audit files are small in PoC scale, so reading
        # the whole thing is fine and avoids fragile seek logic.
        for raw in handle:
            stripped = raw.strip()
            if stripped:
                last_line = stripped.decode("utf-8")
    if not last_line:
        return GENESIS_HASH
    try:
        return json.loads(last_line).get("hash", GENESIS_HASH)
    except json.JSONDecodeError:
        return GENESIS_HASH


def record_event(
    component: str,
    event_type: str,
    *,
    trace_id: Optional[str] = None,
    plan_id: Optional[str] = None,
    actor: Optional[str] = None,
    outcome: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    path: Optional[str] = None,
) -> AuditEntry:
    """Append a new audit entry and return it."""
    target = _resolve_path(path)
    with _LOCK:
        prev_hash = _last_hash(target)
        entry = AuditEntry(
            id=str(uuid.uuid4()),
            timestamp=_utc_now_iso(),
            trace_id=trace_id,
            plan_id=plan_id,
            component=component,
            event_type=event_type,
            actor=actor,
            outcome=outcome,
            payload=dict(payload or {}),
            prev_hash=prev_hash,
        )
        entry.hash = entry.compute_hash()
        with target.open("a", encoding="utf-8") as handle:
            handle.write(entry.to_json() + "\n")
    return entry


def read_entries(
    *,
    trace_id: Optional[str] = None,
    plan_id: Optional[str] = None,
    component: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: Optional[int] = None,
    path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    target = _resolve_path(path)
    if not target.exists():
        return []

    results: List[Dict[str, Any]] = []
    with target.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if trace_id and record.get("trace_id") != trace_id:
                continue
            if plan_id and record.get("plan_id") != plan_id:
                continue
            if component and record.get("component") != component:
                continue
            if event_type and record.get("event_type") != event_type:
                continue
            results.append(record)

    if limit is not None and limit > 0:
        results = results[-limit:]
    return results


def iter_entries(path: Optional[str] = None) -> Iterable[Dict[str, Any]]:
    target = _resolve_path(path)
    if not target.exists():
        return
    with target.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def verify_chain(path: Optional[str] = None) -> Dict[str, Any]:
    """Walk the audit log and report integrity findings."""
    target = _resolve_path(path)
    issues: List[Dict[str, Any]] = []
    expected_prev = GENESIS_HASH
    total = 0
    last_hash: Optional[str] = None

    if not target.exists():
        return {
            "path": str(target),
            "valid": True,
            "entries": 0,
            "issues": [],
            "last_hash": None,
        }

    with target.open("r", encoding="utf-8") as handle:
        for index, raw in enumerate(handle):
            line = raw.strip()
            if not line:
                continue
            total += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                issues.append({"index": index, "issue": f"invalid JSON: {exc}"})
                continue

            stored_hash = record.get("hash", "")
            entry = AuditEntry(
                id=record.get("id", ""),
                timestamp=record.get("timestamp", ""),
                trace_id=record.get("trace_id"),
                plan_id=record.get("plan_id"),
                component=record.get("component", ""),
                event_type=record.get("event_type", ""),
                actor=record.get("actor"),
                outcome=record.get("outcome"),
                payload=record.get("payload", {}) or {},
                prev_hash=record.get("prev_hash", GENESIS_HASH),
            )
            recomputed = entry.compute_hash()

            if entry.prev_hash != expected_prev:
                issues.append(
                    {
                        "index": index,
                        "id": record.get("id"),
                        "issue": "broken_chain",
                        "expected_prev": expected_prev,
                        "found_prev": entry.prev_hash,
                    }
                )
            if recomputed != stored_hash:
                issues.append(
                    {
                        "index": index,
                        "id": record.get("id"),
                        "issue": "hash_mismatch",
                        "expected_hash": recomputed,
                        "found_hash": stored_hash,
                    }
                )
            expected_prev = stored_hash
            last_hash = stored_hash

    return {
        "path": str(target),
        "valid": not issues,
        "entries": total,
        "issues": issues,
        "last_hash": last_hash,
    }
