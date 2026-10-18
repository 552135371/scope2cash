"""Alexa+ add-on: MCP server over Streamable HTTP (2025-11-25 handshake), voice skills, simulated Alexa+ turns.
Nothing here may send a reminder: proposals stay pending until a human approves them in the dashboard."""
import asyncio
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import store
from app.demo_data import ensure_samples

ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def samples():
    ensure_samples()
    return store.all_invoices()


@pytest.fixture
def client():
    from alexa.asgi import app
    with TestClient(app) as c:  # runs the lifespan, which starts the MCP session manager
        yield c


def rpc(c, method, params=None, id_=1, version="2025-11-25", headers=None):
    body = {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}
    h = {**ACCEPT, "MCP-Protocol-Version": version, **(headers or {})}
    return c.post("/mcp", json=body, headers=h)


# ---------------------------------------------------------------- MCP over Streamable HTTP
def test_initialize_negotiates_2025_11_25(client):
    r = rpc(client, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                   "clientInfo": {"name": "alexa-plus-test", "version": "1"}})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert res["protocolVersion"] == "2025-11-25"
    assert res["serverInfo"]["name"] == "scope2cash"
    assert "tools" in res["capabilities"]


def test_tools_list_and_call_over_http(client, samples):
    rpc(client, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    tools = rpc(client, "tools/list", id_=2).json()["result"]["tools"]
    by = {t["name"]: t for t in tools}
    assert set(by) == {"who_owes_me", "cashflow_this_week", "pending_approvals", "propose_reminder"}
    assert by["who_owes_me"]["annotations"]["readOnlyHint"] is True
    assert by["propose_reminder"]["annotations"]["destructiveHint"] is False
    assert "does NOT send" in by["propose_reminder"]["description"]
    r = rpc(client, "tools/call", {"name": "who_owes_me", "arguments": {}}, id_=3).json()["result"]
    assert r["isError"] is False
    speech = r["structuredContent"]["speech"]
    assert "owe you $5,550" in speech and "Northwind Coffee Roasters" in speech


def test_mcp_rejects_foreign_host(client):
    r = rpc(client, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
            headers={"Host": "evil.example"})
    assert r.status_code in (400, 403, 421)


def test_in_process_client_sees_same_tools(samples):
    from mcp import Client
    from alexa.mcp_server import server

    async def go():
        async with Client(server) as c:
            r = await c.call_tool("cashflow_this_week", {})
            return r.structured_content
    out = asyncio.run(go())
    assert out["data"]["overdue"] == 1800 + 450 + 1200
    assert "already overdue" in out["speech"]


# ---------------------------------------------------------------- skills
def test_client_matching(samples):
    from alexa.skills import match_client
    assert match_client("harbor") == "Harbor Yoga"
    assert match_client("lumen dental") == "Lumen Dental Clinic"
    assert match_client("northwind coffee") == "Northwind Coffee Roasters"
    assert match_client("kite and key") == "Kite & Key Travel"
    assert match_client("zebra plumbing") is None


def test_who_owes_me_one_client(samples):
    from alexa.skills import who_owes_me
    out = who_owes_me("harbor yoga")
    assert out["data"]["clients"][0]["owed"] == 2400
    assert out["speech"].startswith("Harbor Yoga owes $2,400, 17 days late.")


def test_propose_reminder_queues_but_never_sends(samples):
    from alexa.skills import propose_reminder
    before = {i.id: i.reminded_at for i in store.all_invoices()}
    out = propose_reminder(client="harbor")
    assert out["data"]["sent"] is False
    queued = out["data"]["queued"]
    assert len(queued) == 2  # overdue UX milestone + partially-paid core build
    assert "Nothing goes out until you approve it" in out["speech"]
    pending = store.actions("pending")
    assert {a.invoice_id for a in pending} >= {q["invoice_id"] for q in queued}
    assert all(a.tool == "send_invoice_reminder" for a in pending)
    assert {i.id: i.reminded_at for i in store.all_invoices()} == before  # nothing was reminded
    assert "Harbor Yoga" in queued[0]["params"]["note"] and "past due" in queued[0]["params"]["note"]
    again = propose_reminder(client="Harbor Yoga")  # idempotent
    assert again["data"]["queued"] == [] and len(again["data"]["already_pending"]) == 2
    assert "already waiting" in again["speech"]


def test_propose_reminder_nothing_to_chase(samples):
    from alexa.skills import propose_reminder
    out = propose_reminder(invoice_number="S2C-SAMPLE-01")  # paid
    assert out["data"]["queued"] == [] and "nothing unpaid" in out["speech"].lower()


def test_voice_queued_reminder_is_approved_in_dashboard(samples):
    """End to end: voice queues, the human approves through the existing dashboard endpoint, PayPal (mock) sends."""
    from alexa.skills import propose_reminder
    from app.main import app
    a = propose_reminder(client="lumen")["data"]["queued"][0]
    c = TestClient(app)
    r = c.post(f"/api/agent/actions/{a['id']}/approve")
    assert r.status_code == 200, r.text
    assert r.json()["action"]["status"] == "executed"
    assert store.get(a["invoice_id"]).reminded_at


# ---------------------------------------------------------------- simulated Alexa+ conversation
def test_conversation_with_memory(client, samples):
    def say(text, sid=""):
        r = client.post("/api/alexa/ask", json={"text": text, "session_id": sid})
        assert r.status_code == 200, r.text
        return r.json()

    t1 = say("Alexa, who still owes me money?")
    sid = t1["session_id"]
    assert t1["calls"][0]["tool"] == "who_owes_me" and t1["memory"]["offer"] == "Northwind Coffee Roasters"
    t2 = say("yes", sid)
    assert t2["calls"][0] == {**t2["calls"][0], "tool": "propose_reminder", "args": {"client": "Northwind Coffee Roasters"}}
    assert "Nothing goes out until you approve" in t2["speech"]
    t3 = say("send it now", sid)
    assert "can't send or approve anything by voice" in t3["speech"]
    assert t3["calls"][0]["tool"] == "pending_approvals"
    t4 = say("what about lumen?", sid)
    assert t4["memory"]["last_client"] == "Lumen Dental Clinic"
    t5 = say("remind them", sid)
    assert t5["calls"][0]["args"] == {"client": "Lumen Dental Clinic"}
    t6 = say("what's coming in this week", sid)
    assert t6["calls"][0]["tool"] == "cashflow_this_week"
    assert all(a.status == "pending" for a in store.actions())  # still nothing executed


def test_unknown_offline_falls_back_to_help(client, samples):
    r = client.post("/api/alexa/ask", json={"text": "play some jazz"}).json()
    assert r["calls"] == [] and "You can ask me" in r["speech"] and r["router"] == "rules"


def test_page_info_and_core_untouched(client):
    assert "Alexa+ simulation" in client.get("/alexa").text
    info = client.get("/api/alexa/info").json()
    assert info["min_protocol_version"] == "2025-11-25" and "2025-11-25" in info["handshake_versions"]
    assert info["sends_without_approval"] is False
    assert client.get("/api/health").json()["ok"] is True
    assert client.get("/alexa/../main.py").status_code == 404


def test_today_week_boundaries(samples):
    from alexa.skills import cashflow_this_week
    out = cashflow_this_week(today=date.today())
    assert set(out["data"]) >= {"collected", "due_this_week", "overdue", "week_start"}
