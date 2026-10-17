"""LLM adapter.

Live: NVIDIA Nemotron models on Nebius Token Factory through its OpenAI-compatible REST API
(POST {base}/chat/completions, GET {base}/models, `Authorization: Bearer $NEBIUS_API_KEY`).
Mock: deterministic heuristics supplied by the caller, so the full flow runs offline.

Every call returns an LLMResult that says which model/tier ran, whether it was live, mock, or a live call that
failed and fell back to the heuristic (with a human-readable warning that the UI shows in the agent trace).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional

import httpx

from .. import config
from . import http
from .http import AdapterError

Tier = Literal["fast", "main", "deep"]
PROVIDER = "Nebius Token Factory"


@dataclass
class LLMResult:
    data: Dict[str, Any]
    ms: int
    mode: str  # live | mock | fallback
    model: str
    tier: str
    warning: str = ""
    usage: Dict[str, int] = field(default_factory=dict)


# ------------------------------------------------------------------ model resolution / startup check
@dataclass
class ModelCheck:
    checked: bool = False
    ok: bool = True
    resolved: Dict[str, str] = field(default_factory=dict)
    missing: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    nvidia_available: List[str] = field(default_factory=list)
    error: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"checked": self.checked, "ok": self.ok, "resolved": self.resolved, "missing": self.missing,
                "notes": self.notes, "nvidia_available": self.nvidia_available, "error": self.error}


MODEL_CHECK = ModelCheck()


def _norm(model_id: str) -> str:
    vendor, _, name = model_id.lower().partition("/")
    name = name.replace("_", ".")
    if name.startswith(vendor + "-"):
        name = name[len(vendor) + 1:]
    return f"{vendor}/{name}"


def resolve_models(configured: Dict[str, str], available: List[str]) -> ModelCheck:
    """Match configured IDs against /v1/models: exact, then case-insensitive, then normalized
    (e.g. `nvidia/Nemotron-3-Nano-30B-A3B` -> `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`)."""
    chk = ModelCheck(checked=True, nvidia_available=sorted(a for a in available if a.lower().startswith("nvidia/")))
    lower = {a.lower(): a for a in available}
    norm = {_norm(a): a for a in available}
    for tier, mid in configured.items():
        if mid in available:
            chk.resolved[tier] = mid
        elif mid.lower() in lower:
            chk.resolved[tier] = lower[mid.lower()]
            chk.notes.append(f"{tier}: '{mid}' matched '{chk.resolved[tier]}' (case differs)")
        elif _norm(mid) in norm:
            chk.resolved[tier] = norm[_norm(mid)]
            chk.notes.append(f"{tier}: '{mid}' resolved to '{chk.resolved[tier]}'")
        else:
            chk.resolved[tier] = mid
            chk.missing.append(f"{tier}: {mid}")
    chk.ok = not chk.missing
    return chk


def check_models() -> ModelCheck:
    """Startup check (only when a key exists): list models and resolve the configured tier IDs."""
    global MODEL_CHECK
    s = config.get_settings()
    if not s.llm_live or not s.llm_check_models:
        MODEL_CHECK = ModelCheck()
        return MODEL_CHECK
    try:
        r = http.request("Nebius /v1/models", "GET", s.nebius_base_url.rstrip("/") + "/models",
                         timeout_s=15, retries=1, headers={"Authorization": f"Bearer {s.nebius_api_key}"},
                         describe_error=_describe_error)
        ids = [m.get("id", "") for m in r.json().get("data", []) if m.get("id")]
        MODEL_CHECK = resolve_models(s.models(), ids)
    except (AdapterError, ValueError) as e:
        MODEL_CHECK = ModelCheck(checked=True, ok=False, error=str(e)[:300])
    return MODEL_CHECK


# ------------------------------------------------------------------ helpers
def _describe_error(resp: httpx.Response) -> str:
    try:
        j = resp.json()
    except ValueError:
        return resp.text[:300]
    d = j.get("detail") if isinstance(j, dict) else None
    if isinstance(d, str):
        return d[:300]
    if isinstance(j, dict) and isinstance(j.get("error"), dict):
        return str(j["error"].get("message", j["error"]))[:300]
    return json.dumps(j)[:300]


def extract_json(text: str) -> Dict[str, Any]:
    """Pull one JSON object out of model text: tolerate <think> blocks, code fences, and prose around it."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.S)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in model output")
    obj = json.loads(text[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("model output JSON is not an object")
    return obj


# ------------------------------------------------------------------ clients
class NebiusLLM:
    mode = "live"
    _no_json_mode: set = set()

    def __init__(self) -> None:
        self.s = config.get_settings()
        self.url = self.s.nebius_base_url.rstrip("/") + "/chat/completions"

    def model_for(self, tier: Tier) -> str:
        return MODEL_CHECK.resolved.get(tier) or self.s.models()[tier]

    def _body(self, model: str, messages: List[Dict[str, str]], json_mode: bool) -> Dict[str, Any]:
        body: Dict[str, Any] = {"model": model, "messages": messages, "temperature": 0.2, "max_tokens": self.s.llm_max_tokens}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if self.s.llm_reasoning_effort:
            body["reasoning_effort"] = self.s.llm_reasoning_effort
        return body

    def _chat(self, model: str, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.s.nebius_api_key}", "Content-Type": "application/json"}
        json_mode = self.s.llm_json_mode and model not in self._no_json_mode
        try:
            r = http.request(PROVIDER, "POST", self.url, timeout_s=self.s.llm_timeout_s, retries=self.s.llm_max_retries,
                             headers=headers, json=self._body(model, messages, json_mode), describe_error=_describe_error)
        except AdapterError as e:
            # Some models reject JSON mode; retry once in plain-text mode and remember that for this model.
            if json_mode and e.status in (400, 422) and "response_format" in e.message.lower():
                self._no_json_mode.add(model)
                r = http.request(PROVIDER, "POST", self.url, timeout_s=self.s.llm_timeout_s, retries=self.s.llm_max_retries,
                                 headers=headers, json=self._body(model, messages, False), describe_error=_describe_error)
            else:
                raise
        try:
            return r.json()
        except ValueError as e:
            raise AdapterError(PROVIDER, "response was not JSON") from e

    def complete_json(self, tier: Tier, system: str, user: str, fallback: Callable[[], Dict[str, Any]],
                      validate: Optional[Callable[[Dict[str, Any]], Any]] = None) -> LLMResult:
        t0 = time.time()
        model = self.model_for(tier)
        messages = [{"role": "system", "content": system + "\nRespond with a single JSON object only, no prose."},
                    {"role": "user", "content": user}]
        usage: Dict[str, int] = {}
        try:
            for attempt in range(2):  # one repair round if the JSON is malformed or fails validation
                j = self._chat(model, messages)
                for k, v in (j.get("usage") or {}).items():
                    if isinstance(v, int):
                        usage[k] = usage.get(k, 0) + v
                choice = (j.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                content = msg.get("content") or ""
                if not content.strip():
                    content = msg.get("reasoning_content") or msg.get("reasoning") or ""
                try:
                    data = extract_json(content)
                    if validate:
                        validate(data)
                    return LLMResult(data, int((time.time() - t0) * 1000), "live", model, tier, usage=usage)
                except (ValueError, TypeError, KeyError) as e:  # pydantic ValidationError is a ValueError
                    if attempt == 1:
                        raise AdapterError(PROVIDER, f"unusable model output after repair: {str(e)[:160]}")
                    messages = messages + [{"role": "assistant", "content": content[:4000]},
                                           {"role": "user", "content": f"That output was invalid ({str(e)[:300]}). "
                                                                       "Reply again with only the corrected JSON object."}]
            raise AdapterError(PROVIDER, "no usable output")  # pragma: no cover
        except AdapterError as e:
            if not self.s.fallback_to_mock:
                raise
            data = fallback()
            return LLMResult(data, int((time.time() - t0) * 1000), "fallback", model, tier, usage=usage,
                             warning=f"Live {model} call failed ({e.message[:160]}); used the offline heuristic instead.")


class MockLLM:
    mode = "mock"

    def model_for(self, tier: Tier) -> str:
        return config.get_settings().models()[tier]

    def complete_json(self, tier, system, user, fallback, validate=None) -> LLMResult:
        t0 = time.time()
        data = fallback()
        return LLMResult(data, int((time.time() - t0) * 1000), "mock", self.model_for(tier), tier)


def get_llm():
    return NebiusLLM() if config.get_settings().llm_live else MockLLM()
