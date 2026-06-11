# Debug Dashboard Spec — /debug Test Cockpit

Two work packages, runnable as **two parallel agents** (they touch different files):
- **Agent A (backend):** simulation endpoints — §1
- **Agent B (frontend):** the dashboard page — §2–§5 (build against §1's contract; don't wait for A)

Shared constraints: no build step, no new dependencies (FastAPI + Jinja2 + vanilla JS only), don't modify existing code except adding routes, everything test-mode-only and clearly marked `# DEBUG ONLY`.

---

## §1. Backend work package (Agent A)

New router `backend/app/api/debug_sim.py`, mounted only when `DEBUG_DASHBOARD=true` env var is set.

Endpoints (all POST, no body unless noted, all return `{ok: true, events_created: [...], jobs_created: [...]}`):

| Endpoint | What it does (through the REAL ingestion path — `EventLog` insert + Rule 1 hooks, never direct table writes) |
|---|---|
| `/api/sim/positive-reply` | Picks a random fixture contact with an active thread → `reply_received` + `positive_reply` events → triage chain fires → book_response job appears |
| `/api/sim/meeting-booked` | Takes optional `{days_out: int=3, persona_tier: str="vp_level"}` → `meeting_booked` event → show-rate machine enters BOOKED, invite job fires |
| `/api/sim/invite-accepted` | Most recent booked meeting → `invite_accepted` |
| `/api/sim/advance-clock` | `{hours: int}` — advances the scheduler's fake clock so T−24h/morning-of/recovery timers fire (reuse the e2e fake-clock harness from Session 12) |
| `/api/sim/meeting-held` | Most recent confirmed meeting → `meeting_held` |
| `/api/sim/no-show` | Most recent confirmed meeting → `meeting_no_show` → recovery agent fires |
| `/api/sim/ad-accepts` | Most recent held meeting → `ad_accepted` → points move pending→credited |
| `/api/sim/new-signal` | Injects a fixture signal (rotate kinds) → research_brief job fires |
| `/api/sim/reset` | Restores fixture DB to pristine state |

Also: `GET /api/sim/state` → `{fake_clock_now, last_5_events: [...]}` for the frontend's event ticker.

Acceptance: each endpoint produces the downstream jobs/events listed above, verified by a small pytest file; `/debug` routes 404 when `DEBUG_DASHBOARD` unset.

## §2. Frontend shell (Agent B)

One file: `backend/app/templates/debug.html`, served at `GET /debug`. All CSS in a `<style>` block, all JS in a `<script>` block.

**Design tokens (use CSS variables, exactly these):**
```css
--bg:#f7f7f8; --card:#ffffff; --border:#e4e4e7; --text:#18181b; --muted:#71717a;
--create:#475569; --convert:#d97706; --hold:#dc2626; --ok:#16a34a;
--accent:#2563eb; --radius:10px; --shadow:0 1px 3px rgba(0,0,0,.06);
font: 14px/1.5 -apple-system, "Segoe UI", sans-serif;
```
Dark text on light cards, generous whitespace (16px card padding, 12px gaps), NO gradients, NO emoji, no more than these colors.

**Layout:** sticky top bar + content area, max-width 1100px centered.
Top bar: left = "BDR OS — Test Cockpit" (600 weight); center = tab buttons [Today] [Queue] [Pace] [Simulate] (active tab: accent underline 2px); right = points chip "▲ 12.5 credited · 5 pending / 35" + fake-clock timestamp + Refresh button. Below the bar, a one-line **event ticker** (muted, monospace, 12px) showing the last event: `14:02 · positive_reply · Acme Corp · j.doe@acme.com`.

Tab switching = show/hide divs, state in a single `App = {data: {}, tab: "today"}` object. Every tab re-fetches its data on show and after any action. `fetchJSON(url, opts)` helper with error toast (red bottom-right, auto-dismiss 4s).

## §3. Today tab

`GET /api/today`. Two parts:

1. **Plan strip** (one row of 4 stat cards): "Touches left today" (per channel, e.g. `✉ 12 · ☎ 25 · in 4`), "Call block" (next block time), "Confirmations due", "Bottleneck" (the narrative string from the API, truncated 80 chars, full on hover).
2. **Job list**: each job = a card row: left color bar 3px (stage color), then: stage badge (pill, 11px uppercase, stage color bg at 12% opacity), job_type (600 weight), account — contact (muted), persona pill (C-SUITE violet#7c3aed / VP blue#2563eb / DIR teal#0d9488 / MGR gray / IC light-gray + "⚠ low-value" suffix), right side: `+1.73 pts` (600, green if ≥1) and buttons **Skip** / **Snooze** (ghost style). Convert-stage jobs with a due_at: show countdown `SLA 2h 14m` in --hold red, and sort pinned to top. Strategic accounts: 🔒 replaced by text "MANUAL ONLY" pill, red border, no buttons.

Empty state: centered muted "No jobs — run the morning plan or simulate events."

## §4. Queue tab

`GET /api/review-queue`. Left column (220px): group list with counts (Speed-to-book first, amber dot if any SLA <2h). Right: selected item:

- Header: account · contact · persona pill · trigger ("signal: hiring_surge") · `EV +1.73 pts`
- Draft pane: white card, 15px serif for email body, subject line bold above. Variants A/B/C as small tabs if present, each with its one-line rationale in muted italic below.
- Sequences (recovery): all 3 touches stacked with day labels (T+10min / +1d / +3d), approved as one unit — single Approve button for the lot.
- `policy_flags`: amber banner with the flag text.
- Persistent top banner on this tab: `Draft-only mode — approving creates drafts, nothing sends.` (muted, 12px, border-left accent).
- Action bar (sticky bottom of pane): **Approve** (accent, solid) · **Reject** (ghost red — opens inline input requiring a reason, submit on Enter) · **Skip** (ghost). Keyboard: A / R / S. After action: optimistic remove, auto-select next item, toast "Approved → draft created".

## §5. Pace tab

`GET /api/pace`. Four stacked sections, each a card with a 13px uppercase muted heading:

1. **GOAL** — horizontal bar: credited (solid accent) + pending (accent at 40%, striped via repeating-linear-gradient) vs target tick; caption `12.5 credited + 5 pending of 35 pts · 51% of month elapsed · pace −8%` (pace red if negative, green if positive).
2. **FUNNEL** — 6 columns (touches → replies → positive → booked → held → AD-accepted): big number, label, small `vs exp 142` muted beneath; column header red if actual <85% of expected.
3. **RATES** — grid of tiles: metric name, blended % (24px, 600), `n=34` + confidence pill (low gray / med blue / high green), drift arrow ±pts vs baseline (red if |drift|>10).
4. **EARNINGS & PROMOTION** — left: `This month $X projected` (24px) with breakdown line `base $2,500 + accelerator $700 + SPIFF $1,000`, annualized vs $135k as a thin bullet bar. Right: promotion scorecard — streak dots (filled = month ≥130%), `Sourced S2: 2 of 3`, `Months >40 pts: 2 consecutive`, status line from API.

## §6. Simulate tab (the demo machine)

Grid of scenario buttons (large, 2 columns), each with a one-line caption of what to expect:

- **📨 Positive reply** — "watch a book_response job appear in Queue within seconds"
- **📅 Meeting booked (VP, 3 days out)** — small persona dropdown + days-out input beside it
- **✅ Invite accepted** · **⏰ Advance clock +24h** — "fires confirmation timers"
- **🤝 Meeting held** · **👻 No-show** — "watch recovery sequence appear"
- **🏆 AD accepts** — "watch Pace: pending → credited"
- **🛰 New signal** — "watch research brief + outreach jobs appear in Today"
- **♻️ Reset fixtures** (bottom, ghost red, confirm dialog)

After any button: call the sim endpoint, then re-fetch ALL tabs' data, flash the event ticker, and show a toast describing what happened (`positive_reply ingested → 1 job created`). Include a "Guided demo" button at top that runs the full happy-path sequence automatically with 2s pauses and switches tabs to follow the action (reply → Queue → approve hint → booked → clock+24h → held → AD accepts → Pace).

## §7. Acceptance (both agents done =)

1. `DEBUG_DASHBOARD=true make dev` → /debug loads, all four tabs render real API data with zero console errors.
2. Full clickable loop works: New signal → job in Today → draft in Queue → Approve → Meeting booked → advance clock → confirmations in Queue → approve → Meeting held → AD accepts → Pace shows credited points increase and earnings move.
3. No-show path: No-show → recovery sequence in Queue (3 touches, one Approve).
4. Keyboard A/R/S works in Queue. Reset restores pristine state.
5. `DEBUG_DASHBOARD` unset → /debug and /api/sim/* return 404. Zero changes to existing files except route mounting.
