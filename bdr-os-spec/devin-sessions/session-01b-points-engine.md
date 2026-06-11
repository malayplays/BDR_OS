# Devin Session 1b — Points & Comp Engine

Prereq: Session 1 merged. Attach COMP_MODEL.md (the spec) + DATA_MODEL.md. Pure functions in `engine/` — same zero-I/O rule.

## Task

1. `engine/points.py` — point valuation from `comp_plan.yaml`: meeting points by persona tier (gated on `ad_accepted`), opp points by type/stage, SPIFF detection (sourced + S2). Three buckets: credited / pending / projected per COMP_MODEL.md §3. Clawback reversal handling (`credit_clawed_back` → negative ledger entry, never deletion).
2. `engine/earnings.py` — projector per COMP_MODEL.md §6: ramp-aware (M1 guarantee, M2 200% cap), $71.43/pt → $100/pt accelerator split, SPIFF cash, Sr.-rate switch on promotion date config, monthly + annualized vs $135k goal, marginal-$ of next point.
3. `engine/promotion.py` — scorecard per COMP_MODEL.md §7: rolling ≥130% streak, sourced-S2 count, consecutive months >40 pts; `m6_case_ready: bool` + evidence table struct.
4. Extend `cascade.py` — points-denominated cascade (DATA_MODEL.md Rule 3 updated form): persona-mix-weighted `avg_pts_per_held`, persona mix as an explicit cascade input with default favoring VP+ (mix itself is a catch-up lever now).
5. Extend `catchup.py` — new levers: dormancy-requalification batch (120-day list), persona-mix shift up-market, month-end accelerator awareness (annotation only); M2 cap-awareness flag (surplus banking suggestion is advisory, never auto).
6. `engine/clawback.py` — provenance completeness check for a meeting (outbound evidence present, Named Target validated or dormancy ≥120d, no duplicate in window) → `credit_risk: none|warn|block_booking`.

## Done = these pass

- `test_point_valuation_table` — every row of COMP_MODEL.md §2 asserted (8/5/3/1/0.5; 5-10/3-6/3-6; inbound locked pre-promotion).
- `test_credit_gating` — booked→confirmed→occurred yields pending only; `ad_accepted` moves to credited; no_show yields zero; reschedule-then-occur credits once.
- `test_clawback_reversal` — clawed-back meeting → ledger nets to 0, history preserved.
- `test_earnings_ramp` — M1: full OTE regardless; M2: 30 pts vs quota 15 → capped at 200%; M4: 60 pts → 35×71.43 + 25×100 = $5,000 (the top-BDR math from COMP_MODEL.md §5, asserted exactly).
- `test_spiff` — sourced net-new opp hits S2 → +$1,000; influenced opp S2 → no SPIFF.
- `test_promotion_scorecard` — synthetic 5-month history matching the M2–M6 plan → m6_case_ready true with correct evidence table; one month at 120% breaks the streak.
- `test_cascade_persona_weighting` — same point target with IC-heavy vs VP-heavy mix → VP mix requires ~10× fewer held meetings; mix bounds respected.
- `test_compounding_play_ev` — net-new VP + S1 + S2 path EV = 20 pts (assert the COMP_MODEL.md §5 example).
- `test_clawback_gate` — meeting missing outbound provenance → `block_booking`; duplicate within window → `warn` minimum.
- Coverage ≥95% on new modules; still zero I/O imports in `engine/`.
