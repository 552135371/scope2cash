import os

os.environ["SCOPE2CASH_FORCE_MOCK"] = "1"
os.environ.setdefault("SCOPE2CASH_DB", ":memory:")

import httpx  # noqa: E402
import pytest  # noqa: E402

from app import config, guard  # noqa: E402
from app.adapters import http, llm, payments  # noqa: E402

LIVE_ENV = {
    "SCOPE2CASH_FORCE_MOCK": "",
    "LLM_PROVIDER": "nebius",
    "NEBIUS_API_KEY": "test-nebius-key",
    "NEBIUS_BASE_URL": "https://api.tokenfactory.nebius.com/v1/",
    "MODEL_FAST": config.DEFAULT_MODEL_FAST,
    "MODEL_MAIN": config.DEFAULT_MODEL_MAIN,
    "MODEL_DEEP": config.DEFAULT_MODEL_DEEP,
    "LLM_JSON_MODE": "1",
    "LLM_REASONING_EFFORT": "",
    "TAVILY_API_KEY": "tvly-test",
    "TAVILY_BASE_URL": "https://api.tavily.com",
    "TAVILY_SEARCH_DEPTH": "basic",
    "PAYPAL_CLIENT_ID": "test-client",
    "PAYPAL_CLIENT_SECRET": "test-secret",
    "PAYPAL_ENV": "sandbox",
    "PAYPAL_INVOICER_EMAIL": "sb-merchant@business.example.com",
    "PAYPAL_BUSINESS_NAME": "",
    "FALLBACK_TO_MOCK": "1",
    "FREELANCER_NAME": "Huilin",
}


def _block(request: httpx.Request) -> httpx.Response:  # no test may touch the real network
    raise AssertionError(f"unexpected network call: {request.method} {request.url}")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOPE2CASH_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("SCOPE2CASH_FORCE_MOCK", "1")
    config.reload_settings()
    http.set_transport(httpx.MockTransport(_block))
    http.set_sleep(lambda s: None)
    payments._token_cache.clear()
    llm.MODEL_CHECK = llm.ModelCheck()
    guard.reset()
    llm.NebiusLLM._no_json_mode = set()
    yield
    http.set_transport(None)
    config.reload_settings()


@pytest.fixture
def live(monkeypatch):
    """Switch every adapter to live mode with fake keys; returns a function to install an HTTP handler."""
    for k, v in LIVE_ENV.items():
        monkeypatch.setenv(k, v)
    config.reload_settings()

    def install(handler):
        http.set_transport(httpx.MockTransport(handler))
    return install
