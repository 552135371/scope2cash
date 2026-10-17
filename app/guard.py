"""Lightweight protection for a public demo URL: per-IP rate limit and a daily cap on live model/search runs.

In-memory only (single instance, resets on restart), which is enough for a hackathon demo on one free instance.
"""
import threading
import time
from collections import defaultdict, deque
from datetime import date

from fastapi import Request

from . import config

_lock = threading.Lock()
_hits: dict = defaultdict(deque)
_day = {"date": None, "count": 0}


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limited(ip: str) -> int:
    """Return 0 if allowed, else the seconds to wait. Counts the request when allowed."""
    limit = config.get_settings().effective_rate_limit
    if limit <= 0:
        return 0
    now = time.time()
    with _lock:
        q = _hits[ip]
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= limit:
            return int(3600 - (now - q[0])) + 1
        q.append(now)
    return 0


def take_live_run() -> bool:
    """Consume one unit of today's live budget. False means the cap is spent and the request should run offline."""
    cap = config.get_settings().daily_live_cap
    if cap <= 0:
        return True
    with _lock:
        today = date.today()
        if _day["date"] != today:
            _day.update(date=today, count=0)
        if _day["count"] >= cap:
            return False
        _day["count"] += 1
        return True


def live_runs_today() -> int:
    return _day["count"] if _day["date"] == date.today() else 0


def reset() -> None:
    with _lock:
        _hits.clear()
        _day.update(date=None, count=0)
