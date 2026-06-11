# Devin Session 4 — Copy Agent

Prereq: Session 3 (AgentBase exists). Attach AGENTS.md §2.

## Task

1. `agents/copy.py` per AGENTS.md §2: CopyPack output (3 email variants ≤90 words w/ distinct mandated angles, call opener ≤40 words + voicemail, LinkedIn note ≤280 chars, per-variant rationale).
2. **Approval gate hard-coded REQUIRED** — assert at class level: `customer_facing=True`; no config can put this agent on the auto lane (mirror of dispatcher boot check).
3. Rejection-feedback loop: last 5 relevant Review Queue rejections/edit-diffs injected into prompt; store edit diffs on approval (`approval.edit_diff`).
4. Refusal path: missing/empty `brief.angle` → no copy, `needs_human_because` set (Golden 3 — generic spray is a bug).
5. Write-back: approved variant → `EmailAdapter.create_draft` (NEVER `send` — verify scope-level too), CRM activity log. Sequencer write-back stubbed `[CONNECT LATER]`.

## Done = these pass

- `test_copypack_schema` — word/char limits enforced via regeneration not truncation; 3 distinct angles present (assert rationale labels).
- `test_gate_unbypassable` — attempts to whitelist `outreach_draft` → boot failure.
- `test_refuses_without_angle` — Golden 3 behavior.
- `test_rejection_feedback_in_prompt` — seed 2 rejections; assert their text reaches the prompt assembly.
- `test_draft_not_send` — write-back calls `create_draft`; `send` path raises if reached during DRAFT_ONLY.
- Golden tests for AGENTS.md §2 examples (deterministic mock + live-LLM flag).
