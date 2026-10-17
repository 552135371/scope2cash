"""Shared HTTP plumbing for live adapters: one place for timeouts, retries with backoff, Retry-After, and
readable errors. Tests inject an httpx.MockTransport through `set_transport`."""
from __future__ import annotations

import time
from typing import Any, Callable, Iterable, Optional

import httpx

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
_transport: Optional[httpx.BaseTransport] = None
_sleep: Callable[[float], None] = time.sleep


class AdapterError(RuntimeError):
    """A live provider call failed after retries. `retryable` is False for 4xx caller errors."""

    def __init__(self, provider: str, message: str, status: Optional[int] = None, retryable: bool = False):
        super().__init__(f"{provider}: {message}")
        self.provider, self.status, self.retryable, self.message = provider, status, retryable, message


def set_transport(transport: Optional[httpx.BaseTransport]) -> None:
    global _transport
    _transport = transport


def set_sleep(fn: Callable[[float], None]) -> None:
    global _sleep
    _sleep = fn


def client(timeout_s: float, **kw: Any) -> httpx.Client:
    timeout = httpx.Timeout(timeout_s, connect=min(10.0, timeout_s))
    return httpx.Client(timeout=timeout, transport=_transport, **kw)


def _retry_after(resp: httpx.Response, default: float) -> float:
    v = resp.headers.get("retry-after")
    try:
        return min(float(v), 20.0) if v is not None else default
    except ValueError:
        return default


def request(provider: str, method: str, url: str, *, timeout_s: float, retries: int = 2,
            describe_error: Callable[[httpx.Response], str] = lambda r: r.text[:300],
            retry_status: Iterable[int] = RETRY_STATUS, **kw: Any) -> httpx.Response:
    """Send a request, retrying transport errors and retryable statuses with exponential backoff.
    Returns the successful response or raises AdapterError."""
    attempt, backoff = 0, 0.6
    while True:
        try:
            with client(timeout_s) as c:
                resp = c.request(method, url, **kw)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            if attempt >= retries:
                kind = "timed out" if isinstance(e, httpx.TimeoutException) else "network error"
                raise AdapterError(provider, f"{kind} after {attempt + 1} attempt(s): {e.__class__.__name__}", retryable=True) from e
            _sleep(backoff * (2 ** attempt))
            attempt += 1
            continue
        if resp.status_code < 400:
            return resp
        if resp.status_code in set(retry_status) and attempt < retries:
            _sleep(_retry_after(resp, backoff * (2 ** attempt)))
            attempt += 1
            continue
        raise AdapterError(provider, f"HTTP {resp.status_code}: {describe_error(resp)}", status=resp.status_code,
                           retryable=resp.status_code in set(retry_status))
