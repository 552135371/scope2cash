"""Self-hosted MCP server for Alexa+ (and any MCP client): Scope2Cash's "who owes me" money skills.

Transport: Streamable HTTP at /mcp (see alexa/asgi.py). Protocol: negotiated by the official MCP Python SDK; the
2025-11-25 revision is accepted on the initialize handshake (the hackathon's minimum), newer revisions too.
Read tools are annotated read-only; `propose_reminder` only QUEUES a proposal for human approval (never sends).
"""
from __future__ import annotations

from typing import Any, Dict

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from . import skills

INSTRUCTIONS = """Scope2Cash answers a freelancer's money questions by voice: who still owes them, what's coming in this
week, and what is waiting for their approval. Read the `speech` field aloud as-is (it is written for voice).
propose_reminder never sends anything: it queues a PayPal reminder that the freelancer must approve in the Scope2Cash
dashboard. Never tell the user a reminder was sent; say it is waiting for their approval."""

server = MCPServer(name="scope2cash", title="Scope2Cash — who owes me?", version="0.1.0", instructions=INSTRUCTIONS,
                   website_url="https://github.com/552135371/scope2cash")

class Card(BaseModel):
    title: str
    rows: list[Dict[str, Any]] = Field(default_factory=list)
    footer: str = ""


class VoiceAnswer(BaseModel):
    """What every Scope2Cash tool returns."""
    speech: str = Field(description="One or two short sentences written to be read aloud as-is")
    card: Card = Field(description="Small display card for screen devices such as Echo Show")
    data: Dict[str, Any] = Field(default_factory=dict, description="Structured details for agents")


def _answer(d: Dict[str, Any]) -> VoiceAnswer:
    return VoiceAnswer.model_validate(d)


READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
QUEUE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)


@server.tool(title="Who still owes me?", annotations=READ)
def who_owes_me(client: str = "") -> VoiceAnswer:
    """Who still owes the freelancer money, most overdue first, with amounts and days late.
    Pass `client` (a spoken name such as "Harbor Yoga" or just "harbor") to ask about one client."""
    return _answer(skills.who_owes_me(client))


@server.tool(title="Cash flow this week", annotations=READ)
def cashflow_this_week() -> VoiceAnswer:
    """Money collected this week (Monday to Sunday), money still due this week, and the amount already overdue."""
    return _answer(skills.cashflow_this_week())


@server.tool(title="Waiting for my approval", annotations=READ)
def pending_approvals() -> VoiceAnswer:
    """Reminders and collaborator payouts that are queued and waiting for the freelancer's approval."""
    return _answer(skills.pending_approvals())


@server.tool(title="Queue a payment reminder (needs approval)", annotations=QUEUE)
def propose_reminder(client: str = "", invoice_number: str = "") -> VoiceAnswer:
    """Queue a PayPal payment reminder for a client's unpaid invoices (or one invoice number; with neither, for every
    overdue invoice). This does NOT send anything: the freelancer approves or rejects it in the Scope2Cash dashboard.
    Calling it twice does not queue duplicates."""
    return _answer(skills.propose_reminder(client=client, invoice_number=invoice_number))


TOOL_NAMES = ("who_owes_me", "cashflow_this_week", "pending_approvals", "propose_reminder")
