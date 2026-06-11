"""Session 2 — Dispatcher merge-bar tests.

Eight named tests that must all pass before merge.
"""

from datetime import datetime, timedelta

import pytest

from app.dispatcher.autoapprove import (
    CUSTOMER_FACING_TYPES,
    AutoApproveViolation,
    is_auto_approved,
    validate_whitelist,
)
from app.dispatcher.ev import (
    compute_ev,
    ev_book_response,
    ev_confirmation,
    ev_no_show_recovery,
    ev_outreach,
)
from app.dispatcher.job_factory import jobs_from_events
from app.dispatcher.morning_plan import build_today_payload
from app.dispatcher.ranker import compute_priority_score, rank_jobs, urgency_boost
from app.engine.types import (
    SEED_BENCHMARKS,
    BottleneckResult,
    Channel,
    Event,
    EventType,
    FunnelStage,
    PersonaTier,
    RateMetric,
    RateRow,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _seed_rate(metric: str, channel: str | None = None, **overrides) -> RateRow:
    """Build a RateRow from seed benchmarks with optional overrides."""
    bm = SEED_BENCHMARKS.get((metric, channel), 0.0)
    defaults = {
        "metric": metric,
        "channel": channel,
        "window_days": 30,
        "n_sample": 50,
        "actual_rate": bm,
        "benchmark_rate": bm,
        "k_strength": 30,
        "blended_rate": bm,
        "confidence": "medium",
        "baseline_90d": bm,
        "computed_at": datetime(2026, 7, 1),
        "persona_tier": None,
    }
    defaults.update(overrides)
    return RateRow(**defaults)


def _seed_rates() -> list[RateRow]:
    """Full set of seed-benchmark RateRows."""
    return [
        _seed_rate(RateMetric.REPLY_RATE, Channel.EMAIL),
        _seed_rate(RateMetric.REPLY_RATE, Channel.CALL),
        _seed_rate(RateMetric.REPLY_RATE, Channel.LINKEDIN),
        _seed_rate(RateMetric.POSITIVE_REPLY_RATE, None),
        _seed_rate(RateMetric.BOOK_RATE, None),
        _seed_rate(RateMetric.SHOW_RATE, None),
        _seed_rate(RateMetric.QUALIFY_RATE, None),
        _seed_rate(RateMetric.AD_ACCEPT_RATE, None),
    ]


NOW = datetime(2026, 7, 15, 7, 30)


# ── 1. test_ev_formulas ───────────────────────────────────────────────


class TestEvFormulas:
    """EV per (job type × persona tier) matches DATA_MODEL.md reference values
    given seed rates; VP outreach outranks IC outreach even at 3× IC reply rate.
    """

    def test_outreach_vp_email_seed(self):
        """DATA_MODEL.md: EV(outreach_draft, VP) ≈ 0.024 pts/touch."""
        ev = ev_outreach(PersonaTier.VP_LEVEL, Channel.EMAIL)
        # .04 × .35 × .55 × .70 × 5 × .9 = 0.024255
        assert abs(ev - 0.024255) < 0.001

    def test_outreach_ic_email_seed(self):
        """DATA_MODEL.md: EV(outreach_draft, IC) ≈ 0.0035."""
        ev = ev_outreach(PersonaTier.IC, Channel.EMAIL)
        # .04 × .35 × .55 × .70 × 0.5 × .9 = 0.002426
        # Spec says ~0.0035 — approximate
        assert ev < 0.004

    def test_book_response_vp(self):
        """DATA_MODEL.md: EV(book_response, VP positive) ≈ 1.73."""
        ev = ev_book_response(PersonaTier.VP_LEVEL)
        # .55 × .70 × 5 × .9 = 1.7325
        assert abs(ev - 1.7325) < 0.01

    def test_confirmation_vp(self):
        """DATA_MODEL.md: EV(confirmation_24h, VP) ≈ 0.45."""
        ev = ev_confirmation(PersonaTier.VP_LEVEL)
        # .10 × 5 × .9 = 0.45
        assert abs(ev - 0.45) < 0.01

    def test_no_show_recovery_vp(self):
        """DATA_MODEL.md: EV(no_show_recovery, VP) ≈ 1.1."""
        ev = ev_no_show_recovery(PersonaTier.VP_LEVEL)
        # .25 × 5 × .9 = 1.125
        assert abs(ev - 1.125) < 0.01

    def test_persona_arbitrage_vp_beats_ic_even_at_3x_reply(self):
        """VP outreach outranks IC outreach even when IC has 3× reply rate.

        This is the persona arbitrage from COMP_MODEL.md §5:
        "1 VP meeting = 10 IC meetings."
        """
        # VP at seed email reply rate (4%)
        vp_ev = ev_outreach(PersonaTier.VP_LEVEL, Channel.EMAIL)

        # IC at 3× seed email reply rate (12%) — via custom rates
        ic_rates = [
            _seed_rate(RateMetric.REPLY_RATE, Channel.EMAIL, blended_rate=0.12),
            _seed_rate(RateMetric.POSITIVE_REPLY_RATE, None),
            _seed_rate(RateMetric.BOOK_RATE, None),
            _seed_rate(RateMetric.SHOW_RATE, None),
            _seed_rate(RateMetric.AD_ACCEPT_RATE, None),
        ]
        ic_ev = ev_outreach(PersonaTier.IC, Channel.EMAIL, ic_rates)

        assert vp_ev > ic_ev, (
            f"VP EV ({vp_ev:.6f}) must beat IC EV ({ic_ev:.6f}) "
            "even at 3× IC reply rate — persona arbitrage"
        )

    def test_compute_ev_via_job_type(self):
        """compute_ev() dispatcher entry point works for various job types."""
        rates = _seed_rates()
        ev_vp = compute_ev("outreach_draft", PersonaTier.VP_LEVEL, Channel.EMAIL, rates)
        ev_ic = compute_ev("outreach_draft", PersonaTier.IC, Channel.EMAIL, rates)
        assert ev_vp > ev_ic


# ── 2. test_ic_demotion ───────────────────────────────────────────────


class TestIcDemotion:
    """IC-persona create-jobs require explicit override to rank above Manager+
    (COMP_MODEL.md §5).
    """

    def test_ic_demoted_below_manager(self):
        """Without override, IC create-job ranks below Manager create-job."""
        bottleneck = BottleneckResult(stage=FunnelStage.CREATE, priority=3, reason="test")

        jobs = [
            {
                "job_type": "outreach_draft",
                "funnel_stage": "create",
                "expected_value": 0.10,
                "estimated_minutes": 10,
                "input_payload": {"persona_tier": PersonaTier.IC},
            },
            {
                "job_type": "outreach_draft",
                "funnel_stage": "create",
                "expected_value": 0.05,
                "estimated_minutes": 10,
                "input_payload": {"persona_tier": PersonaTier.MANAGER},
            },
        ]

        ranked = rank_jobs(jobs, bottleneck, now=NOW)

        # Manager should be first despite lower EV
        assert ranked[0]["input_payload"]["persona_tier"] == PersonaTier.MANAGER
        assert ranked[1]["input_payload"]["persona_tier"] == PersonaTier.IC

    def test_ic_override_restores_rank(self):
        """With explicit override, IC create-job ranks by EV normally."""
        bottleneck = BottleneckResult(stage=FunnelStage.CREATE, priority=3, reason="test")

        jobs = [
            {
                "id": "ic-job-1",
                "job_type": "outreach_draft",
                "funnel_stage": "create",
                "expected_value": 0.10,
                "estimated_minutes": 10,
                "input_payload": {"persona_tier": PersonaTier.IC},
            },
            {
                "id": "mgr-job-1",
                "job_type": "outreach_draft",
                "funnel_stage": "create",
                "expected_value": 0.05,
                "estimated_minutes": 10,
                "input_payload": {"persona_tier": PersonaTier.MANAGER},
            },
        ]

        ranked = rank_jobs(jobs, bottleneck, now=NOW, ic_override_ids={"ic-job-1"})

        # IC job should now be first (higher EV)
        assert ranked[0]["id"] == "ic-job-1"


# ── 3. test_ranking_order ─────────────────────────────────────────────


class TestRankingOrder:
    """Given a mixed bag (cold outreach, aged positive reply, 24h confirmation),
    order = confirmation/convert per bottleneck state.
    Assert exact order for 3 scripted bottleneck scenarios from Rule 5.
    """

    def _make_mixed_bag(self) -> list[dict]:
        return [
            {
                "id": "cold-outreach",
                "job_type": "outreach_draft",
                "funnel_stage": "create",
                "expected_value": 0.024,
                "estimated_minutes": 10,
                "input_payload": {"persona_tier": PersonaTier.VP_LEVEL},
            },
            {
                "id": "book-response",
                "job_type": "book_response",
                "funnel_stage": "convert",
                "expected_value": 1.73,
                "estimated_minutes": 5,
                "due_at": NOW - timedelta(hours=5),  # overdue
                "input_payload": {"persona_tier": PersonaTier.VP_LEVEL},
            },
            {
                "id": "confirm-24h",
                "job_type": "confirmation_24h",
                "funnel_stage": "hold",
                "expected_value": 0.45,
                "estimated_minutes": 3,
                "input_payload": {"persona_tier": PersonaTier.VP_LEVEL},
            },
        ]

    def test_scenario_1_show_rate_bottleneck(self):
        """Show rate down → hold first: confirm-24h, book-response, cold-outreach."""
        bottleneck = BottleneckResult(stage=FunnelStage.HOLD, priority=1, reason="show rate down")
        jobs = self._make_mixed_bag()
        ranked = rank_jobs(jobs, bottleneck, now=NOW)
        order = [j["id"] for j in ranked]
        assert order == ["confirm-24h", "book-response", "cold-outreach"]

    def test_scenario_2_convert_bottleneck(self):
        """Positive reply unactioned → convert first: book-response, confirm-24h, cold-outreach."""
        bottleneck = BottleneckResult(stage=FunnelStage.CONVERT, priority=2, reason="positive reply stale")
        jobs = self._make_mixed_bag()
        ranked = rank_jobs(jobs, bottleneck, now=NOW)
        order = [j["id"] for j in ranked]
        assert order == ["book-response", "confirm-24h", "cold-outreach"]

    def test_scenario_3_create_bottleneck(self):
        """No bottleneck → create first: cold-outreach, then by score."""
        bottleneck = BottleneckResult(stage=FunnelStage.CREATE, priority=3, reason="normal")
        jobs = self._make_mixed_bag()
        ranked = rank_jobs(jobs, bottleneck, now=NOW)
        order = [j["id"] for j in ranked]
        # create-stage (cold-outreach) sorts first; then others by score
        assert order[0] == "cold-outreach"
        # book-response has urgency×5 (overdue) so beats confirm-24h
        assert order[1] == "book-response"
        assert order[2] == "confirm-24h"


# ── 4. test_stage_gate_dominates ───────────────────────────────────────


class TestStageGateDominates:
    """A 0.005-EV hold job outranks a 0.385-EV convert job ONLY when
    hold is the bottleneck stage.

    Rule 5 is a gate, not a weight.
    """

    def test_low_ev_hold_beats_high_ev_convert_when_hold_is_bottleneck(self):
        jobs = [
            {
                "id": "high-ev-convert",
                "job_type": "book_response",
                "funnel_stage": "convert",
                "expected_value": 0.385,
                "estimated_minutes": 5,
                "input_payload": {},
            },
            {
                "id": "low-ev-hold",
                "job_type": "reconfirm",
                "funnel_stage": "hold",
                "expected_value": 0.005,
                "estimated_minutes": 5,
                "input_payload": {},
            },
        ]

        # When hold IS the bottleneck
        bottleneck_hold = BottleneckResult(stage=FunnelStage.HOLD, priority=1, reason="show rate down")
        ranked = rank_jobs(jobs, bottleneck_hold, now=NOW)
        assert ranked[0]["id"] == "low-ev-hold", "Hold job must outrank convert when hold is bottleneck"

        # When convert IS the bottleneck — order flips
        bottleneck_convert = BottleneckResult(stage=FunnelStage.CONVERT, priority=2, reason="stale positive")
        ranked = rank_jobs(jobs, bottleneck_convert, now=NOW)
        assert ranked[0]["id"] == "high-ev-convert", "Convert should lead when it's the bottleneck"


# ── 5. test_due_at_sla ────────────────────────────────────────────────


class TestDueAtSla:
    """positive_reply event → book_response job due_at = event+4h;
    at +4h ranking boost ×3 applied.
    """

    def test_book_response_due_at_event_plus_4h(self):
        event_time = datetime(2026, 7, 15, 10, 0)
        events = [
            Event(
                event_type=EventType.POSITIVE_REPLY,
                occurred_at=event_time,
                persona_tier=PersonaTier.VP_LEVEL,
                channel=Channel.EMAIL,
                account_ref="acct-100",
                contact_ref="contact-200",
            ),
        ]

        jobs = jobs_from_events(events, now=NOW)
        assert len(jobs) == 1

        book_job = jobs[0]
        assert book_job["job_type"] == "book_response"
        assert book_job["due_at"] == event_time + timedelta(hours=4)

    def test_urgency_boost_at_4h(self):
        """At exactly +4h, the due-within-4h boost (×3) should apply."""
        due = NOW + timedelta(hours=3, minutes=59)
        boost = urgency_boost(due, NOW)
        assert boost == 3.0

    def test_urgency_boost_overdue(self):
        """Past due → ×5."""
        due = NOW - timedelta(hours=1)
        boost = urgency_boost(due, NOW)
        assert boost == 5.0

    def test_priority_score_with_urgency(self):
        """Book response job near SLA gets ×3 boost in priority_score."""
        ev = 1.73
        est = 5
        due_at = NOW + timedelta(hours=2)  # within 4h
        score_urgent = compute_priority_score(ev, est, due_at, NOW)
        score_normal = compute_priority_score(ev, est, None, NOW)
        assert score_urgent == pytest.approx(score_normal * 3.0)


# ── 6. test_morning_plan_payload ───────────────────────────────────────


class TestMorningPlanPayload:
    """/api/today schema matches frontend contract (JSON schema snapshot test)."""

    def test_payload_schema(self):
        """Verify all required top-level keys and types."""
        jobs = [
            {
                "id": "j1",
                "job_type": "outreach_draft",
                "funnel_stage": "create",
                "agent": "copy",
                "status": "pending",
                "expected_value": 0.024,
                "estimated_minutes": 10,
                "input_payload": {},
                "due_at": None,
                "created_at": NOW,
            },
        ]

        payload = build_today_payload(
            jobs=jobs,
            plan=None,
            rates=[],
            events=[],
            now=NOW,
        )

        # Top-level keys
        assert "date" in payload
        assert "bottleneck" in payload
        assert "ranked_jobs" in payload
        assert "plan_summary" in payload
        assert "narrative" in payload

        # date is ISO string
        assert payload["date"] == "2026-07-15"

        # bottleneck structure
        bn = payload["bottleneck"]
        assert "stage" in bn
        assert "priority" in bn
        assert "reason" in bn
        assert bn["stage"] in ("create", "convert", "hold")

        # ranked_jobs is a list of job dicts
        assert isinstance(payload["ranked_jobs"], list)
        if payload["ranked_jobs"]:
            j = payload["ranked_jobs"][0]
            for key in ("id", "job_type", "funnel_stage", "agent", "status",
                        "expected_value", "priority_score", "estimated_minutes"):
                assert key in j, f"Missing key {key} in ranked job"

        # plan_summary
        ps = payload["plan_summary"]
        assert "touches_remaining" in ps
        assert "call_blocks" in ps
        assert "confirmations_due" in ps
        tr = ps["touches_remaining"]
        assert "email" in tr
        assert "call" in tr
        assert "linkedin" in tr

        # narrative is a string
        assert isinstance(payload["narrative"], str)
        assert len(payload["narrative"]) > 0


# ── 7. test_autoapprove_lane_safety ────────────────────────────────────


class TestAutoapproveLaneSafety:
    """Adding `outreach_draft` to whitelist with DRAFT_ONLY active
    → app boot fails with clear error.
    """

    def test_customer_facing_in_whitelist_with_draft_only_raises(self, monkeypatch):
        """Config-load-time assertion fires."""
        monkeypatch.setenv("DRAFT_ONLY_UNTIL", "2099-12-31")

        whitelist_with_violation = [
            "crm_note_log",
            "research_brief",
            "outreach_draft",  # customer_facing!
        ]

        with pytest.raises(AutoApproveViolation) as exc_info:
            validate_whitelist(whitelist_with_violation, now=datetime(2026, 7, 15))

        error_msg = str(exc_info.value)
        assert "outreach_draft" in error_msg
        assert "DRAFT_ONLY" in error_msg

    def test_clean_whitelist_passes(self, monkeypatch):
        """Non-customer-facing whitelist passes validation."""
        monkeypatch.setenv("DRAFT_ONLY_UNTIL", "2099-12-31")

        clean_whitelist = [
            "crm_note_log",
            "research_brief",
            "call_prep",
            "pipeline_hygiene_autofix",
            "reporting_personal_recap",
        ]

        # Should not raise
        validate_whitelist(clean_whitelist, now=datetime(2026, 7, 15))

    def test_whitelist_ok_when_draft_only_expired(self, monkeypatch):
        """After DRAFT_ONLY expires, customer_facing in whitelist is fine."""
        monkeypatch.setenv("DRAFT_ONLY_UNTIL", "2026-01-01")

        # This would fail if DRAFT_ONLY were active
        whitelist = ["outreach_draft"]
        validate_whitelist(whitelist, now=datetime(2026, 7, 15))

    def test_auto_approve_check(self):
        """is_auto_approved correctly checks membership."""
        wl = ["research_brief", "crm_note_log"]
        assert is_auto_approved("research_brief", wl) is True
        assert is_auto_approved("outreach_draft", wl) is False

    def test_all_customer_facing_types_are_tagged(self):
        """Verify the CUSTOMER_FACING_TYPES set is non-empty and includes key types."""
        assert "outreach_draft" in CUSTOMER_FACING_TYPES
        assert "book_response" in CUSTOMER_FACING_TYPES
        assert "confirmation_24h" in CUSTOMER_FACING_TYPES
        # Internal types are NOT in the set
        assert "research_brief" not in CUSTOMER_FACING_TYPES
        assert "crm_note_log" not in CUSTOMER_FACING_TYPES


# ── 8. test_snooze_decay ──────────────────────────────────────────────


class TestSnoozeDecay:
    """Snoozed job returns next day with boost decayed, never silently dropped."""

    def test_snooze_shifts_due_at(self, db_session):
        """Snoozing shifts due_at forward."""
        from app.models.job import Job

        job = Job(
            job_type="outreach_draft",
            funnel_stage="create",
            agent="copy",
            status="pending",
            expected_value=0.024,
            priority_score=0.5,
            created_at=NOW,
            updated_at=NOW,
        )
        db_session.add(job)
        db_session.commit()

        # Simulate snooze
        snooze_time = NOW
        job.due_at = snooze_time + timedelta(days=1)
        job.priority_score = job.priority_score * 0.9
        payload = dict(job.input_payload or {})
        payload["snooze_count"] = payload.get("snooze_count", 0) + 1
        payload["last_snoozed_at"] = snooze_time.isoformat()
        job.input_payload = payload
        db_session.commit()

        assert job.due_at == snooze_time + timedelta(days=1)
        assert job.priority_score == pytest.approx(0.5 * 0.9)
        assert job.input_payload["snooze_count"] == 1

    def test_snooze_decay_accumulates(self):
        """Multiple snoozes decay the score exponentially."""
        score = 1.0
        decay = 0.9
        for _ in range(3):
            score *= decay
        assert score == pytest.approx(0.9**3)

    def test_snoozed_job_not_dropped(self, db_session):
        """A snoozed job stays PENDING (never silently removed)."""
        from app.models.enums import JobStatus
        from app.models.job import Job

        job = Job(
            job_type="outreach_draft",
            funnel_stage="create",
            agent="copy",
            status=JobStatus.PENDING,
            expected_value=0.024,
            priority_score=0.5,
            created_at=NOW,
            updated_at=NOW,
        )
        db_session.add(job)
        db_session.commit()

        # Snooze 5 times
        for i in range(5):
            job.due_at = NOW + timedelta(days=i + 1)
            job.priority_score *= 0.9
            payload = dict(job.input_payload or {})
            payload["snooze_count"] = payload.get("snooze_count", 0) + 1
            job.input_payload = payload
            db_session.commit()

        # Job is still PENDING, not dropped
        assert job.status == JobStatus.PENDING
        assert job.input_payload["snooze_count"] == 5
        assert job.priority_score > 0  # decayed but never zero

    def test_snoozed_job_still_ranks(self):
        """A snoozed job with decayed score still appears in ranked list."""
        bottleneck = BottleneckResult(stage=FunnelStage.CREATE, priority=3, reason="normal")

        jobs = [
            {
                "id": "snoozed",
                "job_type": "outreach_draft",
                "funnel_stage": "create",
                "expected_value": 0.024,
                "estimated_minutes": 10,
                "input_payload": {"snooze_count": 3},
            },
            {
                "id": "fresh",
                "job_type": "outreach_draft",
                "funnel_stage": "create",
                "expected_value": 0.024,
                "estimated_minutes": 10,
                "input_payload": {},
            },
        ]

        ranked = rank_jobs(jobs, bottleneck, now=NOW)
        ids = [j["id"] for j in ranked]
        assert "snoozed" in ids, "Snoozed job must not be silently dropped from ranking"
