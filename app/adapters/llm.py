"""LLM adapter. Live: NVIDIA Nemotron on Nebius Token Factory (OpenAI-compatible API).
Mock: deterministic heuristics supplied by the caller, so the full flow runs offline."""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Dict, Literal, Protocol, Tuple

from ..config import settings

Tier = Literal["fast", "main", "deep"]


class LLMClient(Protocol):
    mode: str

    def model_for(self, tier: Tier) -> str: ...

    def complete_json(self, tier: Tier, system: str, user: str,
                      fallback: Callable[[], Dict[str, Any]]) -> Tuple[Dict[str, Any], int]: ...


def _extract_json(text: str) -> Dict[str, Any]:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.S)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start:end + 1])


class NebiusLLM:
    mode = "live"

    def __init__(self) -> None:
        from openai import OpenAI
        self.client = OpenAI(base_url=settings.nebius_base_url, api_key=settings.nebius_api_key)

    def model_for(self, tier: Tier) -> str:
        return {"fast": settings.model_fast, "main": settings.model_main, "deep": settings.model_deep}[tier]

    def complete_json(self, tier, system, user, fallback):
        t0 = time.time()
        resp = self.client.chat.completions.create(
            model=self.model_for(tier),
            temperature=0.2,
            max_tokens=2500,
            messages=[
                {"role": "system", "content": system + "\nRespond with a single JSON object only."},
                {"role": "user", "content": user},
            ],
        )
        data = _extract_json(resp.choices[0].message.content or "")
        return data, int((time.time() - t0) * 1000)


class MockLLM:
    mode = "mock"

    def model_for(self, tier: Tier) -> str:
        return {"fast": settings.model_fast, "main": settings.model_main, "deep": settings.model_deep}[tier]

    def complete_json(self, tier, system, user, fallback):
        t0 = time.time()
        data = fallback()
        return data, int((time.time() - t0) * 1000) + {"fast": 180, "main": 640, "deep": 1100}[tier]


def get_llm() -> LLMClient:
    return NebiusLLM() if settings.llm_live else MockLLM()
