# CONNECT_LATER_CHECKLIST.md — Every Stub → Live, Step by Step

Rules for all go-lives: one adapter at a time; **read-only first, writes after 3 clean days**; run the `guardrail_gauntlet` e2e scenario after every swap; keep the mock available via env flag for instant rollback. Prereq for everything: WEEK_ONE_QUESTIONS §5 policy clearance.

## 1. Salesforce (CRMAdapter) — highest value, do first

1. Confirm API access path (own creds / connected app / sandbox first). Get security token or OAuth client.
2. Field mapping workshop (1 hr with ops or an AD): meetings, AD-acceptance source, opp stages S0–S2, Named Target object, activity/last-touch fields → `adapters/salesforce/fieldmap.yaml`.
3. Implement `SalesforceAdapter(CRMAdapter)` — reads only. Env: `ADAPTER_CRM=salesforce`, `SF_*` keys.
4. Validate 1 week: `pull_events` output vs. what I see in SFDC UI daily; dormancy timer spot-checks on 10 contacts; EventLog dedupe holding.
5. Enable writes (activity notes + tasks only) behind approval gate. Provenance notes on every meeting from day one — clawback log live.
6. Acceptance: e2e `happy_week` green against sandbox; zero writes without Verdict; AD-accepted event flows in ≤24h of acceptance.

## 2. Work email (EmailAdapter)

1. Confirm provider (Google vs Microsoft decides: reuse Gmail adapter vs build Graph adapter — budget a Devin session if Microsoft).
2. Org OAuth consent (IT request). Scopes: read + compose ONLY — request send scope only at day-60 graduation, never before.
3. `ADAPTER_EMAIL=work_gmail` (personal adapter stays for nothing — disconnect it). Triage agent live on real inbox, draft-only.
4. Acceptance: triage classifies 1 real day's inbox correctly (spot-check); drafts appear in work Drafts folder; suppression list honored.

## 3. Work calendar (CalendarAdapter)

1. Same OAuth as email. Read + event-create scopes.
2. Show-rate machine live: invites drafted, confirmations queued through Review Queue.
3. Acceptance: one real booked meeting runs the full state machine; capacity feed (PTO/holidays) reaches the cascade.

## 4. Sequencer [stack TBD]

1. Identify tool. **Decision point:** if team runs all outbound through it, copy agent write-back retargets: approved variant → sequencer personalization step, NOT Gmail draft. Add `SequencerAdapter` (new Devin session — interface sketch in ADAPTER_CONTRACTS.md §7).
2. Sequence-state sync → EventLog (`touch_sent` from sequencer, not self-reported).
3. Acceptance: no double-touch possible (sequencer + BDR-OS both emitting to same contact is detected and blocked by policy).

## 5. Clay (EnrichmentAdapter)

1. Seat + table access; choose push (webhook→`/api/webhooks/clay`) or pull. Map signal kinds to my enum; tune strength scoring on 20 known accounts.
2. Acceptance: live signal → research_brief job within one poll cycle; signal quality reviewed after 2 weeks (precision >~60% or tighten threshold).

## 6. Gong (CallRecordingAdapter)

1. Seat + API key per policy. Implement list/transcript pulls.
2. CRM scribe live: every recorded call → notes draft within 1h. Acceptance: 3 real calls scribed, SQL checklist fields match my own judgment.

## 7. Anthropic key → compliant LLM path

If personal key not allowed for company data (WEEK_ONE §5): swap base URL/key in `agents.yaml` to approved provider; agents are provider-agnostic through the base class. Acceptance: golden tests pass on new provider.

## 8. Day-60 graduation (earliest ~mid-September [set DRAFT_ONLY_UNTIL from real day 1])

Graduate ONE job type at a time from draft-only to auto-send, in this order, one week apart, each requiring: 95%+ approval rate over trailing 30 items + Kyle's blessing:
1. 24h confirmations → 2. morning-of confirmations → 3. reconfirm/reschedule → 4. no-show T+10 reschedule. Cold outreach NEVER auto-sends in v1. Update `policy.yaml` whitelist; rate limits stay forever.

## Manual-sync fallback (if policy blocks API access entirely)

Salesforce reports → CSV export (daily, 5 min) → `make import-csv` → EventLog. Drafts copied out by hand from Review Queue. The system still runs; only the adapters thin out. Worth confirming this worst case in week 1 so nothing here is wasted.
