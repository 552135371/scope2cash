"""Gap #6 (v0.5): step 4's two model calls (proposal message + negotiation tips) are independent, so in live mode they
run at the same time. Contract test with a fake model that takes 0.4 s per call: sequential would take >= 0.8 s."""
import json
import threading
import time

import httpx
from fastapi.testclient import TestClient

from app.main import app
from tests.test_multicurrency import _pricing, _scope


def test_live_quote_runs_cover_and_tips_in_parallel(live):
    seen, lock, active, peak = [], threading.Lock(), [0], [0]

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path.endswith("/chat/completions"), req.url
        body = json.loads(req.content)
        system = body["messages"][0]["content"]
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.4)
        with lock:
            active[0] -= 1
        if "negotiation coach" in system:
            seen.append("tips")
            data = {"negotiation_tips": ["Trade scope, not price.", "Offer a phased start.", "Anchor on outcomes."]}
        else:
            seen.append("cover")
            data = {"cover_message": "Hi Atelier Nord, here is the plan. Best, Huilin"}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(data)}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 10}})

    live(handler)
    t0 = time.time()
    r = TestClient(app).post("/api/quote", json={"scope": _scope().model_dump(), "pricing": _pricing().model_dump(), "currency": "EUR"})
    took = time.time() - t0
    assert r.status_code == 200, r.text
    q = r.json()["quote"]
    assert sorted(seen) == ["cover", "tips"] and peak[0] == 2
    assert took < 0.75, f"calls ran one after the other ({took:.2f}s)"
    assert q["cover_message"].startswith("Hi Atelier Nord") and len(q["negotiation_tips"]) == 3 and q["currency"] == "EUR"
    steps = {t["step"]: t["mode"] for t in r.json()["trace"]}
    assert steps["Draft proposal message"] == "live" and steps["Negotiation coaching"] == "live"


def test_live_quote_parallel_failure_falls_back_per_call(live):
    def handler(req: httpx.Request) -> httpx.Response:
        if "negotiation coach" in json.loads(req.content)["messages"][0]["content"]:
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"cover_message": "Hello from the live model"})}}]})

    live(handler)
    r = TestClient(app).post("/api/quote", json={"scope": _scope().model_dump(), "pricing": _pricing().model_dump()})
    assert r.status_code == 200, r.text
    steps = {t["step"]: t["mode"] for t in r.json()["trace"]}
    assert steps["Draft proposal message"] == "live" and steps["Negotiation coaching"] == "fallback"
    assert r.json()["quote"]["cover_message"] == "Hello from the live model" and len(r.json()["quote"]["negotiation_tips"]) >= 3
