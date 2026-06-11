# v0 Prompt — Screen 3: Pace (Goals · Earnings · Promotion)

Paste into v0 (works in Lovable unchanged). Use recharts for charts.

---

Build a "Pace" screen for a personal BDR operating system. Next.js App Router, Tailwind, shadcn/ui, recharts. Four stacked sections:

**1. Goal cascade tree:** Year → Quarter → Month → This week, each node showing points target vs. credited+pending, with a thin two-segment bar (credited solid, pending striped). Current month expanded: weekly bookings required, daily touch volumes by channel, derived "this comes from: 35 pts ÷ avg 4.1 pts/held ÷ 70% show ÷ 55% book…" math line in muted mono (the cascade is auditable, never hand-edited — show a small "derived" badge, no edit affordance).

**2. Funnel & rates:** horizontal funnel touches → replies → positive → booked → occurred → AD-accepted with per-stage actual vs. expected; below, rate tiles (reply by channel, book, show, AD-accept) each with: blended %, trend sparkline (90d), confidence badge (low=gray "n=12", med, high), and drift indicator vs. baseline (red arrow if >10pts). Persona-mix donut next to the funnel: share of booked meetings by tier (C-Suite/VP/Director/Manager/IC) with a target ring overlay — caption "1 VP meeting = 10 IC meetings."

**3. Earnings projector:** month cards Feb–Jan with quota ramp marked (M1 guaranteed, M2 capped); current month: credited $, pending $, projected $, accelerator zone shaded above quota ("every pt above 35 = $100"); annualized total vs. $135k goal as a bullet chart; SPIFF counter ("Sourced S2 SPIFFs: 2 × $1,000").

**4. Promotion scorecard + levers:** left card "M6 Case": streak tracker (months ≥130% — filled dots), sourced S2 count vs. target 2–3, consecutive months >40 pts, and a status line ("Case ready in 2 more qualifying months"). Right card "Active levers" (only when behind pace): ranked list, each with estimated Δpts and attention cost, Accept/Dismiss buttons — e.g., "Re-engage 14 dormant contacts (≥120d) ≈ +6 pts", "Pull in 2 far-out meetings ≈ +1.4 pts", "Shift mix +10% VP ≈ +3 pts/wk", "Raise volume +25% (cap) ≈ +1.1 pts". If levers can't close the gap show an honest red banner: "Goal at risk: max levers recover 8 of 11 pt gap."

Mock data: mid-month state, slightly behind pace (−8%), show rate drifting −11pts, M4 of the plan, one SPIFF banked. Typed `getPace()` in `lib/api.ts` mirroring `GET /api/pace`.
