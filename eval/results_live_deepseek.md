# Scope2Cash eval results: main set (eval/briefs.json)

- Run: 2026-10-17 20:37 (UTC+0800)
- LLM: live (DeepSeek API); models fast=`deepseek-flash`, main=`deepseek-flash`, deep=`deepseek-v4-pro`
- Search: mock (labelled samples)
- Modes seen in traces: live, mock
- **Score: 155/159 checks (98%), 7/11 cases perfect**

| Case | Score | Type | Hours | Milestones | Conf. | Qs | Budget | Deadline | Rate | Quote | Grounded | Time |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| pilates_ios | 14/15 | ios | 222 | 5 | 0.70 | 8 | - | Before spring promo in March | $82/h | $18,210 | yes | 33016 ms |
| client_portal_web | 14/14 | web | 130 | 6 | 0.60 | 10 | $12,000 | End of January 2027 | $85/h | $11,050 | yes | 37881 ms |
| n8n_leads | 14/14 | automation | 14 | 4 | 0.72 | 8 | $800 | - | $75/h | $1,060 | yes | 52400 ms |
| bakery_logo | 14/15 | design | 39 | 5 | 0.72 | 7 | - | - | $70/h | $2,730 | yes | 30013 ms |
| price_scraper | 13/13 | data | 58 | 5 | 0.60 | 10 | - | - | $65/h | $3,770 | yes | 28765 ms |
| vague_uber | 13/14 | mobile | 342 | 5 | 0.20 | 11 | - | - | $82/h | $28,060 | yes | 36376 ms |
| camping_marketplace | 14/15 | web | 207 | 6 | 0.40 | 10 | $3,000 | asap | $85/h | $17,610 | yes | 223583 ms |
| cafe_spanish | 16/16 | mobile | 112 | 5 | 0.55 | 10 | $5,000 | - | $82/h | $9,210 | yes | 61272 ms |
| dental_ai_receptionist | 15/15 | web | 80 | 5 | 0.68 | 12 | - | Within 6 weeks of kickoff | $85/h | $6,800 | yes | 34128 ms |
| fitness_flutter | 14/14 | mobile | 280 | 6 | 0.72 | 10 | $20,000 | Q2 | $70/h | $19,600 | yes | 45803 ms |
| yoga_wordpress | 14/14 | web | 26 | 5 | 0.78 | 8 | $1,500 | November 15 | $85/h | $2,180 | yes | 31969 ms |

## Pass rate by check

| Check | Passed |
|---|---|
| asks_when_vague | 1/1 |
| budget_extracted | 6/6 |
| budget_gap_explained | 4/4 |
| client_name | 6/6 |
| confident_when_clear | 9/10 |
| cover_no_invented_prices | 11/11 |
| deadline_detected | 11/11 |
| deliverables_present | 11/11 |
| flags_scope_risk | 4/5 |
| hours_in_range | 9/11 |
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

- **pilates_ios** `hours_in_range`: 222 h, want 80-220
- **bakery_logo** `flags_scope_risk`: Hand-drawn style is subjective; feedback may require additional revision rounds beyond the included round.; Font choices may require paid licensing or substitutions if the client wants specific typefa
- **vague_uber** `hours_in_range`: 342 h, want 30-250
- **camping_marketplace** `confident_when_clear`: confidence 0.4

## What each check means

- `milestones_3_to_7`, `deliverables_present`, `no_giant_milestone`: the plan is invoiceable milestone by milestone.
- `project_type`, `hours_in_range`: classification and effort sanity against a human-set range.
- `asks_when_vague` / `confident_when_clear`: confidence is calibrated; thin briefs produce questions, not guesses.
- `flags_scope_risk`: scope-creep, timeline, budget or compliance signals become explicit risks.
- `no_invented_budget`, `budget_extracted`, `budget_gap_explained`, `deadline_detected`: only stated facts are used.
- `price_sourced_or_labelled`, `price_has_citations`: no hourly price without a cited source, or it is labelled a placeholder.
- `quote_arithmetic`, `cover_no_invented_prices`: totals add up and the proposal mentions no price beyond the total and rate.

## Token usage (DeepSeek API)

- `deepseek-flash`: 34 calls, 22550 prompt (5376 cached) + 32971 completion (18686 reasoning) tokens
- `deepseek-v4-pro`: 11 calls, 5098 prompt (0 cached) + 18644 completion (17109 reasoning) tokens
- Estimated cost at peak rates: $0.1253
