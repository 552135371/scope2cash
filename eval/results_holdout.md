# Scope2Cash eval results: hold-out set (eval/briefs_holdout.json, never used to tune the heuristics)

- Run: 2026-10-17 15:32 (UTC+0800)
- LLM: mock (offline heuristics); models fast=`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, main=`nvidia/nemotron-3-super-120b-a12b`, deep=`nvidia/Nemotron-3-Ultra-550b-a55b`
- Search: mock (labelled samples)
- Modes seen in traces: mock
- **Score: 54/55 checks (98%), 3/4 cases perfect**

> Mock mode exercises the pipeline, guardrails and scoring offline. It is a regression gate for the harness and heuristics, not evidence of model quality (these hold-out briefs were written after the heuristics and never used to tune them); rerun with `--mode live` once keys are set.

| Case | Score | Type | Hours | Milestones | Conf. | Qs | Budget | Deadline | Rate | Quote | Grounded | Time |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ho_shopify_migration | 13/13 | web | 70 | 3 | 0.66 | 2 | - | black friday | $85/h | $5,950 | yes | 7 ms |
| ho_german_booking | 13/14 | web | 43 | 3 | 0.45 | 6 | - | - | $85/h | $3,660 | yes | 1 ms |
| ho_vague_ai | 13/13 | web | 55 | 4 | 0.43 | 6 | - | - | $85/h | $4,680 | yes | 0 ms |
| ho_zapier_invoices | 15/15 | automation | 32 | 4 | 0.73 | 2 | $500 | - | $75/h | $2,410 | yes | 0 ms |

## Pass rate by check

| Check | Passed |
|---|---|
| asks_when_vague | 1/1 |
| budget_extracted | 1/1 |
| budget_gap_explained | 1/1 |
| confident_when_clear | 2/3 |
| cover_no_invented_prices | 4/4 |
| deadline_detected | 4/4 |
| deliverables_present | 4/4 |
| flags_scope_risk | 1/1 |
| hours_in_range | 4/4 |
| language_detected | 1/1 |
| milestones_3_to_7 | 4/4 |
| no_giant_milestone | 4/4 |
| no_invented_budget | 3/3 |
| price_has_citations | 4/4 |
| price_sourced_or_labelled | 4/4 |
| project_type | 4/4 |
| quote_arithmetic | 4/4 |
| three_negotiation_tips | 4/4 |

## Failures

- **ho_german_booking** `confident_when_clear`: confidence 0.45

## What each check means

- `milestones_3_to_7`, `deliverables_present`, `no_giant_milestone`: the plan is invoiceable milestone by milestone.
- `project_type`, `hours_in_range`: classification and effort sanity against a human-set range.
- `asks_when_vague` / `confident_when_clear`: confidence is calibrated; thin briefs produce questions, not guesses.
- `flags_scope_risk`: scope-creep, timeline, budget or compliance signals become explicit risks.
- `no_invented_budget`, `budget_extracted`, `budget_gap_explained`, `deadline_detected`: only stated facts are used.
- `price_sourced_or_labelled`, `price_has_citations`: no hourly price without a cited source, or it is labelled a placeholder.
- `quote_arithmetic`, `cover_no_invented_prices`: totals add up and the proposal mentions no price beyond the total and rate.
