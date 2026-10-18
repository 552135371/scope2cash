"""Simulated Alexa+ experience: turns an utterance into MCP tool calls on the Scope2Cash MCP server.

This is the "agentic tool" side of the hackathon's simulation path. It is a real MCP *client*: every answer comes
from `tools/call` on alexa.mcp_server over an in-process MCP session (the same server Alexa+ reaches at /mcp).

Routing: a deterministic rule router handles the everyday phrasings for free; anything it can't place goes to the
configured LLM (DeepSeek on the hosted demo) as a JSON intent classifier, which falls back to the rules offline.
Conversation memory (last client discussed, the offer Alexa just made) is kept per session in SQLite, so "yes" or
"remind her" work across turns and across page reloads.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from mcp import Client

from app import config, guard, store
from . import skills
from .mcp_server import TOOL_NAMES, server

Plan = List[Tuple[str, Dict[str, Any]]]

HELP = ("You can ask me: who still owes me money? How much does Harbor Yoga owe? What's coming in this week? "
        "Remind Lumen about their invoice. Or: what's waiting for my approval?")

# ------------------------------------------------------------------ session memory (SQLite, survives reloads)
_SCHEMA = "CREATE TABLE IF NOT EXISTS voice_sessions (id TEXT PRIMARY KEY, ts TEXT NOT NULL, data TEXT NOT NULL)"


def load_session(sid: str) -> Dict[str, Any]:
    with store._lock:
        db = store._db()
        db.execute(_SCHEMA)
        row = db.execute("SELECT data FROM voice_sessions WHERE id=?", (sid,)).fetchone()
    return json.loads(row[0]) if row else {}


def save_session(sid: str, data: Dict[str, Any]) -> None:
    data = {**data, "turns": data.get("turns", [])[-10:]}
    with store._lock:
        db = store._db()
        db.execute(_SCHEMA)
        db.execute("INSERT OR REPLACE INTO voice_sessions(id, ts, data) VALUES (?,?,?)", (sid, store.now(), json.dumps(data)))
        db.commit()


# ------------------------------------------------------------------ rule router
_YES = re.compile(r"^\s*(yes|yeah|yep|sure|ok(ay)?|please( do)?|do it|go ahead|sounds good|queue it)\b", re.I)
_NO = re.compile(r"^\s*(no|nope|not now|never ?mind|cancel|stop|don'?t)\b", re.I)
_SEND = re.compile(r"\b(send|email|text)\b.*\b(now|it|reminder|them|her|him)\b|\bapprove\b", re.I)
_REMIND = re.compile(r"\b(remind|reminder|nudge|chase|follow[- ]?up|ping|bug)\b", re.I)
_PENDING = re.compile(r"\b(waiting|pending|approval|queued|to approve)\b", re.I)
_WEEK = re.compile(r"\b(this week|cash ?flow|coming in|collected|incoming|expect|earn(ed)?|made)\b", re.I)
_OWE = re.compile(r"\b(owe|owes|owing|owed|unpaid|outstanding|hasn'?t paid|haven'?t paid|not paid|late|overdue|pay me|paid me|balance)\b", re.I)
_ABOUT = re.compile(r"^\s*(what|how) about\s+(.+?)[?.!]*$", re.I)
_HELP = re.compile(r"\b(help|what can you do|how does this work)\b", re.I)
_PRONOUN = re.compile(r"\b(her|him|them|they|their|that client|that one|it)\b", re.I)
_VOICE_WORDS = {"who", "owes", "owe", "me", "money", "still", "much", "how", "does", "what", "about", "remind", "reminder",
                "send", "week", "this", "chase", "nudge", "late", "overdue", "invoice", "invoices", "their", "them", "her",
                "his", "pay", "paid", "the", "for", "alexa", "ask", "scope", "cash", "please", "can", "you", "is", "are",
                "has", "hasn", "have", "any", "my", "to", "a", "do", "follow", "up", "tell", "status", "s"}


def find_client(text: str) -> Optional[str]:
    words = [w for w in re.findall(r"[a-z0-9&']+", text.lower()) if w not in _VOICE_WORDS]
    return skills.match_client(" ".join(words)) if words else None


def rule_route(text: str, ctx: Dict[str, Any]) -> Tuple[Optional[Plan], str]:
    """Returns (plan, direct_reply). plan None + empty reply = not understood."""
    t = text.strip()
    if not t:
        return None, HELP
    if ctx.get("offer") and _YES.search(t):
        return [("propose_reminder", {"client": ctx["offer"]})], ""
    if _NO.search(t) and not _OWE.search(t):
        return [], "Okay, I won't queue anything."
    client = find_client(t)
    if not client and _PRONOUN.search(t):
        client = ctx.get("last_client")
    if _SEND.search(t):
        plan: Plan = [("pending_approvals", {})]
        return plan, ("I can't send or approve anything by voice. For safety, you approve each reminder with a tap in "
                      "Scope2Cash. ")
    if _REMIND.search(t):
        return [("propose_reminder", {"client": client} if client else {})], ""
    if _PENDING.search(t):
        return [("pending_approvals", {})], ""
    if _WEEK.search(t):
        return [("cashflow_this_week", {})], ""
    if _OWE.search(t):
        return [("who_owes_me", {"client": client} if client else {})], ""
    m = _ABOUT.match(t)
    if (m or client) and client:
        return [("who_owes_me", {"client": client})], ""
    if _HELP.search(t):
        return [], HELP
    return None, ""


# ------------------------------------------------------------------ LLM router (only for what the rules can't place)
LLM_SYSTEM = """You route a freelancer's spoken request to ONE tool of a voice assistant called Scope2Cash.
Tools:
- who_owes_me {{"client": optional spoken client name}}: who still owes money / how much one client owes
- cashflow_this_week {{}}: collected this week, due this week, overdue
- pending_approvals {{}}: what is waiting for approval
- propose_reminder {{"client": optional}}: QUEUE a payment reminder for approval (never sends)
- help {{}}: anything else
Known clients: {clients}. Last client discussed: {last}.
Return JSON {{"tool": one of the names, "args": object}}."""


def llm_route(text: str, ctx: Dict[str, Any]) -> Tuple[Optional[Plan], str, str]:
    s = config.get_settings()
    if not s.llm_live or not guard.take_live_run():
        return None, "", "rules"
    from app.adapters.llm import get_llm
    system = LLM_SYSTEM.format(clients=", ".join(skills.client_names()) or "none", last=ctx.get("last_client") or "none")
    r = get_llm().complete_json("fast", system, text[:500], lambda: {"tool": "help", "args": {}},
                                validate=lambda d: d.get("tool") in TOOL_NAMES + ("help",))
    tool, args = r.data.get("tool"), r.data.get("args") or {}
    label = f"{r.mode}:{r.model}"
    if tool not in TOOL_NAMES:
        return [], HELP, label
    args = {k: str(v) for k, v in args.items() if k in ("client", "invoice_number") and v}
    return [(tool, args)], "", label


# ------------------------------------------------------------------ the turn
async def ask(text: str, session_id: str = "") -> Dict[str, Any]:
    sid = re.sub(r"[^A-Za-z0-9_-]", "", session_id)[:64] or "vs_" + uuid.uuid4().hex[:12]
    ctx = load_session(sid)
    t0 = time.time()
    plan, reply = rule_route(text, ctx)
    router = "rules"
    if plan is None and not reply:
        plan, reply, router = llm_route(text, ctx)
    if plan is None and not reply:
        plan, reply = [], "Sorry, I didn't catch that. " + HELP
    calls: List[Dict[str, Any]] = []
    speech_parts = [reply] if reply else []
    cards: List[Dict[str, Any]] = []
    offer = None
    if plan:
        async with Client(server) as mcp:
            for tool, args in plan:
                c0 = time.time()
                res = await mcp.call_tool(tool, args)
                out = res.structured_content or {}
                calls.append({"tool": tool, "args": args, "ms": int((time.time() - c0) * 1000), "error": bool(res.is_error)})
                if res.is_error or not out:
                    speech_parts.append("Sorry, something went wrong looking that up.")
                    continue
                speech_parts.append(out["speech"])
                cards.append(out["card"])
                data = out.get("data") or {}
                if tool == "who_owes_me":
                    clients = data.get("clients") or []
                    late = [c for c in clients if c.get("overdue", 0) > 0]
                    if len(clients) == 1:
                        ctx["last_client"] = clients[0]["client"]
                    if late:
                        offer = late[0]["client"]
                        ctx["last_client"] = ctx.get("last_client") if len(clients) == 1 else offer
                elif tool == "propose_reminder" and data.get("client"):
                    ctx["last_client"] = data["client"]
    ctx["offer"] = offer
    speech = " ".join(p.strip() for p in speech_parts if p).strip()
    ctx.setdefault("turns", []).append({"you": text[:200], "alexa": speech[:400], "ts": store.now()})
    save_session(sid, ctx)
    return {"session_id": sid, "speech": speech, "cards": cards, "calls": calls, "router": router,
            "ms": int((time.time() - t0) * 1000), "memory": {"last_client": ctx.get("last_client"), "offer": offer}}
