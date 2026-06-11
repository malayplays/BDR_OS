"""test_models_roundtrip — create/read all six objects."""

from datetime import date, datetime

from app.models.conversion_rates import ConversionRates
from app.models.event_log import EventLog
from app.models.funnel_state import FunnelState
from app.models.goal import Goal
from app.models.job import Job
from app.models.plan import Plan


def test_goal_roundtrip(db_session):
    g = Goal(
        unit="points",
        target_value=35.0,
        period_type="month",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        edited_at=datetime(2026, 6, 1),
    )
    db_session.add(g)
    db_session.commit()
    fetched = db_session.get(Goal, g.id)
    assert fetched is not None
    assert fetched.unit == "points"
    assert fetched.target_value == 35.0


def test_event_log_roundtrip(db_session):
    e = EventLog(
        event_type="touch_sent",
        channel="email",
        account_ref="acct-001",
        contact_ref="con-001",
        occurred_at=datetime(2026, 6, 1, 10, 0),
        source="mock",
        payload={"test": True},
    )
    db_session.add(e)
    db_session.commit()
    fetched = db_session.get(EventLog, e.id)
    assert fetched is not None
    assert fetched.event_type == "touch_sent"
    assert fetched.payload == {"test": True}


def test_conversion_rates_roundtrip(db_session):
    cr = ConversionRates(
        metric="reply_rate",
        channel="email",
        window_days=30,
        n_sample=50,
        actual_rate=0.04,
        benchmark_rate=0.04,
        k_strength=30,
        blended_rate=0.04,
        confidence="medium",
        computed_at=datetime(2026, 6, 1),
    )
    db_session.add(cr)
    db_session.commit()
    fetched = db_session.get(ConversionRates, cr.id)
    assert fetched is not None
    assert fetched.blended_rate == 0.04


def test_funnel_state_roundtrip(db_session):
    g = Goal(
        unit="points",
        target_value=35.0,
        period_type="month",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        edited_at=datetime(2026, 6, 1),
    )
    db_session.add(g)
    db_session.commit()

    fs = FunnelState(
        goal_id=g.id,
        as_of=datetime(2026, 6, 10),
        counts={"touches": {"email": 100, "call": 50, "linkedin": 30}},
        points={"credited": 5.0, "pending": 2.0, "projected": 10.0},
        pct_goal=0.2,
        pct_period_elapsed=0.33,
        pace_gap=-0.13,
    )
    db_session.add(fs)
    db_session.commit()
    fetched = db_session.get(FunnelState, fs.id)
    assert fetched is not None
    assert fetched.pace_gap == -0.13


def test_plan_roundtrip(db_session):
    g = Goal(
        unit="points",
        target_value=35.0,
        period_type="month",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        edited_at=datetime(2026, 6, 1),
    )
    db_session.add(g)
    db_session.commit()

    p = Plan(
        goal_id=g.id,
        week_start=date(2026, 6, 9),
        weekly_bookings_required=5.0,
        weekly_held_target=3.5,
        daily_allocation={"monday": {"email_touches": 20}},
        rates_snapshot={"reply_rate_email": 0.04},
        capacity={"business_days": 5},
        generated_at=datetime(2026, 6, 8),
        replan_reason="weekly_cascade",
    )
    db_session.add(p)
    db_session.commit()
    fetched = db_session.get(Plan, p.id)
    assert fetched is not None
    assert fetched.weekly_bookings_required == 5.0


def test_job_roundtrip(db_session):
    j = Job(
        job_type="research_brief",
        funnel_stage="create",
        agent="research_brief",
        trigger={"kind": "manual", "ref": "acct-001"},
        account_ref="acct-001",
        status="pending",
        expected_value=0.024,
        priority_score=0.5,
        created_at=datetime(2026, 6, 1),
        updated_at=datetime(2026, 6, 1),
    )
    db_session.add(j)
    db_session.commit()
    fetched = db_session.get(Job, j.id)
    assert fetched is not None
    assert fetched.job_type == "research_brief"
    assert fetched.status == "pending"
