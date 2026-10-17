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
from .models import Invoice

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None
_conn_path = ""

SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
  id TEXT PRIMARY KEY, seq INTEGER, data TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, event TEXT NOT NULL, invoice_id TEXT, detail TEXT);
"""


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
