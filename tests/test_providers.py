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
