# Devin Session 11 — Reporting Agent

Prereq: Sessions 0–2 (needs FunnelState/Plan/rates). Attach AGENTS.md §10.

## Task

1. `agents/reporting.py`: Friday 15:00 + month-end triggers.
2. Personal recap (auto-approved, archived to `reports/` table + markdown export): plan vs. actual by stage, rate trends w/ confidence, wins, at_risk flags, next week's plan summary. Numbers come straight from FunnelState/Plan — the LLM narrates, it never computes (assert: every number in output exists in input payload).
3. Manager update draft for Kyle (approval REQUIRED): short, outcome-first, format per AGENTS.md §10 golden `[CONFIRM WEEK 1: Kyle's preferred format]`; includes honest risks + one "need from you". Write-back: Gmail draft.
4. Month-end variant adds: goal pace vs. annual, rate deltas vs. benchmarks, cold-start exit progress.

## Done = these pass

- `test_numbers_never_hallucinated` — every numeric token in rendered output matches a value in the input payload (regex-extract and assert).
- `test_recap_auto_manager_gated` — recap auto-archives; manager draft sits in Review Queue, then lands as Gmail draft via mock.
- `test_friday_and_monthend_triggers` — fake clock fires both correctly.
- `test_at_risk_honesty` — at_risk=true funnel → manager draft contains the risk; suppressing it is impossible via prompt (assert string present).
- Golden test for AGENTS.md §10.
