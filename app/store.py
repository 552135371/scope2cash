"""SQLite persistence for the single-user demo: invoices plus an append-only audit log of human approvals and
provider actions. Path comes from SCOPE2CASH_DB (default data/scope2cash.db)."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config
from .models import AgentAction, Invoice

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None
_conn_path = ""

SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
  id TEXT PRIMARY KEY, seq INTEGER, data TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, event TEXT NOT NULL, invoice_id TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS webhook_events (
  event_id TEXT PRIMARY KEY, ts TEXT NOT NULL, event_type TEXT, invoice_id TEXT, verified_by TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS agent_actions (
  id TEXT PRIMARY KEY, ts TEXT NOT NULL, data TEXT NOT NULL);
"""
MAX_INVOICES = 300  # the hosted demo keeps a rolling pipeline; oldest rows are pruned


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _db() -> sqlite3.Connection:
    global _conn, _conn_path
    path = config.get_settings().db_path
    if _conn is None or _conn_path != path:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(path, check_same_thread=False)
        _conn.executescript(SCHEMA)
        _conn_path = path
    return _conn


def save(inv: Invoice, seq: Optional[int] = None) -> Invoice:
    with _lock:
        db = _db()
        if seq is None:
            row = db.execute("SELECT seq FROM invoices WHERE id=?", (inv.id,)).fetchone()
            seq = row[0] if row else (db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM invoices").fetchone()[0])
        db.execute("INSERT OR REPLACE INTO invoices(id, seq, data, updated_at) VALUES (?,?,?,?)",
                   (inv.id, seq, inv.model_dump_json(), now()))
        db.execute("DELETE FROM invoices WHERE seq <= (SELECT MAX(seq) FROM invoices) - ?", (MAX_INVOICES,))
        db.commit()
    return inv


def get(inv_id: str) -> Optional[Invoice]:
    with _lock:
        row = _db().execute("SELECT data FROM invoices WHERE id=?", (inv_id,)).fetchone()
    return Invoice.model_validate_json(row[0]) if row else None


def all_invoices() -> List[Invoice]:
    with _lock:
        rows = _db().execute("SELECT data FROM invoices ORDER BY seq").fetchall()
    return [Invoice.model_validate_json(r[0]) for r in rows]


def next_seq() -> int:
    with _lock:
        return _db().execute("SELECT COALESCE(MAX(seq),0)+1 FROM invoices").fetchone()[0]


def delete_samples() -> None:
    with _lock:
        db = _db()
        db.execute("DELETE FROM invoices WHERE json_extract(data, '$.sample') = 1")
        db.commit()


def clear() -> None:
    with _lock:
        db = _db()
        db.execute("DELETE FROM invoices")
        db.commit()


def audit(event: str, invoice_id: str = "", **detail: Any) -> None:
    with _lock:
        db = _db()
        db.execute("INSERT INTO audit(ts, event, invoice_id, detail) VALUES (?,?,?,?)",
                   (now(), event, invoice_id, json.dumps(detail, default=str)))
        db.commit()


def audit_log(limit: int = 100) -> List[Dict[str, Any]]:
    with _lock:
        rows = _db().execute("SELECT ts, event, invoice_id, detail FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{"ts": r[0], "event": r[1], "invoice_id": r[2], "detail": json.loads(r[3] or "{}")} for r in rows]


# ---- PayPal webhook idempotency (PayPal delivers at least once and retries for up to 3 days)
def webhook_claim(event_id: str, event_type: str, invoice_id: str, verified_by: str) -> bool:
    """Record an event id; False if it was already processed (duplicate delivery)."""
    with _lock:
        db = _db()
        try:
            db.execute("INSERT INTO webhook_events(event_id, ts, event_type, invoice_id, verified_by, result) VALUES (?,?,?,?,?,?)",
                       (event_id, now(), event_type, invoice_id, verified_by, ""))
            db.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def webhook_result(event_id: str, result: str) -> None:
    with _lock:
        db = _db()
        db.execute("UPDATE webhook_events SET result=? WHERE event_id=?", (result, event_id))
        db.commit()


def webhook_events(limit: int = 30) -> List[Dict[str, Any]]:
    with _lock:
        rows = _db().execute("SELECT event_id, ts, event_type, invoice_id, verified_by, result FROM webhook_events "
                             "ORDER BY ts DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
    return [dict(zip(("event_id", "ts", "event_type", "invoice_id", "verified_by", "result"), r)) for r in rows]


# ---- agent actions awaiting human approval
def save_action(a: AgentAction) -> AgentAction:
    with _lock:
        db = _db()
        db.execute("INSERT OR REPLACE INTO agent_actions(id, ts, data) VALUES (?,?,?)", (a.id, a.created_at or now(), a.model_dump_json()))
        db.commit()
    return a


def get_action(action_id: str) -> Optional[AgentAction]:
    with _lock:
        row = _db().execute("SELECT data FROM agent_actions WHERE id=?", (action_id,)).fetchone()
    return AgentAction.model_validate_json(row[0]) if row else None


def actions(status: str = "", limit: int = 50) -> List[AgentAction]:
    with _lock:
        rows = _db().execute("SELECT data FROM agent_actions ORDER BY ts DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
    out = [AgentAction.model_validate_json(r[0]) for r in rows]
    return [a for a in out if not status or a.status == status]
