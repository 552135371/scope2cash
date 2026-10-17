# Scope2Cash

**From a messy client brief to a paid invoice, with an AI agent that keeps a human in the loop.**

Independent developers and designers lose hours (and money) turning "here's what we want..." into a scoped, fairly priced, milestone-based quote and then chasing payment. Scope2Cash does that pipeline end to end:

1. **Scope**: paste an email, chat, or job post; the agent extracts milestones, deliverables, hours, assumptions, risks, and questions to ask the client.
2. **Market price**: Tavily web research and a model-synthesized hourly range, with cited sources.
3. **Quote**: value, market, or premium positioning; milestone line items with due dates; a proposal message; negotiation coaching.
4. **Get paid**: one PayPal invoice draft per milestone. You **approve** each one before it is sent. Statuses sync from PayPal.
5. **Follow up**: overdue invoices get a reminder draft whose tone escalates with days late. You review it before anything goes out.

## How NVIDIA Nemotron and Nebius Token Factory are used
All model calls go through Nebius Token Factory's OpenAI-compatible API (`app/adapters/llm.py`). Each step is routed by difficulty:

| Step | Tier | Default model |
|---|---|---|
| Scope extraction | fast | `nvidia/Nemotron-3-Nano-30B-A3B` |
| Price synthesis, proposal, reminders | main | `nvidia/nemotron-3-super-120b-a12b` |
| Negotiation coaching | deep | `nvidia/Nemotron-3-Ultra-550b-a55b` |

The UI's **Agent trace** panel shows which model handled each step and how long it took.

## How PayPal is used
`app/adapters/payments.py` uses the PayPal Invoicing API v2 in the sandbox: OAuth client credentials, then create draft, send (human-approved), get status, and remind.

## Quick start
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # optional: fill in keys; anything left empty runs in mock mode
uvicorn app.main:app --port 8765
# open http://localhost:8765 and click "Use sample brief"
```
With no keys, every adapter runs in **mock mode**, so the full flow can be demoed offline. Add `NEBIUS_API_KEY`, `TAVILY_API_KEY`, and `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` to switch each adapter to live mode independently. The header badges show which mode each one is in.

## Tests and screenshots
```bash
python -m pytest -q                 # end-to-end API flow (mock mode)
python scripts/screenshots.py out/  # headless walkthrough screenshots (server must be running)
```

## Architecture
```
app/
  main.py            FastAPI routes + static UI
  agent.py           pipeline: scope -> pricing -> quote -> reminder (model routing)
  adapters/llm.py    Nebius Token Factory (Nemotron) | Mock
  adapters/search.py Tavily | Mock
  adapters/payments.py PayPal Invoicing v2 | Mock
  static/            single-page UI (no build step)
```

## License
MIT
