# Devin Session 3 — Research Brief Agent

Prereq: Sessions 0–2. Attach AGENTS.md (§1 is the contract) + ADAPTER_CONTRACTS.md.

## Task

1. `agents/base.py` (first agent session builds the shared base): `AgentBase.run(job) -> AgentOutput` with prompt assembly (system + voice_profile + value_props + job input), Anthropic API client (model from `agents.yaml`, env key), JSON-schema-validated output parsing with one retry on parse failure, `confidence` + `needs_human_because` on every output, token/cost logging per run.
2. `agents/research_brief.py` per AGENTS.md §1: inputs, Brief output schema (≤200 words enforced — truncation is a test failure, regeneration on overflow), auto-approve gate, write-back = CRM note + chain `outreach_draft` job.
3. Strategic-account behavior: brief generated, outreach chain suppressed, `needs_human_because` set (Golden 3).
4. Trigger wiring: signal ≥0.5 from EnrichmentAdapter poll → job created.

## Done = these pass

- `test_base_output_validation` — malformed LLM JSON → one retry → hard fail to `Job.failed` with raw output preserved.
- `test_brief_schema_and_length` — fixture signal (hiring_surge) → valid Brief, ≤200 words, why_now references the signal evidence string.
- `test_strategic_suppression` — strategic-tier account → no outreach_draft job created, needs_human_because set.
- `test_chain` — standard account → outreach_draft job exists, pending, carries brief in input_payload.
- `test_writeback_via_policy` — CRM note write passes through `policy.check()`; assert mock CRM `written[]` contains the note.
- Golden tests: all 3 golden examples from AGENTS.md §1 as fixture-driven assertions on structure + key content (use deterministic LLM mock for CI; live-LLM variant behind `RUN_LIVE_LLM=1`).
