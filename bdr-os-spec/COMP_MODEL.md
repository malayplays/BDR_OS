# COMP_MODEL.md — Points, Pay, Credit Pipeline, Promotion Case

The goal engine's currency is **AD-accepted points**, not meetings. Everything below is config (`comp_plan.yaml`), never hard-coded — comp plans change.

## 1. Comp facts (source: Malay's plan brief, June 2026)

- SDR: $70k base + $30k variable. Sr. SDR: $75k + $35k. Personal goal: top of class, ~$135k+ total, **promotion ask at month 6** (standard = M9).
- Plan year Feb 1 2026 – Jan 31 2027. Commissions quarterly, ≤21 days after close.
- Quota: SDR 35 pts/mo; Sr. SDR 40. Ramp: M1=0 (100% OTE guaranteed), M2=15 (commission capped 200%), M3=30, M4+=35. M1 = first full month from start (mid-July start → confirm whether July or Aug counts as M1 `[CONFIRM WEEK 1]`).
- Payout: $71.43/pt to 35; **$100/pt above 35 (accelerator)**. Sr.: $72.92/pt to 40, $100 above.
- SPIFF: sourced opp reaching S2 = $1,000 cash, unlimited.

## 2. Point values (`comp_plan.yaml`)

```yaml
meeting_points:   # must OCCUR and be AD-ACCEPTED
  global_c_suite: 8
  vp_group_csuite_mp_head_coe_ai_devx: 5
  director_head_of: 3
  manager: 1
  ic: 0.5
opp_points:
  sourced_net_new:    {S1: 5, S2: 10}
  sourced_engaged:    {S1: 3, S2: 6}
  influenced:         {S1: 3, S2: 6}
  inbound_sr_only:    {S2: 2}      # locked until promotion
spiff: {sourced_s2_cash: 1000}
```

## 3. Credit pipeline (gates all point recognition)

```
booked → confirmed → occurred → AD_ACCEPTED  ← points exist ONLY here
                       ↘ no_show = ZERO credit (reschedule re-earns on occurrence)
```
EventLog additions: `ad_accepted`, `ad_rejected`, `s1_reached`, `s2_reached`, `credit_clawed_back` (`meeting_held` is the canonical event for "occurred"). FunnelState tracks points in three buckets: **credited** (AD-accepted), **pending** (occurred, awaiting AD), **projected** (booked × show_rate × accept_rate). Pace runs on credited+pending; never let projected masquerade as real.

**Clawback protection log** (the system's insurance policy): every meeting carries `provenance: {outbound_touches: [event_ids], first_touch_channel, named_target_validated: bool, dormancy_check: {last_activity_date, days_dormant}}`. Auto-assembled from EventLog; exported as evidence on AD disputes. Duplicate detection: same contact within eligibility window → hard warn before booking. Salesforce is system of record — provenance is logged there too (activity note), per the clawback rules.

## 4. Qualification gates (encoded, not vibes)

- **Net New Meeting** = first-ever meeting w/ Named Target OR contact dormant ≥120 days, sourced from SDR outbound, AD-validated. → `named_target_status` on account refs; **120-day dormancy timer** per contact (computed nightly from CRM last-activity; alert at day 120 — "re-qualified as net new" job).
- **SQL bar** (gates S1): ICP fit, relevant title, expressed pain, confirmed need, agreed next steps, eval ≤6mo, key facts verified. → crm_scribe's qualification block maps 1:1 to this checklist; 3 Why's (Anything/Now/Windsurf-Devin) captured per opp.

## 5. Strategy encoded in the engine

- **Persona arbitrage**: 1 VP meeting = 10 IC meetings. `expected_value` is now in **Δpoints**, not Δmeetings: `EV = P(held) × P(ad_accept) × persona_points`. A VP outreach job at 4% reply beats an IC job at 12% reply — the math enforces "never book down-market." Dispatcher refuses to rank IC-persona create-jobs above Manager+ unless explicitly approved.
- **Compounding play**: net-new VP meeting → S1 → S2 = 20 pts + $1,000 from one account. Research brief flags `compound_candidate: true` on net-new accounts with multi-thread potential; these get priority + multi-contact mapping.
- **120-day goldmine**: dormant contacts at engaged accounts re-qualify as net new (sourced_net_new S1=5 vs engaged S1=3). Hygiene agent's nightly dormancy sweep is a first-class point source, not cleanup.
- **Accelerator math**: marginal point #36+ is worth $100 vs $71.43 — catch-up levers and month-end pushes price this in. Conversely in M2 (quota 15, 200% cap): cap at 30 pts commission — engine flags when banked points exceed cap and suggests **slipping surplus bookings to month+1** where legal/ethical (book the meeting when it serves the prospect; never sandbag a hot deal — flag for Malay's judgment, draft-only as always).

## 6. Earnings projector (Pace screen module)

`project(month) = base/12 + min(pts, quota)×rate + max(pts−quota,0)×100 + spiffs`, ramp-aware (M1 guarantee, M2 cap). Outputs: month-to-date $, projected month $, annualized vs $135k goal, "value of one more VP meeting this month" (marginal-$ widget — concrete motivation).

## 7. Promotion scorecard (auto-builds the M6 case)

Tracked continuously, rendered on Pace screen + monthly report:
- Rolling attainment streak (months ≥130%)
- Sourced S2 count (target 2–3 by M6)
- Consecutive months above Sr. SDR quota (40 pts) — the framing line: "performing above Sr. quota for N straight months"
- Auto-drafted promotion memo at M5 month-end (reporting agent; approval required) with the evidence table. If the ask misses: tracker for "specific gaps + date" from Austin Mead.

## 8. Month-by-month targets (seed `Goal` rows)

| Month | Quota | Target | Focus |
|---|---|---|---|
| M1 | 0 | foundation | Named Target lists w/ ADs, engaged/install account map, 120-day dormant report, sequences loaded for M2 landing, 3 Why's + demo mastery |
| M2 | 15 | 30+ | cheapest 200% of the year — bank the data point |
| M3 | 30 | 45+ | first sourced S2 ($1k SPIFF) |
| M4–M5 | 35 | 55–60 | 1 S2/mo; document wins in writing to VP/ADs |
| M6 | 35 | 60 | **promotion ask** |
| M7–M12 | 40 (Sr.) | 60–65 | inbound S2 unlocked (+2 ea) |

These seed the Goal table (unit=`points`); the cascade then derives weekly/daily activity through the persona-weighted funnel: `points_needed ÷ avg_pts_per_held_meeting(persona_mix) → meetings → bookings → touches`.
