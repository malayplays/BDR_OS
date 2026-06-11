# Devin Session 9 — CRM Scribe

Prereq: Session 3. Attach AGENTS.md §8.

## Task

1. `agents/crm_scribe.py`: trigger on new transcript from CallRecordingAdapter poll; output per AGENTS.md §8 (5-bullet summary, qualification block, next_steps with owner+due, crm_fields_patch).
2. **Split approval gate**: summary/notes auto-approve and write back as CRM activity; next_steps tasks + field patches go to Review Queue (wrong CRM data is worse than no data).
3. Stage advancement is NEVER set by the scribe — it may only propose `s1_candidate: true` (SQL bar met) for human review; `s1_reached`/`s2_reached`/`ad_accepted` events come only from Salesforce sync, never from agent output.
4. Write-back: CRM activity note + (approved) tasks via CRMAdapter, all through policy.check().

## Done = these pass

- `test_split_gate` — one run produces an auto-written note AND queued next_steps; mock CRM `written[]` shows note only until approval.
- `test_stage_never_auto` — transcript with obvious qualification → output has `s1_candidate` flag only; no s1_reached/ad_accepted event exists until CRM sync provides it.
- `test_three_fixture_transcripts` — discovery/objection-heavy/no-show-reschedule transcripts each produce schema-valid output; objection-heavy yields next_step containing objection follow-up.
- `test_field_patch_safety` — patch touching a non-whitelisted CRM field → REQUIRE_APPROVAL policy flag.
- Golden test for AGENTS.md §8.
