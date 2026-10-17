# Scope2Cash eval results: hold-out set (eval/briefs_holdout.json, never used to tune the heuristics)

- Run: 2026-10-17 20:30 (UTC+0800)
- LLM: live (DeepSeek API); models fast=`deepseek-flash`, main=`deepseek-flash`, deep=`deepseek-v4-pro`
- Search: mock (labelled samples)
- Modes seen in traces: live, mock
- **Score: 55/55 checks (100%), 4/4 cases perfect**

| Case | Score | Type | Hours | Milestones | Conf. | Qs | Budget | Deadline | Rate | Quote | Grounded | Time |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ho_shopify_migration | 13/13 | web | 117 | 5 | 0.70 | 9 | - | Before Black Friday (late November 2026) | $85/h | $9,950 | yes | 36366 ms |
| ho_german_booking | 14/14 | web | 116 | 6 | 0.55 | 10 | - | - | $85/h | $9,860 | yes | 39604 ms |
| ho_vague_ai | 13/13 | automation | 48 | 3 | 0.15 | 10 | - | - | $75/h | $3,600 | yes | 32639 ms |
| ho_zapier_invoices | 15/15 | automation | 13 | 4 | 0.70 | 8 | $500 | - | $75/h | $980 | yes | 50004 ms |

## Pass rate by check

| Check | Passed |
|---|---|
| asks_when_vague | 1/1 |
| budget_extracted | 1/1 |
| budget_gap_explained | 1/1 |
| confident_when_clear | 3/3 |
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

- none

## What each check means

- `milestones_3_to_7`, `deliverables_present`, `no_giant_milestone`: the plan is invoiceable milestone by milestone.
- `project_type`, `hours_in_range`: classification and effort sanity against a human-set range.
- `asks_when_vague` / `confident_when_clear`: confidence is calibrated; thin briefs produce questions, not guesses.
- `flags_scope_risk`: scope-creep, timeline, budget or compliance signals become explicit risks.
- `no_invented_budget`, `budget_extracted`, `budget_gap_explained`, `deadline_detected`: only stated facts are used.
- `price_sourced_or_labelled`, `price_has_citations`: no hourly price without a cited source, or it is labelled a placeholder.
- `quote_arithmetic`, `cover_no_invented_prices`: totals add up and the proposal mentions no price beyond the total and rate.

## Token usage (DeepSeek API)

- `deepseek-flash`: 12 calls, 7568 prompt (1152 cached) + 8885 completion (3954 reasoning) tokens
- `deepseek-v4-pro`: 4 calls, 1837 prompt (0 cached) + 8325 completion (7729 reasoning) tokens
- Estimated cost at peak rates: $0.0480
