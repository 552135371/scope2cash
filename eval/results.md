# Scope2Cash eval results: main set (eval/briefs.json)

- Run: 2026-10-17 18:25 (UTC+0800)
- LLM: mock (offline heuristics); models fast=`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, main=`nvidia/nemotron-3-super-120b-a12b`, deep=`nvidia/Nemotron-3-Ultra-550b-a55b`
- Search: mock (labelled samples)
- Modes seen in traces: mock
- **Score: 159/159 checks (100%), 11/11 cases perfect**

> Mock mode exercises the pipeline, guardrails and scoring offline. It is a regression gate for the harness and heuristics, not evidence of model quality (the heuristics were written alongside these cases); rerun with `--mode live` once keys are set.

| Case | Score | Type | Hours | Milestones | Conf. | Qs | Budget | Deadline | Rate | Quote | Grounded | Time |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| pilates_ios | 15/15 | ios | 138 | 5 | 0.90 | 2 | - | March | $75/h | $10,350 | yes | 9 ms |
| client_portal_web | 14/14 | web | 92 | 4 | 0.78 | 1 | $12,000 | end of January | $85/h | $7,820 | yes | 1 ms |
| n8n_leads | 14/14 | automation | 31 | 4 | 0.78 | 2 | $800 | - | $75/h | $2,330 | yes | 1 ms |
| bakery_logo | 15/15 | design | 44 | 6 | 0.72 | 3 | - | - | $70/h | $3,080 | yes | 0 ms |
| price_scraper | 13/13 | data | 54 | 5 | 0.66 | 3 | - | - | $65/h | $3,530 | yes | 1 ms |
| vague_uber | 14/14 | web | 61 | 3 | 0.42 | 6 | - | - | $85/h | $5,190 | yes | 0 ms |
| camping_marketplace | 15/15 | web | 163 | 5 | 0.80 | 2 | $3,000 | asap | $85/h | $13,860 | yes | 1 ms |
| cafe_spanish | 16/16 | mobile | 112 | 4 | 0.73 | 3 | $5,000 | - | $82/h | $9,200 | yes | 1 ms |
| dental_ai_receptionist | 15/15 | web | 96 | 4 | 0.66 | 2 | - | 6 weeks | $85/h | $8,160 | yes | 1 ms |
| fitness_flutter | 14/14 | mobile | 133 | 5 | 0.86 | 1 | $20,000 | Q2 | $82/h | $10,920 | yes | 1 ms |
| yoga_wordpress | 14/14 | web | 56 | 3 | 0.61 | 1 | $1,500 | November 15 | $85/h | $4,760 | yes | 0 ms |

## Pass rate by check

| Check | Passed |
|---|---|
| asks_when_vague | 1/1 |
| budget_extracted | 6/6 |
| budget_gap_explained | 4/4 |
| client_name | 6/6 |
| confident_when_clear | 10/10 |
| cover_no_invented_prices | 11/11 |
| deadline_detected | 11/11 |
| deliverables_present | 11/11 |
| flags_scope_risk | 5/5 |
| hours_in_range | 11/11 |
| language_detected | 1/1 |
| milestones_3_to_7 | 11/11 |
| no_giant_milestone | 11/11 |
| no_invented_budget | 5/5 |
| price_has_citations | 11/11 |
| price_sourced_or_labelled | 11/11 |
| project_type | 11/11 |
| quote_arithmetic | 11/11 |
| three_negotiation_tips | 11/11 |

## Failures

- none

## What each check means

- `milestones_3_to_7`, `deliverables_present`, `no_giant_milestone`: the plan is invoiceable milestone by milestone.
- `project_type`, `hours_in_range`: classification and effort sanity against a human-set range.
- `asks_when_vague` / `confident_when_clear`: confidence is calibrated; thin briefs produce questions, not guesses.
- `flags_scope_risk`: scope-creep, timeline, budget or compliance signals become explicit risks.
- `no_invented_budget`, `budget_extracted`, `budget_gap_explained`, `deadline_detected`: only stated facts are used.
- `price_sourced_or_labelled`, `price_has_citations`: no hourly price without a cited source, or it is labelled a placeholder.
- `quote_arithmetic`, `cover_no_invented_prices`: totals add up and the proposal mentions no price beyond the total and rate.
