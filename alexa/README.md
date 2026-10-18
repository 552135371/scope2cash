# Scope2Cash for Alexa+ — "Alexa, who still owes me money?"

A voice add-on for [Scope2Cash](../README.md), the freelancer quote-to-cash app. It answers the money questions a
freelancer actually asks out loud (who owes me, what's coming in this week, what's waiting for me) and can **queue**
payment reminders. It never sends one on its own: every reminder waits for a tap on **Approve** in the dashboard.

Built for the Alexa+ track of *Build, Ship, Shape: Amazon Developer Hackathon* (2026). Two parts:

| Part | Where | What |
|---|---|---|
| Self-hosted **MCP server** | `POST/GET/DELETE /mcp` (`alexa/mcp_server.py`) | Official MCP Python SDK (`mcp==2.3.0`), **Streamable HTTP**, protocol **2025-11-25** negotiated on the initialize handshake (newer revisions also accepted). 4 tools, typed output schema, tool annotations. |
| Simulated **Alexa+ experience** | `GET /alexa` (`alexa/static/alexa.html`, `alexa/assistant.py`) | Push-to-talk (Web Speech API), spoken answers, Echo-Show-style display cards, conversation memory that survives reloads. It is a real MCP *client*: every answer is a `tools/call` on the server above. |

## Tools

| Tool | Kind | Example utterance |
|---|---|---|
| `who_owes_me(client?)` | read-only | "Who still owes me money?" · "How much does Harbor Yoga owe?" |
| `cashflow_this_week()` | read-only | "What's coming in this week?" |
| `pending_approvals()` | read-only | "What's waiting for my approval?" |
| `propose_reminder(client?, invoice_number?)` | queues only, idempotent | "Remind Lumen about their invoice." · "Yes" (after Alexa offers) |

Every tool returns `{speech, card, data}`: `speech` is written to be read aloud as-is (short, no Markdown), `card` is
a small display card for screen devices, `data` is structured detail for agents.

### Safety model
- Read tools are annotated `readOnlyHint: true`; `propose_reminder` is `destructiveHint: false`, `idempotentHint: true`.
- `propose_reminder` writes one `pending` row into the same approval queue the dashboard's PayPal collections agent
  uses. The guardrail (`app.paypal_agent.guard_action`: only Scope2Cash-created, unpaid invoices; sandbox recipients on
  the public demo) runs when it is queued **and again** when the human approves.
- "Send it" / "approve it" by voice is refused on purpose; the assistant reads back what's waiting instead.
- `/mcp` keeps DNS-rebinding protection on (only our own host names are accepted; set `MCP_ALLOWED_HOSTS`).
- The public demo rate limit (30 AI requests per IP per hour) also covers `/api/alexa/ask`.

### Conversation memory
`alexa/assistant.py` keeps a small per-session memory in SQLite: the last client discussed and the offer Alexa just
made. So "Who still owes me?" → "Yes" queues the reminder Alexa offered, and "What about Lumen?" → "Remind them"
knows who "them" is, also after a page reload. Everyday phrasings are routed by rules (free, instant); anything else
goes to the configured LLM (DeepSeek on the hosted demo) as a JSON intent classifier, with the rules as fallback.

## Run it

```bash
pip install -r requirements.txt -r requirements-alexa.txt
pip install --no-deps -r requirements-paypal-toolkit.txt
SCOPE2CASH_AUTOSEED=1 uvicorn alexa.asgi:app --port 8000   # no keys needed: mock PayPal + offline heuristics
```
- Voice simulator: http://localhost:8000/alexa (Chrome/Edge for the mic; typing works everywhere)
- Dashboard (approve the queued reminders): http://localhost:8000/
- MCP endpoint: http://localhost:8000/mcp — check it like an MCP host would:
  ```bash
  python -m alexa.mcp_check http://localhost:8000/mcp            # read-only: lists tools, asks who owes me
  python -m alexa.mcp_check http://localhost:8000/mcp --propose harbor   # also queues (not sends) a reminder
  ```
  or with the MCP Inspector: `npx @modelcontextprotocol/inspector` → Streamable HTTP → `http://localhost:8000/mcp`.

The Docker image on this branch starts `alexa.asgi:app` (set `ASGI_APP=app.main:app` for the core app only).

## Tests
`pytest tests/test_alexa.py` — 13 tests: the 2025-11-25 handshake over Streamable HTTP, `tools/list` annotations,
`tools/call`, foreign-Host rejection, client-name matching, reminders queued but never sent (and idempotent), a
voice-queued reminder approved through the existing dashboard endpoint, and a multi-turn conversation with memory.
