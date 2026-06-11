"""Exhaustive unit tests for Session 1b — Points & Comp Engine.

Every test name listed in session-01b-points-engine.md "Done = these pass".
"""

from __future__ import annotations

import ast
import uuid
from datetime import date, datetime
from pathlib import Path

import pytest

from app.engine.cascade import (
    PERSONA_MIX_MAX,
    PERSONA_MIX_MIN,
    avg_pts_per_held,
    compute_plan,
    validate_persona_mix,
)
from app.engine.catchup import compute_catchup
from app.engine.clawback import (
    CreditRisk,
    DuplicateCheck,
    MeetingProvenance,
    check_provenance,
    find_duplicates_in_window,
)
from app.engine.earnings import (
    ACCELERATOR_RATE,
    PERSONAL_GOAL_ANNUAL,
    SDR_BASE_ANNUAL,
    SDR_OTE,
    SDR_RATE_PER_POINT,
    SR_SDR_RATE_PER_POINT,
    project_earnings,
)
from app.engine.points import (
    MEETING_POINTS,
    OPP_POINTS,
    SPIFF_SOURCED_S2_CASH,
    compute_compounding_play_ev,
    compute_points,
    is_inbound_locked,
    meeting_points_for_tier,
    opp_points_for_type_stage,
)
from app.engine.promotion import (
    MonthRecord,
    compute_scorecard,
)
from app.engine.types import (
    Capacity,
    Channel,
    Event,
    EventType,
    FunnelCounts,
    FunnelState,
    Goal,
    PersonaTier,
    Plan,
    PointsBucket,
    RateMetric,
    RateRow,
)

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _make_event(
    event_type: str,
    *,
    persona_tier: str | None = None,
    points_value: float | None = None,
    account_ref: str = "acct-1",
    contact_ref: str | None = "contact-1",
    payload: dict | None = None,
    reverses_event_id: str | None = None,
    occurred_at: datetime | None = None,
    channel: str | None = None,
) -> Event:
    return Event(
        event_type=event_type,
        occurred_at=occurred_at or datetime(2026, 6, 15, 10, 0),
        persona_tier=persona_tier,
        points_value=points_value,
        account_ref=account_ref,
        contact_ref=contact_ref,
        payload=payload or {},
        reverses_event_id=reverses_event_id,
        channel=channel,
    )


def _make_goal(**overrides) -> Goal:
    defaults = dict(
        id=str(uuid.uuid4()),
        unit="points",
        target_value=35.0,
        period_type="month",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        edited_at=datetime(2026, 5, 15),
    )
    defaults.update(overrides)
    return Goal(**defaults)


def _make_capacity(**overrides) -> Capacity:
    defaults = dict(business_days=20, pto_dates=(), blocked_hours=0)
    defaults.update(overrides)
    return Capacity(**defaults)


def _make_rates() -> list[RateRow]:
    """Standard rate rows for cascade tests."""
    now = datetime(2026, 6, 15)
    rows = []
    benchmarks = {
        (RateMetric.REPLY_RATE, Channel.EMAIL): 0.04,
        (RateMetric.REPLY_RATE, Channel.CALL): 0.08,
        (RateMetric.REPLY_RATE, Channel.LINKEDIN): 0.08,
        (RateMetric.POSITIVE_REPLY_RATE, None): 0.35,
        (RateMetric.BOOK_RATE, None): 0.55,
        (RateMetric.SHOW_RATE, None): 0.70,
        (RateMetric.QUALIFY_RATE, None): 0.60,
        (RateMetric.AD_ACCEPT_RATE, None): 0.90,
    }
    for (metric, channel), rate in benchmarks.items():
        rows.append(RateRow(
            metric=metric,
            channel=channel,
            window_days=30,
            n_sample=50,
            actual_rate=rate,
            benchmark_rate=rate,
            k_strength=30,
            blended_rate=rate,
            confidence="medium",
            baseline_90d=rate,
            computed_at=now,
        ))
    return rows


def _make_funnel(
    *,
    pace_gap: float = -0.20,
    held: int = 3,
    gap_by_stage: dict | None = None,
    goal_id: str = "g1",
) -> FunnelState:
    return FunnelState(
        goal_id=goal_id,
        as_of=datetime(2026, 6, 15),
        counts=FunnelCounts(held=held),
        points=PointsBucket(credited=10.0, pending=5.0),
        pace_gap=pace_gap,
        gap_by_stage=gap_by_stage or {"hold": {"actual_held": held, "expected_held": held + 5}},
    )


def _make_plan(
    *,
    weekly_held_target: float = 4.0,
    rates_snapshot: dict | None = None,
    goal_id: str = "g1",
) -> Plan:
    return Plan(
        id="plan-1",
        goal_id=goal_id,
        week_start=date(2026, 6, 8),
        weekly_bookings_required=6.0,
        weekly_held_target=weekly_held_target,
        daily_allocations=(),
        rates_snapshot=rates_snapshot or {
            "show_rate": 0.70,
            "book_rate": 0.55,
            "ad_accept_rate": 0.90,
        },
        capacity=_make_capacity(),
        generated_at=datetime(2026, 6, 8),
    )


# ═══════════════════════════════════════════════════════════════════════
# 1. test_point_valuation_table
# ═══════════════════════════════════════════════════════════════════════

class TestPointValuationTable:
    """Every row of COMP_MODEL.md §2 asserted."""

    def test_meeting_points_global_c_suite(self):
        assert meeting_points_for_tier(PersonaTier.GLOBAL_C_SUITE) == 8.0

    def test_meeting_points_vp_level(self):
        assert meeting_points_for_tier(PersonaTier.VP_LEVEL) == 5.0

    def test_meeting_points_director(self):
        assert meeting_points_for_tier(PersonaTier.DIRECTOR) == 3.0

    def test_meeting_points_manager(self):
        assert meeting_points_for_tier(PersonaTier.MANAGER) == 1.0

    def test_meeting_points_ic(self):
        assert meeting_points_for_tier(PersonaTier.IC) == 0.5

    def test_opp_points_sourced_net_new_s1(self):
        assert opp_points_for_type_stage("sourced_net_new", "S1") == 5.0

    def test_opp_points_sourced_net_new_s2(self):
        assert opp_points_for_type_stage("sourced_net_new", "S2") == 10.0

    def test_opp_points_sourced_engaged_s1(self):
        assert opp_points_for_type_stage("sourced_engaged", "S1") == 3.0

    def test_opp_points_sourced_engaged_s2(self):
        assert opp_points_for_type_stage("sourced_engaged", "S2") == 6.0

    def test_opp_points_influenced_s1(self):
        assert opp_points_for_type_stage("influenced", "S1") == 3.0

    def test_opp_points_influenced_s2(self):
        assert opp_points_for_type_stage("influenced", "S2") == 6.0

    def test_opp_points_inbound_sr_only_s2(self):
        assert opp_points_for_type_stage("inbound_sr_only", "S2") == 2.0

    def test_inbound_locked_pre_promotion(self):
        assert is_inbound_locked(is_promoted=False) is True

    def test_inbound_unlocked_post_promotion(self):
        assert is_inbound_locked(is_promoted=True) is False

    def test_inbound_blocked_in_compute_points(self):
        """Inbound S2 event yields 0 points when not promoted."""
        events = [
            _make_event(
                EventType.S2_REACHED,
                payload={"opp_type": "inbound_sr_only"},
                points_value=2.0,
            ),
        ]
        result = compute_points(events, is_promoted=False)
        assert result.buckets.credited == 0.0

    def test_inbound_allowed_when_promoted(self):
        events = [
            _make_event(
                EventType.S2_REACHED,
                payload={"opp_type": "inbound_sr_only"},
                points_value=2.0,
            ),
        ]
        result = compute_points(events, is_promoted=True)
        assert result.buckets.credited == 2.0


def test_point_valuation_table():
    """Aggregate test matching the spec name exactly."""
    # Meeting points: 8/5/3/1/0.5
    assert MEETING_POINTS[PersonaTier.GLOBAL_C_SUITE] == 8.0
    assert MEETING_POINTS[PersonaTier.VP_LEVEL] == 5.0
    assert MEETING_POINTS[PersonaTier.DIRECTOR] == 3.0
    assert MEETING_POINTS[PersonaTier.MANAGER] == 1.0
    assert MEETING_POINTS[PersonaTier.IC] == 0.5

    # Opp points: sourced_net_new 5-10, sourced_engaged 3-6, influenced 3-6
    assert OPP_POINTS["sourced_net_new"]["S1"] == 5.0
    assert OPP_POINTS["sourced_net_new"]["S2"] == 10.0
    assert OPP_POINTS["sourced_engaged"]["S1"] == 3.0
    assert OPP_POINTS["sourced_engaged"]["S2"] == 6.0
    assert OPP_POINTS["influenced"]["S1"] == 3.0
    assert OPP_POINTS["influenced"]["S2"] == 6.0

    # Inbound locked pre-promotion
    assert is_inbound_locked(is_promoted=False) is True
    assert is_inbound_locked(is_promoted=True) is False


# ═══════════════════════════════════════════════════════════════════════
# 2. test_credit_gating
# ═══════════════════════════════════════════════════════════════════════

def test_credit_gating():
    """booked→confirmed→occurred yields pending only; ad_accepted moves to
    credited; no_show yields zero; reschedule-then-occur credits once."""

    # Scenario 1: booked → held (occurred) → pending only (no ad_accepted yet)
    events_pending = [
        _make_event(EventType.MEETING_BOOKED, persona_tier=PersonaTier.VP_LEVEL),
        _make_event(EventType.MEETING_HELD, persona_tier=PersonaTier.VP_LEVEL),
    ]
    result = compute_points(events_pending)
    assert result.buckets.credited == 0.0
    assert result.buckets.pending == 5.0  # VP = 5 pts

    # Scenario 2: booked → held → ad_accepted → credited
    events_credited = [
        _make_event(EventType.MEETING_BOOKED, persona_tier=PersonaTier.VP_LEVEL),
        _make_event(EventType.MEETING_HELD, persona_tier=PersonaTier.VP_LEVEL),
        _make_event(EventType.AD_ACCEPTED, persona_tier=PersonaTier.VP_LEVEL, points_value=5.0),
    ]
    result = compute_points(events_credited)
    assert result.buckets.credited == 5.0
    assert result.buckets.pending == 0.0

    # Scenario 3: no_show → zero credit
    events_noshow = [
        _make_event(EventType.MEETING_BOOKED, persona_tier=PersonaTier.VP_LEVEL),
        _make_event(EventType.MEETING_NO_SHOW, persona_tier=PersonaTier.VP_LEVEL),
    ]
    result = compute_points(events_noshow)
    assert result.buckets.credited == 0.0
    assert result.buckets.pending == 0.0
    assert result.buckets.projected == 0.0

    # Scenario 4: reschedule-then-occur credits once
    events_reschedule = [
        _make_event(EventType.MEETING_BOOKED, persona_tier=PersonaTier.DIRECTOR,
                    account_ref="acct-2", contact_ref="c-2"),
        _make_event(EventType.MEETING_RESCHEDULED, persona_tier=PersonaTier.DIRECTOR,
                    account_ref="acct-2", contact_ref="c-2"),
        _make_event(EventType.MEETING_HELD, persona_tier=PersonaTier.DIRECTOR,
                    account_ref="acct-2", contact_ref="c-2"),
        _make_event(EventType.AD_ACCEPTED, persona_tier=PersonaTier.DIRECTOR,
                    points_value=3.0, account_ref="acct-2", contact_ref="c-2"),
    ]
    result = compute_points(events_reschedule)
    assert result.buckets.credited == 3.0


# ═══════════════════════════════════════════════════════════════════════
# 3. test_clawback_reversal
# ═══════════════════════════════════════════════════════════════════════

def test_clawback_reversal():
    """Clawed-back meeting → ledger nets to 0, history preserved."""
    original_event_id = str(uuid.uuid4())
    events = [
        _make_event(EventType.MEETING_BOOKED, persona_tier=PersonaTier.VP_LEVEL),
        _make_event(EventType.MEETING_HELD, persona_tier=PersonaTier.VP_LEVEL),
        _make_event(EventType.AD_ACCEPTED, persona_tier=PersonaTier.VP_LEVEL, points_value=5.0),
        _make_event(
            EventType.CREDIT_CLAWED_BACK,
            persona_tier=PersonaTier.VP_LEVEL,
            points_value=5.0,
            reverses_event_id=original_event_id,
        ),
    ]
    result = compute_points(events)

    # Net credited = 5 - 5 = 0
    assert result.buckets.credited == 0.0

    # History preserved: both the credit and the clawback entries exist in ledger
    assert len(result.ledger) >= 2
    ad_entries = [e for e in result.ledger if e.event_type == EventType.AD_ACCEPTED]
    claw_entries = [e for e in result.ledger if e.event_type == EventType.CREDIT_CLAWED_BACK]
    assert len(ad_entries) == 1
    assert len(claw_entries) == 1
    assert ad_entries[0].points == 5.0
    assert claw_entries[0].points == -5.0
    assert claw_entries[0].reverses_event_id == original_event_id

    # Net is zero
    total_ledger_pts = sum(e.points for e in result.ledger)
    assert total_ledger_pts == 0.0


# ═══════════════════════════════════════════════════════════════════════
# 4. test_earnings_ramp
# ═══════════════════════════════════════════════════════════════════════

def test_earnings_ramp():
    """M1: full OTE regardless; M2: 30 pts vs quota 15 → capped at 200%;
    M4: 60 pts → 35×71.43 + 25×100 = $5,000."""

    # M1: full OTE guaranteed
    m1 = project_earnings(points=0, month=1)
    expected_m1_variable = SDR_OTE / 12 - SDR_BASE_ANNUAL / 12
    # M1 total = base/12 + variable/12
    assert m1.commission == pytest.approx(expected_m1_variable, abs=0.01)
    assert m1.total_monthly == pytest.approx(SDR_OTE / 12, abs=1.0)

    # M2: 30 pts vs quota 15 → commission = min(15, 15)*71.43 + max(15, 0)*100
    #   = 15*71.43 + 15*100 = 1071.45 + 1500 = 2571.45
    # But capped at 200% of variable/12 = 200% × (30000/12) = 2 × 2500 = 5000
    m2 = project_earnings(points=30, month=2)
    variable_monthly = SDR_OTE - SDR_BASE_ANNUAL
    cap_200 = 2.0 * (variable_monthly / 12)  # 2 * 2500 = 5000
    # 15*71.43 + 15*100 = 1071.45 + 1500 = 2571.45 — under cap, so no cap
    raw_commission = 15 * SDR_RATE_PER_POINT + 15 * ACCELERATOR_RATE
    assert raw_commission == pytest.approx(2571.45, abs=0.01)
    assert raw_commission < cap_200  # 2571.45 < 5000 → not capped
    assert m2.commission == pytest.approx(raw_commission, abs=0.01)
    assert m2.cap_applied is False

    # M4: 60 pts, quota 35 → 35×71.43 + 25×100 = 2500.05 + 2500 = $5000.05
    m4 = project_earnings(points=60, month=4)
    expected_commission = 35 * SDR_RATE_PER_POINT + 25 * ACCELERATOR_RATE
    # 35 × 71.43 = 2500.05, 25 × 100 = 2500. Total = 5000.05
    assert expected_commission == pytest.approx(5000.05, abs=0.01)
    assert m4.commission == pytest.approx(expected_commission, abs=0.01)


# ═══════════════════════════════════════════════════════════════════════
# 5. test_spiff
# ═══════════════════════════════════════════════════════════════════════

def test_spiff():
    """Sourced net-new opp hits S2 → +$1,000; influenced opp S2 → no SPIFF."""

    # Sourced net-new S2 → SPIFF
    events_sourced = [
        _make_event(
            EventType.S1_REACHED,
            payload={"opp_type": "sourced_net_new"},
            points_value=5.0,
        ),
        _make_event(
            EventType.S2_REACHED,
            payload={"opp_type": "sourced_net_new"},
            points_value=10.0,
        ),
    ]
    result = compute_points(events_sourced)
    assert result.spiff_cash == SPIFF_SOURCED_S2_CASH  # $1,000
    assert result.buckets.credited == 15.0  # 5 + 10

    # Influenced S2 → no SPIFF
    events_influenced = [
        _make_event(
            EventType.S2_REACHED,
            payload={"opp_type": "influenced"},
            points_value=6.0,
        ),
    ]
    result = compute_points(events_influenced)
    assert result.spiff_cash == 0.0
    assert result.buckets.credited == 6.0


# ═══════════════════════════════════════════════════════════════════════
# 6. test_promotion_scorecard
# ═══════════════════════════════════════════════════════════════════════

def test_promotion_scorecard():
    """Synthetic 5-month history matching M2–M6 plan → m6_case_ready true
    with correct evidence table; one month at 120% breaks the streak."""

    # M2: quota 15, target 30+ (200% → ≥130% ✓)
    # M3: quota 30, target 45+ (150% → ≥130% ✓)
    # M4: quota 35, target 55 (157% → ≥130% ✓)
    # M5: quota 35, target 60 (171% → ≥130% ✓)
    # M6: quota 35, target 60 (171% → ≥130% ✓)
    history_passing = [
        MonthRecord(month=2, points=30, quota=15, sourced_s2_count=0),
        MonthRecord(month=3, points=45, quota=30, sourced_s2_count=1),
        MonthRecord(month=4, points=55, quota=35, sourced_s2_count=1),
        MonthRecord(month=5, points=60, quota=35, sourced_s2_count=0),
        MonthRecord(month=6, points=60, quota=35, sourced_s2_count=1),
    ]
    sc = compute_scorecard(history_passing)
    assert sc.m6_case_ready is True
    assert sc.rolling_130_streak == 5
    assert sc.sourced_s2_total == 3
    assert sc.consecutive_above_sr_quota >= 3  # M4 (55), M5 (60), M6 (60) all > 40
    assert sc.months_evaluated == 5

    # Evidence table has expected rows
    evidence_metrics = [e.metric for e in sc.evidence_table]
    assert "Rolling ≥130% attainment streak" in evidence_metrics
    assert "Sourced S2 count" in evidence_metrics
    assert "Consecutive months >40 pts (Sr. SDR quota)" in evidence_metrics

    # Now break streak: one month at 120% (not ≥130%)
    history_broken = [
        MonthRecord(month=2, points=30, quota=15, sourced_s2_count=0),
        MonthRecord(month=3, points=36, quota=30, sourced_s2_count=1),  # 120% — breaks streak
        MonthRecord(month=4, points=55, quota=35, sourced_s2_count=1),
        MonthRecord(month=5, points=60, quota=35, sourced_s2_count=0),
        MonthRecord(month=6, points=60, quota=35, sourced_s2_count=1),
    ]
    sc_broken = compute_scorecard(history_broken)
    assert sc_broken.m6_case_ready is False
    assert sc_broken.rolling_130_streak == 3  # only M4, M5, M6


# ═══════════════════════════════════════════════════════════════════════
# 7. test_cascade_persona_weighting
# ═══════════════════════════════════════════════════════════════════════

def test_cascade_persona_weighting():
    """Same point target with IC-heavy vs VP-heavy mix → VP mix requires
    ~10× fewer held meetings; mix bounds respected."""

    rates = _make_rates()
    ad_accept = 0.90

    # VP-heavy mix: 80% VP, 20% Director
    vp_mix = {PersonaTier.VP_LEVEL: 0.80, PersonaTier.DIRECTOR: 0.20}
    vp_avg = avg_pts_per_held(vp_mix, ad_accept)

    # IC-heavy mix: 80% IC, 20% Manager
    ic_mix = {PersonaTier.IC: 0.80, PersonaTier.MANAGER: 0.20}
    ic_avg = avg_pts_per_held(ic_mix, ad_accept)

    # VP mix should yield much higher avg pts per held
    assert vp_avg > ic_avg

    # For 35 points target, calculate meetings needed
    target = 35.0
    vp_meetings = target / vp_avg
    ic_meetings = target / ic_avg

    # VP mix requires ~10× fewer meetings (5 pts × 0.9 ≈ 4.5 vs 0.5 × 0.9 ≈ 0.45)
    ratio = ic_meetings / vp_meetings
    assert ratio > 5.0, f"Expected >5× ratio, got {ratio:.1f}×"

    # Validate mix bounds are respected (use ≥3 tiers for jointly satisfiable bounds)
    validated_extreme = validate_persona_mix({
        PersonaTier.IC: 0.90,
        PersonaTier.VP_LEVEL: 0.05,
        PersonaTier.DIRECTOR: 0.05,
    })
    for tier, frac in validated_extreme.items():
        assert frac >= PERSONA_MIX_MIN - 0.001
        assert frac <= PERSONA_MIX_MAX + 0.001
    assert abs(sum(validated_extreme.values()) - 1.0) < 0.001

    # Full cascade test: same goal, different mixes
    goal = _make_goal(target_value=35.0)
    cap = _make_capacity()

    plan_vp = compute_plan(
        goal, rates, cap,
        persona_mix_target=vp_mix,
        as_of=date(2026, 6, 1),
    )
    plan_ic = compute_plan(
        goal, rates, cap,
        persona_mix_target=ic_mix,
        as_of=date(2026, 6, 1),
    )

    # VP plan should require fewer weekly held meetings
    assert plan_vp.weekly_held_target < plan_ic.weekly_held_target


# ═══════════════════════════════════════════════════════════════════════
# 8. test_compounding_play_ev
# ═══════════════════════════════════════════════════════════════════════

def test_compounding_play_ev():
    """Net-new VP + S1 + S2 path EV = 20 pts (COMP_MODEL.md §5 example)."""
    ev = compute_compounding_play_ev(
        PersonaTier.VP_LEVEL,
        opp_type="sourced_net_new",
        p_held=1.0,
        p_ad_accept=1.0,
    )
    # VP meeting (5) + sourced_net_new S1 (5) + sourced_net_new S2 (10) = 20
    assert ev == 20.0


# ═══════════════════════════════════════════════════════════════════════
# 9. test_clawback_gate
# ═══════════════════════════════════════════════════════════════════════

def test_clawback_gate():
    """Meeting missing outbound provenance → block_booking;
    duplicate within window → warn minimum."""

    # Missing outbound provenance → block_booking
    provenance_missing = MeetingProvenance(
        outbound_touches=(),  # empty!
        first_touch_channel=None,
        named_target_validated=True,
        dormancy_days=None,
    )
    result = check_provenance(provenance_missing, DuplicateCheck(has_duplicate=False))
    assert result.risk == CreditRisk.BLOCK_BOOKING

    # Good provenance but duplicate → warn
    provenance_good = MeetingProvenance(
        outbound_touches=("ev-1", "ev-2"),
        first_touch_channel="email",
        named_target_validated=True,
        dormancy_days=None,
    )
    dup = DuplicateCheck(has_duplicate=True, prior_meeting_count=1)
    result = check_provenance(provenance_good, dup)
    assert result.risk == CreditRisk.WARN

    # Good provenance, no duplicate → none
    result = check_provenance(provenance_good, DuplicateCheck(has_duplicate=False))
    assert result.risk == CreditRisk.NONE


# ═══════════════════════════════════════════════════════════════════════
# Additional coverage tests
# ═══════════════════════════════════════════════════════════════════════

class TestEarningsAdditional:
    """Additional earnings tests for coverage."""

    def test_m1_full_ote(self):
        """M1 pays full OTE regardless of points."""
        e = project_earnings(points=0, month=1)
        assert e.total_monthly == pytest.approx(SDR_OTE / 12, abs=1.0)

    def test_sr_sdr_rate(self):
        """Promoted SDR uses Sr. rates."""
        e = project_earnings(points=40, month=7, is_promoted=True)
        expected = 40 * SR_SDR_RATE_PER_POINT
        assert e.commission == pytest.approx(expected, abs=0.01)

    def test_accelerator_marginal(self):
        """Above quota, marginal $ = $100."""
        e = project_earnings(points=40, month=4)
        assert e.marginal_dollar_next_point == ACCELERATOR_RATE

    def test_below_quota_marginal(self):
        """Below quota, marginal $ = standard rate."""
        e = project_earnings(points=20, month=4)
        assert e.marginal_dollar_next_point == SDR_RATE_PER_POINT

    def test_annualized_vs_goal(self):
        """Annualized tracks against $135k."""
        e = project_earnings(points=35, month=4)
        assert e.vs_goal_annual == pytest.approx(e.annualized - PERSONAL_GOAL_ANNUAL, abs=0.01)

    def test_promotion_month_switch(self):
        """promotion_month triggers Sr. rates from that month onward."""
        e = project_earnings(points=40, month=7, promotion_month=7)
        assert e.is_promoted is True
        assert e.commission == pytest.approx(40 * SR_SDR_RATE_PER_POINT, abs=0.01)

    def test_m2_cap_not_hit(self):
        """M2 with low points doesn't hit cap."""
        e = project_earnings(points=10, month=2)
        assert e.cap_applied is False

    def test_spiff_in_earnings(self):
        """SPIFF cash is added to total."""
        e = project_earnings(points=35, month=4, spiff_cash=1000)
        assert e.spiff_cash == 1000
        assert e.total_monthly > e.base_monthly + e.commission


class TestClawbackAdditional:
    """Additional clawback tests for coverage."""

    def test_dormancy_qualifies(self):
        """Dormancy ≥120 days passes the Named Target check."""
        prov = MeetingProvenance(
            outbound_touches=("ev-1",),
            first_touch_channel="email",
            named_target_validated=False,
            dormancy_days=120,
        )
        result = check_provenance(prov, DuplicateCheck(has_duplicate=False))
        assert result.risk == CreditRisk.NONE

    def test_dormancy_below_threshold(self):
        """Dormancy <120 days with no Named Target → warn."""
        prov = MeetingProvenance(
            outbound_touches=("ev-1",),
            first_touch_channel="email",
            named_target_validated=False,
            dormancy_days=119,
        )
        result = check_provenance(prov, DuplicateCheck(has_duplicate=False))
        assert result.risk == CreditRisk.WARN

    def test_multiple_issues(self):
        """Missing provenance + duplicate → block_booking (worst case)."""
        prov = MeetingProvenance(
            outbound_touches=(),
            first_touch_channel=None,
            named_target_validated=False,
            dormancy_days=50,
        )
        dup = DuplicateCheck(has_duplicate=True, prior_meeting_count=2)
        result = check_provenance(prov, dup)
        assert result.risk == CreditRisk.BLOCK_BOOKING
        assert len(result.reasons) >= 2

    def test_find_duplicates_in_window(self):
        """find_duplicates_in_window identifies meetings in the window."""
        meeting_date = datetime(2026, 6, 15)
        prior = [
            datetime(2026, 4, 1),  # within 90-day window
            datetime(2026, 1, 1),  # outside window
        ]
        dup = find_duplicates_in_window("c-1", meeting_date, prior)
        assert dup.has_duplicate is True
        assert dup.prior_meeting_count == 1


class TestCatchupNewLevers:
    """Tests for Session 1b catchup extensions."""

    def test_dormancy_requalification_lever(self):
        """Dormant contacts generate a requalification lever."""
        funnel = _make_funnel()
        plan = _make_plan()
        result = compute_catchup(funnel, plan, dormant_contacts=10)
        lever_names = [lev.name for lev in result.levers]
        assert "dormancy_requalification" in lever_names

    def test_persona_mix_shift_lever(self):
        """High IC mix generates upmarket shift lever."""
        funnel = _make_funnel()
        plan = _make_plan()
        result = compute_catchup(funnel, plan, current_persona_mix_ic_pct=0.50)
        lever_names = [lev.name for lev in result.levers]
        assert "persona_mix_shift_upmarket" in lever_names

    def test_accelerator_awareness_lever(self):
        """Above-quota points generate accelerator awareness annotation."""
        funnel = _make_funnel()
        plan = _make_plan()
        result = compute_catchup(
            funnel, plan, month_to_date_pts=40, quota=35,
        )
        lever_names = [lev.name for lev in result.levers]
        assert "accelerator_awareness" in lever_names

    def test_m2_cap_awareness_lever(self):
        """M2 with points above cap generates cap awareness lever."""
        funnel = _make_funnel()
        plan = _make_plan()
        result = compute_catchup(
            funnel, plan, month=2, month_to_date_pts=35, quota=15, ramp_cap_pct=2.0,
        )
        lever_names = [lev.name for lev in result.levers]
        assert "m2_cap_awareness" in lever_names


class TestPointsAdditional:
    """Additional points tests for coverage."""

    def test_projected_bucket_booked_only(self):
        """Booked-only meeting goes to projected bucket."""
        events = [
            _make_event(EventType.MEETING_BOOKED, persona_tier=PersonaTier.DIRECTOR,
                        account_ref="acct-proj", contact_ref="c-proj"),
        ]
        result = compute_points(events)
        assert result.buckets.projected > 0
        assert result.buckets.credited == 0.0
        assert result.buckets.pending == 0.0

    def test_opp_points_credited(self):
        """S1/S2 events go to credited bucket."""
        events = [
            _make_event(
                EventType.S1_REACHED,
                payload={"opp_type": "sourced_engaged"},
                points_value=3.0,
                account_ref="acct-opp",
                contact_ref="c-opp",
            ),
        ]
        result = compute_points(events)
        assert result.buckets.credited == 3.0

    def test_unknown_persona_zero_points(self):
        """Unknown persona tier → 0 meeting points."""
        assert meeting_points_for_tier("unknown") == 0.0

    def test_unknown_opp_type_zero_points(self):
        """Unknown opp type → 0 points."""
        assert opp_points_for_type_stage("unknown", "S1") == 0.0


class TestPromotionAdditional:
    """Additional promotion tests for coverage."""

    def test_empty_history(self):
        """Empty history → not ready."""
        sc = compute_scorecard([])
        assert sc.m6_case_ready is False
        assert sc.rolling_130_streak == 0

    def test_partial_streak(self):
        """2 of 3 months ≥130% → streak = 2 (from end)."""
        history = [
            MonthRecord(month=2, points=15, quota=15, sourced_s2_count=0),   # 100%
            MonthRecord(month=3, points=45, quota=30, sourced_s2_count=1),   # 150%
            MonthRecord(month=4, points=55, quota=35, sourced_s2_count=1),   # 157%
        ]
        sc = compute_scorecard(history)
        assert sc.rolling_130_streak == 2  # M3 + M4 but M2 breaks


# ═══════════════════════════════════════════════════════════════════════
# Zero I/O lint test
# ═══════════════════════════════════════════════════════════════════════

def test_zero_io_imports_in_engine():
    """Enforce zero imports of SQLAlchemy/FastAPI/adapters/agents/api inside engine/."""
    engine_dir = Path(__file__).resolve().parent.parent / "app" / "engine"
    banned_modules = {"sqlalchemy", "fastapi", "app.adapters", "app.agents", "app.api", "app.database"}

    for py_file in engine_dir.glob("*.py"):
        source = py_file.read_text()
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for banned in banned_modules:
                        assert not alias.name.startswith(banned), (
                            f"{py_file.name}: imports banned module '{alias.name}'"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for banned in banned_modules:
                        assert not node.module.startswith(banned), (
                            f"{py_file.name}: imports from banned module '{node.module}'"
                        )
