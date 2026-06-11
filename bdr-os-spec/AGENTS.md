# AGENTS.md — Agent Roster & I/O Contracts

Ten agents. Every agent implements `run(job: Job) -> AgentOutput` and **never touches an adapter write directly** — output goes to the approval gate, write-back happens in the executor after `policy.check()`.

**60-day guardrail, baked into every customer-facing agent below:** output is ALWAYS a draft. `approval_gate: required` is hard-coded for job types tagged `customer_facing` while `now < DRAFT_ONLY_UNTIL`. Strategic-tier accounts: agents may *prepare* materials but write-back is blocked; job converts to manual-only. Rate limits checked at write-back, not draft time (drafting is free; sending/queueing is what's limited).

Shared conventions: agents receive `voice_profile.md` (Malay's writing voice — built in Session 0 from samples, refined from Review Queue edit diffs) and `value_props.yaml` (Cognition/Devin/Windsurf positioning `[CONFIRM WEEK 1 — sales-approved messaging]`). Every output includes `confidence: 0–1` and `needs_human_because: str|null`; dispatcher uses these for queue grouping.

**Comp-aware behavior (COMP_MODEL.md):** EV is in Δpoints — persona arbitrage means agents target Manager+ (VP+ preferred) by default; IC-persona outreach requires explicit override. Every meeting carries a clawback-protection provenance record (outbound touch evidence, Named-Target/dormancy check, duplicate scan) written to Salesforce. The 3 Why's (Anything / Now / Windsurf-Devin) structure all qualification artifacts.

---

## 1. research_brief — Research Brief Agent (create)

| | |
|---|---|
| Trigger | New signal from EnrichmentAdapter ≥0.5 strength; account added to territory; manual |
| Inputs | `{account: Account, contacts: Contact[], signal: Signal\|null, recent_activity: Activity[], company: CompanyProfile, dormancy_report: per-contact days_dormant}` |
| Output | `Brief{company_snapshot(3 bullets), why_now(signal-anchored, 2 sentences), who_to_contact(ranked 1–3 w/ reason — **ranked by persona points × reachability; VP+ first, never down-market**), angle(1 sentence connecting signal→Devin/Windsurf value), landmines(competitors in stack, recent bad press, existing Cognition relationship), compound_candidate: bool (net-new multi-thread potential → 20-pt play), requalified_contacts: [≥120d dormant — net-new credit available]}` ≤200 words total |
| Approval | Auto-approved (internal artifact) |
| Write-back | CRM note on account: `[BDR-OS] Research brief` + brief; chains an `outreach_draft` job |

**Golden 1** — in: Vercel-like co, signal `hiring_surge` ("27 open backend roles, +40% eng headcount QoQ") → out: why_now: "Eng team growing 40% QoQ with 27 open backend reqs — onboarding/velocity pain is now, and every new hire makes codebase ramp-up costlier. Devin absorbs the grunt-work backlog while seniors onboard juniors." angle: "Frame Devin as headcount-multiplier during the hiring crunch, not headcount replacement." landmines: "Already pay for Copilot Enterprise — don't lead with autocomplete comparison."
**Golden 2** — in: signal `eng_leadership_change` (new VP Eng, ex-Stripe) → out: who_to_contact ranks the new VP #1: "New leaders audit tooling in first 90 days; ex-Stripe = high bar for dev productivity tooling." angle references first-90-days audit window.
**Golden 3 (negative)** — in: account tier=strategic → out: brief generated, but `needs_human_because: "strategic account — outreach chain suppressed, manual plan required"`; no outreach_draft job chained.

## 2. copy — Copy Agent (create)

| | |
|---|---|
| Trigger | Approved/auto research_brief; sequence step due; signal-triggered touch |
| Inputs | `{brief: Brief, contact: Contact, channel_plan: [email,call,linkedin], voice_profile, value_props, thread_history: Message[]\|null, rejection_feedback: str[] (last 5 relevant)}` |
| Output | `CopyPack{email_variants: 3 (distinct angles: signal-direct / problem-led / social-proof; ≤90 words, 1 CTA, no images/links beyond 1), call_opener: ≤40 words + voicemail line, linkedin_note: ≤280 chars, rationale: 1 line per variant}` |
| Approval | **REQUIRED — customer-facing. No auto lane, ever, in v1.** |
| Write-back | Approved variant → `EmailAdapter.create_draft` (draft folder only) or sequencer task `[CONNECT LATER]`; CRM activity logged |

**Golden 1** — brief w/ hiring_surge → variant A (signal-direct): "Saw the 27 backend openings — congrats on the growth. Usually that means seniors spend the next two quarters onboarding instead of shipping. Teams like [proof] are using Devin to keep velocity flat through hiring waves. Worth 15 minutes to see how?"
**Golden 2** — reply-thread input (mild objection "we have Copilot") → variant reframes: autocomplete vs. delegating whole tickets; CTA = side-by-side on one real ticket from their backlog.
**Golden 3 (negative)** — input missing brief.angle → agent returns `needs_human_because: "no angle — refusing to write generic spray"` rather than producing filler. Generic copy is a bug, not a fallback.

## 3. inbox_triage — Inbox Triage Agent (convert)

| | |
|---|---|
| Trigger | Every inbound via `watch_replies` (continuous) |
| Inputs | `{message: InboundMessage, thread: Thread, contact, account, autoreply_info}` |
| Output | `Triage{classification: positive\|objection\|question\|referral\|ooo\|unsubscribe\|bounce\|spam, urgency: now\|today\|this_week, extracted: {objection_type?, referred_to?, ooo_return_date?}, next_job: jobtype_to_chain}` |
| Approval | Auto (classification only). Chained jobs carry their own gates. **SLA: positive reply → `book_response` job created within minutes, due_at = +4h (Rule 5 escalation).** |
| Write-back | EventLog (`reply_received`/`positive_reply`); CRM activity; unsubscribe → suppression list immediately, no approval needed (compliance overrides draft-only in the *suppressive* direction only) |

**Golden 1** — "This is interesting, can you send more info?" → `positive, now`, chains `book_response` (NOT a literature-send: more-info asks are booking opportunities). **Golden 2** — OOO until July 28 → `ooo`, chains `reschedule_touch` dated July 29, pauses sequence. **Golden 3** — "take me off your list" → `unsubscribe`, immediate suppression, sequence killed, no recovery jobs ever.

## 4. book_response — Speed-to-Book (convert) *(triage's chained twin — top-priority automation)*

| | |
|---|---|
| Trigger | `positive_reply` event |
| Inputs | `{thread, triage, slots: find_slots(prefer ≤3–4 days out, 2–3 options), contact}` |
| Output | `BookDraft{reply_in_thread: ≤60 words, acknowledges their words, offers 2 concrete times + link, books in SAME thread}` |
| Approval | **REQUIRED (customer-facing).** Surfaced at top of Review Queue with countdown vs. 4h SLA — these jump every queue. |
| Write-back | `create_draft` in-thread; on actual booking (event detected) → show-rate machine takes over |

**Golden 1** — "Sure, how does next week look?" → "Great — rather than next week, I have Thu 2:00 or Fri 10:30 (15 min) if either works; sooner usually beats calendar-tetris. Grabbing one: [link]." (pulls toward <4 days, gives agency, one link). **Golden 2** — positive but asks for info first → one-line value tease + "easiest is 15 min where I show it on a ticket like yours — Thu 2:00 or Fri 10:30?"

## 5. show_rate_machine — Show-Rate Machine (hold) — explicit state machine

States: `BOOKED → INVITE_SENT → ACCEPTED → CONFIRMED_24H → CONFIRMED_AM → HELD | NO_SHOW → RECOVERY(→ no_show_recovery agent)`

**Comp note: no-show = ZERO credit (COMP_MODEL.md §3).** A protected VP meeting is worth ~4.5 pts of EV; this machine is the highest-$-density component in the system. Post-HELD, the machine also tracks `occurred → ad_accepted` and nudges if AD acceptance lags >3d.

| Transition | Trigger | Job emitted (all customer-facing ⇒ approval REQUIRED) |
|---|---|---|
| BOOKED→INVITE_SENT | booking detected | instant calendar invite, **value-framed agenda in body** ("What you'll see: how [signal-relevant thing] works on a ticket like yours"), includes "feel free to bring a colleague" |
| INVITE_SENT→risk | not accepted in 24h | `reconfirm` draft ("making sure this still works — or here are two other times") |
| any→risk | OOO autoreply from attendee | `reschedule` draft with 2 slots |
| ACCEPTED→CONFIRMED_24H | T−24h timer | content-bearing reminder: "the one thing I'll show you about [their signal]" — never naked "looking forward to it" |
| CONFIRMED_24H→CONFIRMED_AM | morning-of timer | one-line relevant proof point |
| slot science | booking >4 days out at creation | `pull_in_offer` draft ("a slot opened Thursday — want it?") |
| no attendance signal +10min | calendar + meeting-link heuristics | mark `meeting_no_show` → hand off to no_show_recovery |

Inputs: `{event: CalEvent, invite_status, brief, signal, thread}`. Output per job: the draft + state transition record. Write-back: drafts via Email/CalendarAdapter; every transition → EventLog (this is what makes show-rate measurable).

**Golden (24h confirm)** — meeting from hiring-surge signal → "Tomorrow at 2 I'll show you the one thing most teams miss: how Devin picks up a real ticket from your backlog end-to-end — bring one if you want to see it live. Feel free to bring a colleague."

## 6. no_show_recovery — No-Show Recovery Agent (hold)

| | |
|---|---|
| Trigger | `meeting_no_show` event |
| Inputs | `{meeting, contact, thread, prior_confirmations, slots(2, ≤3 days out)}` |
| Output | T+10min: polite zero-guilt reschedule w/ two one-click times. Then 3-touch sequence: +1d (value nudge: new proof point), +3d (different channel — call task), +7d (graceful close + open door). Sequence killed instantly on any reply. |
| Approval | REQUIRED per touch (batched: the 3-touch sequence approves as one unit, sends are scheduled) |
| Write-back | drafts + CRM tasks; `meeting_rescheduled` or sequence-exhausted → EventLog |

**Golden (T+10min)** — "No worries at all — calendars happen. Two quick options to regrab 15 min: [Thu 2:00] [Fri 10:30]. Same agenda: the [signal] walkthrough." **Golden (negative)** — contact replied "running late, join in 10"? → recovery suppressed, `meeting_held` path; agent checks thread before drafting.

## 7. call_prep — Call Prep Agent (create/hold)

| | |
|---|---|
| Trigger | T−30min before any call block or booked meeting |
| Inputs | `{meeting/call_block, brief, thread_history, transcript_refs (past calls), signal, funnel context}` |
| Output | 5-min pre-call card: `{who(2 lines), why_now(1), last_interaction(1), goal_of_call(1), likely_objections(2, w/ responses), the_one_thing_to_show}` — fits on phone screen |
| Approval | Auto (internal) |
| Write-back | none (ephemeral, attached to Today screen job) |

**Golden** — pre-meeting card where last_interaction says: "He replied 'we have Copilot' on 6/2 — you reframed to ticket-delegation; open by picking that thread up, don't restart pitch."

## 8. crm_scribe — CRM Scribe (any stage)

| | |
|---|---|
| Trigger | New transcript from CallRecordingAdapter; manual voice-note `[CONNECT LATER — Gong]` |
| Inputs | `{transcript, meeting, account, contact}` |
| Output | `{summary: 5 bullets, sql_checklist: {icp_fit, relevant_title, expressed_pain, confirmed_need, next_steps_agreed, eval_timeline_6mo, facts_verified} (the S1 bar from COMP_MODEL.md §4), three_whys: {anything, now, windsurf_devin}, next_steps: [{action, owner, due}], crm_fields_patch, provenance_note (outbound touch evidence for clawback log)}` |
| Approval | **Light gate:** auto-approve summary/notes/provenance; next_steps + field patches REQUIRED (wrong CRM data is worse than no data — and CRM errors are a clawback risk) |
| Write-back | Salesforce activity note (incl. provenance) + tasks for approved next_steps |

**Golden** — discovery transcript → sql_checklist 6/7 true (eval_timeline unconfirmed → next_step "confirm eval timeline"), three_whys filled from prospect's own words, `s1_candidate: true` for review — scribe never advances stage on its own; AD acceptance and S1/S2 events come only from CRM sync.

## 9. pipeline_hygiene — Pipeline Hygiene Agent (any)

| | |
|---|---|
| Trigger | Nightly 02:30 |
| Inputs | full sweep: open CRM tasks, threads awaiting reply >5d, booked meetings missing invites, sequences stalled, contacts touched ≥3× w/ no reply in 21d, **120-day dormancy timer** (contacts crossing ≥120d at engaged/install accounts → `dormancy_requalified` event + research_brief job — this sweep is a point source, not cleanup), **credit pipeline audit** (occurred meetings awaiting AD acceptance >3d → nudge task; missing provenance on any booked meeting → fix-before-meeting flag), duplicate-meeting check within eligibility window |
| Output | `HygieneReport{auto_fixed: [], proposed: [{issue, proposed_job, evidence}]}` |
| Approval | Auto-fix only data-hygiene whitelist (close own stale tasks, fix missing EventLog links). Anything generating a customer touch → proposes jobs into normal gated flow |
| Write-back | CRM task updates; jobs created |

**Golden** — finds booked meeting with invite never accepted, 3 days out, no reconfirm sent → proposes `reconfirm` job flagged "show-rate risk." **Golden (negative)** — finds stale thread on strategic account → proposes manual-only task, never a draft job.

## 10. reporting — Reporting Agent

| | |
|---|---|
| Trigger | Friday 15:00; month-end |
| Inputs | `{funnel_state (points: credited/pending/projected), plan vs. actual, rates w/ trends, persona_mix, earnings projection, promotion_scorecard, wins[], at_risk flags, next_week_plan}` |
| Output | (a) personal recap (points + earnings + persona mix, honest gaps, what's changing next week); (b) **manager update draft** for Kyle — short, outcome-first, no activity-theater `[CONFIRM WEEK 1: format Kyle actually wants]`; (c) month-end: promotion scorecard update; at M5 month-end, auto-draft the **M6 promotion memo** (evidence: attainment streak ≥130%, sourced S2 count, months above Sr. quota 40) — approval required, framing per COMP_MODEL.md §7 |
| Approval | Personal recap auto; manager update REQUIRED (it's externally visible) |
| Write-back | recap → local archive; manager draft → Gmail draft |

**Golden (manager draft)** — "Week of 8/10: 3 held (target 3), 5 booked for next week. Reply rate dipped to 2.8% — testing two new angles Mon; show rate 80% (confirmations cadence working). Risk: 2 of next week's 5 are >5 days out, pull-in offers going out Monday. Need from you: 10 min on the [X] account list."

## 11. dispatcher — Dispatcher (meta-agent, not LLM-first)

Deterministic core (Rules 5 + priority_score), LLM only for the morning-plan narrative. Trigger: 07:30 daily + on replan. Inputs: Plan, FunnelState, ConversionRates, open Jobs, calendar capacity. Output: ranked Today list + 2-sentence "why this order" summary. Approval: n/a (it only orders work). Write-back: none.

**Golden** — show_rate baseline 70%, last-30d 58% → today's list leads with 4 hold-stage jobs before any outreach, narrative: "Show rate is the bottleneck (−12pts); an hour of confirmations recovers ~0.4 held meetings vs. ~0.05 from the same hour of cold outreach."
