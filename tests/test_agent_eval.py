"""The collections-agent eval cases (eval/agent_cases.json) as a regression gate for the offline planner + guardrails."""
import json
from pathlib import Path

import pytest

from app import paypal_agent, store
from app.demo_data import sample_invoices

CASES = json.loads((Path(__file__).resolve().parent.parent / "eval" / "agent_cases.json").read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_agent_case(case):
    for inv in sample_invoices():
        store.save(inv, seq=store.next_seq())
    r = paypal_agent.run_agent(case["instruction"])
    assert sorted([p.tool, p.invoice_number] for p in r.proposals) == sorted(case["expect"])
    assert all(p.status == "pending" for p in r.proposals)  # proposing never executes
    assert "Nothing is sent until you approve it." in r.answer
