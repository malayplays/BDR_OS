"""Simulation harness — drives the whole BDR-OS system on a fake clock against fixtures.

Replays ``event_timeline.json`` day by day, runs schedulers, auto-approves queue
items via API (simulating Malay's 3 daily check-ins), and asserts system behavior.

Usage (from backend/):
    python -m pytest tests/e2e/ -v
    RUN_LIVE=1 python -m pytest tests/e2e/ -v   # optional live leg
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.mock.calendar import MockCalendarAdapter
from app.adapters.mock.crm import MockCRMAdapter
from app.adapters.mock.email import MockEmailAdapter
from app.adapters.mock.enrichment import MockEnrichmentAdapter
from app.models import Base
from app.models.conversion_rates import ConversionRates
from app.models.enums import (
    Confidence,
    JobStatus,
    ReplanReason,
    VerdictResult,
)
from app.models.event_log import EventLog
from app.models.funnel_state import FunnelState
from app.models.goal import Goal
from app.models.job import Job
from app.models.meeting_state import MeetingRecord, MeetingState
from app.models.plan import Plan
from app.policy.guardrails import WriteBackAction, check
from app.schemas import Verdict

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "fixtures"

# ── Deterministic LLM mock ────────────────────────────────────────────


class DeterministicLLMMock:
    """Returns canned agent outputs keyed by agent_name, no real API calls."""

    CANNED: dict[str, dict] = {
        "research_brief": {
            "company_snapshot": ["Fast-growing dev-tools co", "200+ eng headcount", "Series B funded"],
            "why_now": "Eng team growing 40% QoQ. Onboarding velocity pain is now.",
            "who_to_contact": [
                {"name": "VP Person", "title": "VP Engineering", "reason": "New leader audits tooling"},
            ],
            "angle": "Frame Devin as headcount-multiplier during hiring crunch.",
            "landmines": "Uses Copilot Enterprise already.",
            "compound_candidate": True,
            "requalified_contacts": [],
            "confidence": 0.85,
            "needs_human_because": None,
        },
        "copy": {
            "email_variants": [
                {
                    "angle": "signal-direct",
                    "subject": "Re: developer productivity",
                    "body": "Saw the backend openings — Devin keeps velocity flat through hiring waves. Worth 15 min?",
                },
                {
                    "angle": "problem-led",
                    "subject": "Re: onboarding bottleneck",
                    "body": "New hires ramp faster when Devin handles the grunt-work backlog. Quick demo?",
                },
                {
                    "angle": "social-proof",
                    "subject": "Re: what teams like yours do",
                    "body": "Teams scaling 40%+ use Devin to keep shipping. 15 min to see how?",
                },
            ],
            "call_opener": "Hi — saw the growth. Quick q about onboarding velocity.",
            "linkedin_note": "Growing fast? Devin keeps eng velocity flat through hiring waves.",
            "rationale": "Signal-direct strongest for hiring_surge.",
            "confidence": 0.82,
            "needs_human_because": None,
        },
        "inbox_triage": {
            "classification": "positive",
            "urgency": "now",
            "extracted": {},
            "next_job": "book_response",
            "confidence": 0.95,
            "needs_human_because": None,
        },
        "book_response": {
            "reply_in_thread": "Great — Thu 2:00 or Fri 10:30 (15 min)? Grabbing one: [link].",
            "slots_offered": 2,
            "confidence": 0.90,
            "needs_human_because": None,
        },
        "show_rate_machine": {
            "draft_body": "Tomorrow at 2 I'll show how Devin handles a real ticket. Bring one if you want.",
            "state_transition": "CONFIRMED_24H",
            "confidence": 0.88,
            "needs_human_because": None,
        },
        "no_show_recovery": {
            "t_plus_10_draft": "No worries — calendars happen. Two quick options: Thu 2:00, Fri 10:30.",
            "sequence": [
                {"delay_days": 1, "channel": "email", "body": "New proof point on dev velocity."},
                {"delay_days": 3, "channel": "call", "body": "Quick call to reconnect?"},
                {"delay_days": 7, "channel": "email", "body": "Door's open if timing improves."},
            ],
            "confidence": 0.80,
            "needs_human_because": None,
        },
        "call_prep": {
            "who": "VP Eng, ex-Stripe. 2 lines.",
            "why_now": "Hiring surge signal.",
            "last_interaction": "Replied positively on 6/2.",
            "goal_of_call": "Book next steps.",
            "likely_objections": ["We have Copilot", "Budget locked"],
            "the_one_thing_to_show": "Real ticket delegation.",
            "confidence": 0.85,
            "needs_human_because": None,
        },
        "crm_scribe": {
            "summary": ["Discovery complete", "Pain confirmed", "Next steps agreed", "Eval <6mo", "ICP fit"],
            "sql_checklist": {
                "icp_fit": True,
                "relevant_title": True,
                "expressed_pain": True,
                "confirmed_need": True,
                "next_steps_agreed": True,
                "eval_timeline_6mo": True,
                "facts_verified": True,
            },
            "three_whys": {
                "anything": "They need dev velocity tooling.",
                "now": "Hiring surge creates urgency.",
                "windsurf_devin": "Devin handles whole tickets, not just autocomplete.",
            },
            "next_steps": [{"action": "Send proposal", "owner": "malay", "due": "2026-06-20"}],
            "crm_fields_patch": {"stage": "S1"},
            "provenance_note": "5 outbound touches, named target validated, 0 days dormant.",
            "confidence": 0.90,
            "needs_human_because": None,
        },
        "pipeline_hygiene": {
            "auto_fixed": [],
            "proposed": [
                {
                    "issue": "Booked meeting missing invite acceptance",
                    "proposed_job": "reconfirm",
                    "evidence": "cal-005 invite not accepted, 3 days out",
                }
            ],
            "confidence": 0.75,
            "needs_human_because": None,
        },
        "reporting": {
            "personal_recap": "3 held (target 3), 5 booked next week. Reply rate 2.8%.",
            "manager_draft": "Week summary: 3 held, on pace. Show rate 80%. Risk: 2 meetings >5d out.",
            "promotion_scorecard": {"attainment_streak": 0, "sourced_s2": 0, "months_above_sr": 0},
            "earnings": {"mtd": 0.0, "projected_month": 0.0, "annualized": 70000.0},
            "confidence": 0.88,
            "needs_human_because": None,
        },
    }

    def get_output(self, agent_name: str, job_input: dict | None = None) -> dict:
        base = dict(self.CANNED.get(agent_name, {"confidence": 0.5, "needs_human_because": "unknown agent"}))
        return base


# ── Simulation Clock ──────────────────────────────────────────────────


class SimClock:
    """Fake clock for deterministic time progression."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def today(self) -> date:
        return self._now.date()

    def advance_hours(self, hours: float) -> None:
        self._now += timedelta(hours=hours)

    def advance_days(self, days: int) -> None:
        self._now += timedelta(days=days)

    def set(self, dt: datetime) -> None:
        self._now = dt


# ── Event Injector ────────────────────────────────────────────────────


def load_timeline() -> list[dict]:
    path = FIXTURES_DIR / "event_timeline.json"
    return json.loads(path.read_text())


def load_fixture(name: str) -> dict:
    path = FIXTURES_DIR / name
    return json.loads(path.read_text())


# ── Points Calculator (COMP_MODEL.md §2) ─────────────────────────────

PERSONA_POINTS: dict[str, float] = {
    "global_c_suite": 8.0,
    "vp_level": 5.0,
    "director": 3.0,
    "manager": 1.0,
    "ic": 0.5,
}


def calc_points(persona_tier: str) -> float:
    return PERSONA_POINTS.get(persona_tier, 0.0)


# ── Earnings Projector (COMP_MODEL.md §6) ─────────────────────────────

BASE_SALARY = 70_000.0
RATE_PER_POINT = 71.43
ACCELERATOR_RATE = 100.0
QUOTA = 35.0


def project_earnings(month: int, points: float, spiffs: float = 0.0) -> dict:
    """project(month) = base/12 + min(pts, quota)×rate + max(pts−quota,0)×100 + spiffs

    Ramp-aware: M1 guarantee, M2 cap at 200%.
    """
    monthly_base = BASE_SALARY / 12.0

    if month == 1:
        # M1 = 100% OTE guaranteed
        variable = 30_000.0 / 12.0
        return {
            "base": monthly_base,
            "variable": variable,
            "spiffs": 0.0,
            "total": monthly_base + variable,
            "ramp_note": "M1 guaranteed",
        }

    # Ramp quotas
    if month == 2:
        effective_quota = 15.0
        cap_pct = 2.0
    elif month == 3:
        effective_quota = 30.0
        cap_pct = None
    else:
        effective_quota = QUOTA
        cap_pct = None

    base_variable = min(points, effective_quota) * RATE_PER_POINT
    accel_variable = max(points - effective_quota, 0.0) * ACCELERATOR_RATE

    total_variable = base_variable + accel_variable

    if cap_pct is not None:
        max_variable = effective_quota * RATE_PER_POINT * cap_pct
        total_variable = min(total_variable, max_variable)

    total = monthly_base + total_variable + spiffs
    return {
        "base": monthly_base,
        "variable": total_variable,
        "spiffs": spiffs,
        "total": total,
        "annualized": total * 12,
    }


# ── Promotion Scorecard (COMP_MODEL.md §7) ────────────────────────────


def promotion_scorecard(monthly_points: list[float], sourced_s2: int) -> dict:
    sr_quota = 40.0
    streak_130 = 0
    for pts in reversed(monthly_points):
        if pts >= QUOTA * 1.3:
            streak_130 += 1
        else:
            break
    months_above_sr = sum(1 for p in monthly_points if p >= sr_quota)
    return {
        "attainment_streak_130pct": streak_130,
        "sourced_s2_count": sourced_s2,
        "months_above_sr_quota": months_above_sr,
    }


# ── SimulationHarness ─────────────────────────────────────────────────


class SimulationHarness:
    """Drives the whole BDR-OS system on a fake clock against fixtures."""

    def __init__(
        self,
        *,
        start_date: datetime | None = None,
        strategic_accounts: list[str] | None = None,
        draft_only_until: str = "2099-12-31",
        max_outbound_per_day: int = 40,
    ) -> None:
        self.clock = SimClock(start_date or datetime(2026, 6, 9, 7, 0))
        self.llm = DeterministicLLMMock()

        # In-memory DB
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        # Mock adapters with spy capability
        self.email_adapter = MockEmailAdapter()
        self.calendar_adapter = MockCalendarAdapter()
        self.crm_adapter = MockCRMAdapter()
        self.enrichment_adapter = MockEnrichmentAdapter()

        # Policy env
        self._strategic_accounts = strategic_accounts or ["acct-001", "acct-002", "acct-003"]
        self._draft_only_until = draft_only_until
        self._max_outbound_per_day = max_outbound_per_day

        # Tracking
        self.jobs_created: list[Job] = []
        self.events_ingested: list[EventLog] = []
        self.approvals: list[dict] = []
        self.narrative: list[str] = []
        self._daily_outbound_count = 0

    def db(self) -> Session:
        return self.Session()

    def _policy_env(self) -> dict[str, str]:
        return {
            "STRATEGIC_ACCOUNTS": json.dumps(self._strategic_accounts),
            "DRAFT_ONLY_UNTIL": self._draft_only_until,
            "MAX_NEW_OUTBOUND_PER_DAY": str(self._max_outbound_per_day),
        }

    def check_policy(self, action: WriteBackAction) -> Verdict:
        """Run guardrails.check() with sim env vars patched."""
        orig = {}
        for k, v in self._policy_env().items():
            orig[k] = os.environ.get(k)
            os.environ[k] = v
        try:
            return check(action)
        finally:
            for k, v in orig.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    # ── Event ingestion ───────────────────────────────────────────────

    def ingest_event(
        self,
        db: Session,
        event_type: str,
        account_ref: str,
        occurred_at: datetime | None = None,
        *,
        persona_tier: str | None = None,
        points_value: float | None = None,
        channel: str | None = None,
        contact_ref: str | None = None,
        job_id: str | None = None,
        source: str = "mock",
        payload: dict | None = None,
    ) -> EventLog:
        ev = EventLog(
            id=str(uuid.uuid4()),
            event_type=event_type,
            persona_tier=persona_tier,
            points_value=points_value,
            channel=channel,
            account_ref=account_ref,
            contact_ref=contact_ref,
            job_id=job_id,
            occurred_at=occurred_at or self.clock.now(),
            source=source,
            payload=payload or {},
        )
        db.add(ev)
        db.commit()
        self.events_ingested.append(ev)
        return ev

    # ── Job lifecycle ─────────────────────────────────────────────────

    def create_job(
        self,
        db: Session,
        *,
        job_type: str,
        agent: str,
        funnel_stage: str = "create",
        account_ref: str | None = None,
        contact_ref: str | None = None,
        expected_value: float = 0.0,
        priority_score: float = 0.0,
        input_payload: dict | None = None,
        is_customer_facing: bool = False,
        trigger: dict | None = None,
        due_at: datetime | None = None,
    ) -> Job:
        job = Job(
            id=str(uuid.uuid4()),
            job_type=job_type,
            funnel_stage=funnel_stage,
            agent=agent,
            trigger=trigger or {},
            account_ref=account_ref,
            contact_ref=contact_ref,
            status=JobStatus.PENDING,
            expected_value=expected_value,
            priority_score=priority_score,
            input_payload=input_payload or {},
            due_at=due_at,
        )
        db.add(job)
        db.commit()
        self.jobs_created.append(job)
        self.narrative.append(
            f"[{self.clock.now().isoformat()}] JOB CREATED: {job_type} ({agent}) "
            f"for {account_ref or 'system'}"
        )
        return job

    def run_agent(self, db: Session, job: Job) -> dict:
        """Simulate agent execution with deterministic LLM mock."""
        job.status = JobStatus.IN_PROGRESS
        db.commit()

        output = self.llm.get_output(job.agent, job.input_payload)
        job.output = output
        job.status = JobStatus.AWAITING_APPROVAL
        db.commit()

        self.narrative.append(
            f"[{self.clock.now().isoformat()}] AGENT RAN: {job.agent} → "
            f"confidence={output.get('confidence', 'N/A')}"
        )
        return output

    def approve_job(self, db: Session, job: Job, decided_by: str = "malay") -> Verdict | None:
        """Simulate approval check-in. Returns Verdict if write-back attempted."""
        if job.status != JobStatus.AWAITING_APPROVAL:
            return None

        job.status = JobStatus.APPROVED
        job.approval = {
            "decided_by": decided_by,
            "decided_at": self.clock.now().isoformat(),
        }
        db.commit()

        self.approvals.append({"job_id": job.id, "job_type": job.job_type, "at": self.clock.now().isoformat()})
        self.narrative.append(
            f"[{self.clock.now().isoformat()}] APPROVED: {job.job_type} (job {job.id[:8]})"
        )

        # Attempt write-back through policy
        is_cf = job.job_type in {
            "outreach_draft", "book_response", "reconfirm",
            "reminder_24h", "reminder_am", "no_show_recovery",
            "pull_in_offer", "reschedule",
        }
        action = WriteBackAction(
            action_type="create_draft",
            account_ref=job.account_ref,
            contact_ref=job.contact_ref,
            is_customer_facing=is_cf,
            daily_new_outbound_count=self._daily_outbound_count,
        )
        verdict = self.check_policy(action)

        if verdict.result == VerdictResult.ALLOW:
            job.status = JobStatus.WRITTEN_BACK
            job.write_back_ref = f"wb-{uuid.uuid4().hex[:8]}"
            db.commit()
            self.narrative.append(
                f"[{self.clock.now().isoformat()}] WRITTEN BACK: {job.job_type}"
            )
            if is_cf:
                self._daily_outbound_count += 1
        elif verdict.result == VerdictResult.BLOCK:
            job.policy_flags = {"blocked": True, "reason": verdict.reason, "flag": verdict.policy_flag}
            db.commit()
            self.narrative.append(
                f"[{self.clock.now().isoformat()}] BLOCKED: {job.job_type} — {verdict.reason}"
            )

        return verdict

    def reject_job(self, db: Session, job: Job, reason: str = "rejected") -> None:
        if job.status != JobStatus.AWAITING_APPROVAL:
            return
        job.status = JobStatus.REJECTED
        job.approval = {
            "decided_by": "malay",
            "decided_at": self.clock.now().isoformat(),
            "rejection_reason": reason,
        }
        db.commit()

    # ── Batch approval (simulating Malay's 3 daily check-ins) ─────────

    def batch_approve_pending(self, db: Session) -> list[Verdict]:
        """Approve all awaiting_approval jobs (simulating a check-in)."""
        jobs = db.query(Job).filter(Job.status == JobStatus.AWAITING_APPROVAL).all()
        verdicts = []
        for job in jobs:
            v = self.approve_job(db, job)
            if v:
                verdicts.append(v)
        return verdicts

    # ── Scheduler simulations ─────────────────────────────────────────

    def run_nightly_rates(self, db: Session) -> None:
        """Simulate nightly 02:00 rates recompute."""
        self.narrative.append(f"[{self.clock.now().isoformat()}] SCHEDULER: nightly rates recompute")

    def run_weekly_cascade(self, db: Session, goal: Goal) -> Plan:
        """Simulate Sunday 21:00 weekly cascade."""
        plan = Plan(
            id=str(uuid.uuid4()),
            goal_id=goal.id,
            week_start=self.clock.today(),
            weekly_bookings_required=5.0,
            weekly_held_target=3.0,
            daily_allocation={"email": 30, "call": 10, "linkedin": 5},
            rates_snapshot={"show_rate": 0.70, "reply_rate": 0.04, "book_rate": 0.55},
            replan_reason=ReplanReason.WEEKLY_CASCADE,
        )
        db.add(plan)
        db.commit()
        self.narrative.append(f"[{self.clock.now().isoformat()}] SCHEDULER: weekly cascade → plan {plan.id[:8]}")
        return plan

    def run_dispatcher_morning(self, db: Session) -> list[Job]:
        """Simulate 07:30 dispatcher morning plan."""
        self.narrative.append(f"[{self.clock.now().isoformat()}] DISPATCHER: morning plan")
        self._daily_outbound_count = 0
        return []

    # ── Meeting state machine ─────────────────────────────────────────

    def create_meeting_record(
        self,
        db: Session,
        *,
        account_ref: str,
        contact_ref: str | None = None,
        event_ref: str | None = None,
        persona_tier: str = "vp_level",
    ) -> MeetingRecord:
        rec = MeetingRecord(
            id=str(uuid.uuid4()),
            event_ref=event_ref or f"cal-{uuid.uuid4().hex[:8]}",
            account_ref=account_ref,
            contact_ref=contact_ref,
            state=MeetingState.BOOKED,
            meeting_start=self.clock.now() + timedelta(days=3),
            meeting_end=self.clock.now() + timedelta(days=3, minutes=30),
        )
        db.add(rec)
        db.commit()
        return rec

    def advance_meeting(self, db: Session, rec: MeetingRecord, target_state: str) -> None:
        rec.state = target_state
        db.commit()

    # ── Goal / FunnelState helpers ────────────────────────────────────

    def create_goal(
        self,
        db: Session,
        *,
        target_value: float = 35.0,
        period_start: date | None = None,
        period_end: date | None = None,
    ) -> Goal:
        goal = Goal(
            id=str(uuid.uuid4()),
            unit="points",
            target_value=target_value,
            period_type="month",
            period_start=period_start or date(2026, 6, 1),
            period_end=period_end or date(2026, 6, 30),
        )
        db.add(goal)
        db.commit()
        return goal

    def update_funnel_state(
        self,
        db: Session,
        goal: Goal,
        *,
        points: dict | None = None,
        pace_gap: float = 0.0,
        at_risk: bool = False,
        pct_goal: float = 0.0,
        pct_period_elapsed: float = 0.0,
    ) -> FunnelState:
        fs = FunnelState(
            id=str(uuid.uuid4()),
            goal_id=goal.id,
            as_of=self.clock.now(),
            points=points or {"credited": 0, "pending": 0, "projected": 0},
            pace_gap=pace_gap,
            at_risk=at_risk,
            pct_goal=pct_goal,
            pct_period_elapsed=pct_period_elapsed,
        )
        db.add(fs)
        db.commit()
        return fs

    def seed_rates(self, db: Session, rates: dict[str, float] | None = None) -> list[ConversionRates]:
        """Seed benchmark conversion rates."""
        defaults = {
            "reply_rate": 0.04,
            "positive_reply_rate": 0.35,
            "book_rate": 0.55,
            "show_rate": 0.70,
            "qualify_rate": 0.60,
            "ad_accept_rate": 0.90,
        }
        used = {**defaults, **(rates or {})}
        created = []
        for metric_str, val in used.items():
            cr = ConversionRates(
                id=str(uuid.uuid4()),
                metric=metric_str,
                benchmark_rate=val,
                blended_rate=val,
                confidence=Confidence.LOW if (rates and metric_str in rates) else Confidence.MEDIUM,
                n_sample=0,
            )
            db.add(cr)
            created.append(cr)
        db.commit()
        return created

    # ── Timeline replay ───────────────────────────────────────────────

    def replay_timeline_day(self, db: Session, target_date: date, timeline: list[dict]) -> list[EventLog]:
        """Ingest all events from timeline for a given day."""
        day_events = [
            e for e in timeline
            if e["occurred_at"][:10] == target_date.isoformat()
        ]
        ingested = []
        for e in day_events:
            ev = self.ingest_event(
                db,
                event_type=e["event_type"],
                account_ref=e["account_ref"],
                occurred_at=datetime.fromisoformat(e["occurred_at"]),
                persona_tier=e.get("persona_tier"),
                channel=e.get("channel"),
                contact_ref=e.get("contact_ref"),
                source=e.get("source", "mock"),
                payload=e.get("payload", {}),
            )
            ingested.append(ev)
        return ingested

    # ── Day simulation ────────────────────────────────────────────────

    def simulate_day(
        self,
        db: Session,
        timeline: list[dict],
        goal: Goal,
        *,
        extra_jobs: list[dict] | None = None,
    ) -> dict:
        """Simulate one full day: ingest events, run dispatcher, create+run jobs,
        batch-approve at 3 check-ins."""
        day = self.clock.today()
        result: dict[str, Any] = {"date": day.isoformat(), "events": [], "jobs_run": [], "approvals": []}

        # 02:00 — nightly rates
        self.clock.set(datetime.combine(day, datetime.min.time()).replace(hour=2))
        self.run_nightly_rates(db)

        # 07:30 — dispatcher morning plan
        self.clock.set(datetime.combine(day, datetime.min.time()).replace(hour=7, minute=30))
        self.run_dispatcher_morning(db)

        # Ingest day's events
        ingested = self.replay_timeline_day(db, day, timeline)
        result["events"] = [{"type": e.event_type, "account": e.account_ref} for e in ingested]

        # Create extra jobs if provided
        if extra_jobs:
            for jd in extra_jobs:
                job = self.create_job(db, **jd)
                output = self.run_agent(db, job)
                result["jobs_run"].append({"job_type": jd["job_type"], "output": output})

        # 3 check-ins: 11:30, 15:30, 17:30
        for hour in [11, 15, 17]:
            self.clock.set(datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=30))
            verdicts = self.batch_approve_pending(db)
            result["approvals"].extend([v.model_dump() for v in verdicts])

        return result

    # ── Narrative output ──────────────────────────────────────────────

    def print_narrative(self) -> str:
        return "\n".join(self.narrative)
