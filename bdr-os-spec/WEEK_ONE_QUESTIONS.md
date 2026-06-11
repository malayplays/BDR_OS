# WEEK_ONE_QUESTIONS.md — Confirm at Cognition Before Connecting Anything

Ordered by how much of the system each answer unblocks. Get policy clearance (§5) before any real connection.

## 1. Comp mechanics — verify the brief against the official plan doc

- [ ] Plan doc matches my brief: 35 pts/mo, ramp 0/15/30/35, $71.43/pt + $100 accelerator, point values (8/5/3/1/0.5 meetings; 5-10/3-6/3-6 opps), $1k S2 SPIFF unlimited. Flag any drift → update `comp_plan.yaml` only.
- [ ] Which calendar month counts as M1 given mid-July start (July or August)? Ramp math depends on it.
- [ ] AD acceptance: where is it recorded in Salesforce (field? approval object? email?) — this is the `ad_accepted` event source, the system's most important sync.
- [ ] Eligibility window for duplicate meetings (the clawback rule references one — exact length?).
- [ ] "Dormant 120+ days" — dormant by what measure exactly (any activity? meetings only? whose activity)?
- [ ] Named Target validation workflow with ADs: format, where it lives, how fast they turn it around.

## 2. Team benchmark rates (replace seed priors in `comp_plan.yaml`/rates config)

- [ ] Reply / positive / book rates by channel for the team; show rate; AD-accept rate (or proxy: how often do meetings get rejected, and why — feeds clawback gate).
- [ ] Typical persona mix of a top performer's calendar (calibrates the cascade's mix target).
- [ ] Dials/hour realistic on the team's dialer (seed = 12).

## 3. Stack (fills the [CONNECT LATER] registry)

- [ ] Salesforce: edition, API access for personal tooling, sandbox available? Which objects/fields for: meetings, AD acceptance, opp stages S0–S2, Named Targets, activity history.
- [ ] Sequencer (Outreach/Salesloft/Apollo/other?) — determines whether copy write-back targets sequencer or Gmail drafts, and whether sequence-management jobs exist at all.
- [ ] Dialer + where calls log. Gong: do SDRs get seats + API?
- [ ] Clay: team workspace? Can I get table/webhook access?
- [ ] Email/calendar: Google or Microsoft? Scheduling-link tool in use (affects book_response drafts)?
- [ ] Where do signals come from today (intent data, alerts) — anything I can subscribe to instead of building?

## 4. People & process

- [ ] Kyle: preferred update format + cadence (reporting agent output) — ask for one example of an update he liked.
- [ ] ADs I'm mapped to; their account lists; how they want Named Target proposals.
- [ ] Sales-approved messaging/value props + banned claims → `value_props.yaml`. Win stories I can reference.
- [ ] What does "top BDR" actually look like here — points/month of #1 last quarter? (Calibrates the 60-pt target.)

## 5. Policy / compliance — BLOCKING, before any connection

- [ ] Personal-tool policy: am I allowed to connect personal software to company Salesforce/Gmail/Calendar at all? API keys policy? (If no: system runs in manual-sync mode — CSV export/import path; design supports it, confirm worst case early.)
- [ ] LLM policy: can company data touch a personal Anthropic key? If not, what's approved?
- [ ] Outbound limits/deliverability rules the team already enforces (daily caps, domains, warmup) → `policy.yaml`.
- [ ] Data handling: anything prospect-related that must not leave Salesforce?

## Already answered by the comp brief (do not re-ask, just verify)

CRM = Salesforce. Comp unit = AD-accepted points. Quota/ramp/accelerator/SPIFF mechanics. Promotion bar context (Sr. = 40 pts, standard track M9).
