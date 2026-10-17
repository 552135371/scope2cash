import json
import threading
from pathlib import Path
from typing import Dict, List

from .models import Invoice

_PATH = Path(__file__).resolve().parent.parent / "data" / "invoices.json"
_lock = threading.Lock()


def _load() -> Dict[str, dict]:
    if _PATH.exists():
        return json.loads(_PATH.read_text())
    return {}


def save(inv: Invoice) -> Invoice:
    with _lock:
        d = _load()
        d[inv.id] = inv.model_dump()
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(json.dumps(d, indent=2))
    return inv


def get(inv_id: str) -> Invoice | None:
    d = _load().get(inv_id)
    return Invoice.model_validate(d) if d else None


def all_invoices() -> List[Invoice]:
    return [Invoice.model_validate(v) for v in _load().values()]


def clear() -> None:
    with _lock:
        if _PATH.exists():
            _PATH.unlink()
