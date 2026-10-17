from app import evals, heuristics as H


def test_eval_set_runs_in_mock_mode_and_scores():
    results = evals.run()
    card = evals.scorecard(results)
    assert card["cases"] >= 8 and all(not r.error for r in results)
    assert card["pass_rate"] >= 0.9, evals.to_markdown(results, card)
    md = evals.to_markdown(results, card)
    assert "| Case | Score |" in md and "mock" in md


def test_mock_outputs_are_brief_sensitive():
    """Different briefs must not produce the same canned plan."""
    briefs = {c["id"]: c["brief"] for c in evals.load_briefs()}
    scopes = {k: H.mock_scope(v) for k, v in briefs.items()}
    titles = {tuple(m["title"] for m in s["milestones"]) for s in scopes.values()}
    hours = {round(sum(m["est_hours"] for m in s["milestones"])) for s in scopes.values()}
    assert len(titles) >= 6 and len(hours) >= 8
    assert scopes["vague_uber"]["confidence"] < scopes["pilates_ios"]["confidence"]
    assert scopes["cafe_spanish"]["language"] == "es"


def test_budget_and_deadline_extraction():
    assert H.extract_budget("Budget is flexible") is None
    assert H.extract_budget("Budget $15-20k, launch in Q2") == 20000
    assert H.extract_budget("Presupuesto: 5.000 euros") == 5000
    assert H.extract_budget("we have $2,000 total") == 2000
    assert H.extract_deadline("ready by November 15 please") == "november 15"
    assert H.extract_deadline("We'd like to launch within 6 weeks.") == "6 weeks"
    assert H.extract_deadline("no rush at all") is None


def test_price_grounding_guardrail():
    snippets = ["Rates are $40 to $80 per hour.", "A site costs $2,000-$6,000."]
    hourly, project = H.money_figures(snippets)
    assert hourly == [40, 80] and project == [2000, 6000]
    assert H.ground_pricing(45, 75, snippets)[2:4] == (True, ["$40/h", "$80/h", "$2,000", "$6,000"])
    lo, hi, grounded, _, note = H.ground_pricing(150, 300, snippets)
    assert grounded and hi <= 80 and "clamped" in note
    assert H.ground_pricing(50, 90, ["no numbers here"])[2] is False
