"""Provider selection: DeepSeek / Nebius / NVIDIA NIM share one OpenAI-compatible client."""
import httpx

from app import config
from app.adapters import llm


def _clear(monkeypatch):
    for k in ("LLM_PROVIDER", "NEBIUS_API_KEY", "DEEPSEEK_API_KEY", "NVIDIA_API_KEY", "MODEL_FAST", "MODEL_MAIN",
              "MODEL_DEEP", "LLM_REASONING_EFFORT", "DEEPSEEK_BASE_URL", "SCOPE2CASH_FORCE_MOCK"):
        monkeypatch.delenv(k, raising=False)


def test_deepseek_selected_from_key(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-ds-key")
    s = config.reload_settings()
    assert s.llm_provider == "deepseek" and s.llm_label == "DeepSeek API" and s.llm_live
    assert s.llm_base_url == "https://api.deepseek.com"
    assert s.models() == {"fast": "deepseek-flash", "main": "deepseek-flash", "deep": "deepseek-v4-pro"}
    assert s.effort_for("fast") == "low" and s.effort_for("deep") == "high"


def test_explicit_provider_and_nim_ready(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-ds-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-nv-key")
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")
    s = config.reload_settings()
    assert s.llm_provider == "nvidia" and s.llm_api_key == "test-nv-key"
    assert s.llm_base_url.startswith("https://integrate.api.nvidia.com")


def test_deepseek_request_shape(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-ds-key")
    config.reload_settings()
    seen = {}

    def handler(req: httpx.Request):
        import json
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        seen["auth"] = req.headers["authorization"]
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}],
                                         "usage": {"prompt_tokens": 5, "completion_tokens": 3}})

    from app.adapters import http as http_mod
    http_mod.set_transport(httpx.MockTransport(handler))
    c = llm.OpenAICompatLLM()
    j = c._chat("deepseek-v4-pro", [{"role": "user", "content": "hi"}], "deep")
    assert seen["url"] == "https://api.deepseek.com/chat/completions"
    assert seen["auth"] == "Bearer test-ds-key"
    assert seen["body"]["reasoning_effort"] == "high" and seen["body"]["response_format"] == {"type": "json_object"}
    assert j["choices"][0]["message"]["content"] == '{"ok": true}'


def test_keyless_run_labels_match_the_submission(monkeypatch):
    """No LLM key at all (a judge cloning the repo): mock mode shows DeepSeek labels and model names, the same provider
    the hosted demo and the write-up use, instead of an unrelated default."""
    _clear(monkeypatch)
    s = config.reload_settings()
    assert s.llm_provider == "deepseek" and s.llm_label == "DeepSeek API" and not s.llm_live
    assert s.models() == {"fast": "deepseek-flash", "main": "deepseek-flash", "deep": "deepseek-v4-pro"}
    from fastapi.testclient import TestClient
    from app.main import app
    h = TestClient(app).get("/api/health").json()
    assert h["adapters"]["llm"] == {**h["adapters"]["llm"], "mode": "mock", "provider": "DeepSeek API", "provider_id": "deepseek"}


def test_quickstart_copy_is_mode_aware():
    """The 'Judging in a hurry?' box promises real sandbox calls only when PayPal is live; keyless runs get mock wording
    that names the button that actually exists there (⏩ paid)."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "app" / "static"
    html, js = (root / "index.html").read_text(), (root / "app.js").read_text()
    assert 'id="qs-text"' in html and "client pays (sandbox card)" in html
    assert 'h.adapters.payments.mode !== "live"' in js and "offline mock mode" in js and "⏩ paid" in js
    # the payments card really labels its mock pay button "⏩ paid" (keeps the copy honest if the button is renamed)
    assert 'data-a="pay" data-id="${i.id}">⏩ paid</button>' in js


def test_paypal_badge_says_sandbox_not_live(live):
    """With sandbox keys the adapter is 'live' (real API calls) but the header badge must read 'sandbox', so nobody
    mistakes the demo for real-money payments. Keyless runs read 'mock'."""
    from fastapi.testclient import TestClient
    from app.main import app
    a = TestClient(app).get("/api/health").json()["adapters"]
    assert a["payments"]["mode"] == "live" and a["payments"]["env"] == "sandbox"
    assert a["webhooks"]["env"] == "mock"  # no PAYPAL_WEBHOOK_ID in the fake env
    js = (__import__("pathlib").Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text()
    assert "${esc(a.env || a.mode)}" in js


def test_paypal_badge_keyless_is_mock():
    from fastapi.testclient import TestClient
    from app.main import app
    a = TestClient(app).get("/api/health").json()["adapters"]
    assert a["payments"]["mode"] == "mock" and a["payments"]["env"] == "mock"
