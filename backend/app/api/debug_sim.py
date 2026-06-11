"""DEBUG ONLY — Simulation endpoints for the debug dashboard.

Mounted at /api/sim only when DEBUG_DASHBOARD=true env var is set.
Uses the real ingestion path (EventLog insert + downstream job creation).
Reuses the e2e fake-clock harness from Session 12.
"""

from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.enums import EventType, FunnelStage, JobStatus
from app.models.event_log import EventLog
from app.models.job import Job
from app.models.meeting_state import MeetingRecord, MeetingState

router = APIRouter(prefix="/api/sim", tags=["debug-sim"])

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "fixtures"

# ── Fake clock (shared state for this debug session) ──────────────────

_fake_clock: datetime = datetime(2026, 6, 11, 9, 0, 0)


def _now() -> datetime:
    return _fake_clock


def _reset_clock() -> None:
    global _fake_clock
    _fake_clock = datetime(2026, 6, 11, 9, 0, 0)


def _advance_clock(hours: int) -> None:
    global _fake_clock
    _fake_clock += timedelta(hours=hours)


# ── Fixture data helpers ──────────────────────────────────────────────

_SIGNAL_KINDS = [
    "hiring_surge",
    "eng_leadership_change",
    "funding",
    "dev_velocity_pain",
    "new_product_launch",
    "expansion",
]


def _load_crm_fixtures() -> dict:
    path = FIXTURES_DIR / "crm.json"
    return json.loads(path.read_text())


def _pick_fixture_contact(db: Session) -> dict:
    """Pick a random fixture contact that has an active thread (job in system)."""
    crm = _load_crm_fixtures()
    contacts = crm.get("contacts", [])
    if not contacts:
        return {"ref": "con-001", "account_ref": "acct-001", "email": "contact1@acmecorp.com"}
    return random.choice(contacts)


# ── Response schema ───────────────────────────────────────────────────


class SimResponse(BaseModel):
    ok: bool = True
    events_created: list[dict[str, Any]] = Field(default_factory=list)
    jobs_created: list[dict[str, Any]] = Field(default_factory=list)


class MeetingBookedRequest(BaseModel):
    days_out: int = 3
    persona_tier: str = "vp_level"


class AdvanceClockRequest(BaseModel):
    hours: int


class SimStateResponse(BaseModel):
    fake_clock_now: str
    last_5_events: list[dict[str, Any]] = Field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────


def _create_event(
    db: Session,
    event_type: str,
    account_ref: str,
    *,
    contact_ref: str | None = None,
    persona_tier: str | None = None,
    points_value: float | None = None,
    channel: str | None = None,
    job_id: str | None = None,
    payload: dict | None = None,
) -> EventLog:
    """Insert an EventLog record through the real ingestion path."""
    ev = EventLog(
        id=str(uuid.uuid4()),
        event_type=event_type,
        persona_tier=persona_tier,
        points_value=points_value,
        channel=channel,
        account_ref=account_ref,
        contact_ref=contact_ref,
        job_id=job_id,
        occurred_at=_now(),
        source="mock",
        payload=payload or {},
    )
    db.add(ev)
    db.flush()
    return ev


def _create_job(
    db: Session,
    *,
    job_type: str,
    agent: str,
    funnel_stage: str = "create",
    account_ref: str | None = None,
    contact_ref: str | None = None,
    expected_value: float = 0.0,
    priority_score: float = 0.5,
    trigger: dict | None = None,
    due_at: datetime | None = None,
    input_payload: dict | None = None,
) -> Job:
    """Create a Job record."""
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
    db.flush()
    return job


def _event_to_dict(ev: EventLog) -> dict:
    return {
        "id": ev.id,
        "event_type": ev.event_type,
        "account_ref": ev.account_ref,
        "contact_ref": ev.contact_ref,
        "occurred_at": ev.occurred_at.isoformat() if ev.occurred_at else None,
    }


def _job_to_dict(job: Job) -> dict:
    return {
        "id": job.id,
        "job_type": job.job_type,
        "agent": job.agent,
        "status": job.status,
        "account_ref": job.account_ref,
        "contact_ref": job.contact_ref,
    }


# ── Points table (COMP_MODEL §2) ─────────────────────────────────────

PERSONA_POINTS: dict[str, float] = {
    "global_c_suite": 8.0,
    "vp_level": 5.0,
    "director": 3.0,
    "manager": 1.0,
    "ic": 0.5,
}


# ── Endpoints ─────────────────────────────────────────────────────────


@router.post("/positive-reply", response_model=SimResponse)
def sim_positive_reply(db: Session = Depends(get_db)):
    """Picks a random fixture contact with an active thread → reply_received +
    positive_reply events → triage chain fires → book_response job appears."""
    contact = _pick_fixture_contact(db)
    account_ref = contact["account_ref"]
    contact_ref = contact["ref"]

    events = []
    jobs = []

    # reply_received event
    ev1 = _create_event(
        db,
        EventType.REPLY_RECEIVED,
        account_ref,
        contact_ref=contact_ref,
        channel="email",
    )
    events.append(ev1)

    # positive_reply event
    ev2 = _create_event(
        db,
        EventType.POSITIVE_REPLY,
        account_ref,
        contact_ref=contact_ref,
        channel="email",
    )
    events.append(ev2)

    # Triage chain fires → inbox_triage job
    triage_job = _create_job(
        db,
        job_type="inbox_triage",
        agent="inbox_triage",
        funnel_stage=FunnelStage.CONVERT,
        account_ref=account_ref,
        contact_ref=contact_ref,
        trigger={"event_id": ev2.id, "type": "positive_reply"},
    )
    jobs.append(triage_job)

    # book_response job (chained from triage)
    book_job = _create_job(
        db,
        job_type="book_response",
        agent="book_response",
        funnel_stage=FunnelStage.CONVERT,
        account_ref=account_ref,
        contact_ref=contact_ref,
        expected_value=1.73,
        priority_score=0.9,
        trigger={"event_id": ev2.id, "type": "positive_reply", "chained_from": "inbox_triage"},
        due_at=_now() + timedelta(hours=4),
    )
    jobs.append(book_job)

    db.commit()
    return SimResponse(
        events_created=[_event_to_dict(e) for e in events],
        jobs_created=[_job_to_dict(j) for j in jobs],
    )


@router.post("/meeting-booked", response_model=SimResponse)
def sim_meeting_booked(body: MeetingBookedRequest | None = None, db: Session = Depends(get_db)):
    """meeting_booked event → show-rate machine enters BOOKED, invite job fires."""
    if body is None:
        body = MeetingBookedRequest()

    contact = _pick_fixture_contact(db)
    account_ref = contact["account_ref"]
    contact_ref = contact["ref"]
    persona_tier = body.persona_tier
    points = PERSONA_POINTS.get(persona_tier, 1.0)

    events = []
    jobs = []

    # meeting_booked event
    ev = _create_event(
        db,
        EventType.MEETING_BOOKED,
        account_ref,
        contact_ref=contact_ref,
        persona_tier=persona_tier,
        points_value=points,
        payload={"days_out": body.days_out, "persona_tier": persona_tier},
    )
    events.append(ev)

    # Create MeetingRecord → BOOKED state
    meeting = MeetingRecord(
        id=str(uuid.uuid4()),
        event_ref=f"cal-{uuid.uuid4().hex[:8]}",
        account_ref=account_ref,
        contact_ref=contact_ref,
        state=MeetingState.BOOKED,
        signal_kind="sim_signal",
        meeting_start=_now() + timedelta(days=body.days_out),
        meeting_end=_now() + timedelta(days=body.days_out, minutes=30),
    )
    db.add(meeting)
    db.flush()

    # Invite job fires (show_rate_machine: BOOKED→INVITE_SENT)
    invite_job = _create_job(
        db,
        job_type="send_invite",
        agent="show_rate_machine",
        funnel_stage=FunnelStage.HOLD,
        account_ref=account_ref,
        contact_ref=contact_ref,
        expected_value=points,
        trigger={"event_id": ev.id, "meeting_id": meeting.id, "type": "meeting_booked"},
        input_payload={"meeting_id": meeting.id, "persona_tier": persona_tier},
    )
    jobs.append(invite_job)

    db.commit()
    return SimResponse(
        events_created=[_event_to_dict(e) for e in events],
        jobs_created=[_job_to_dict(j) for j in jobs],
    )


@router.post("/invite-accepted", response_model=SimResponse)
def sim_invite_accepted(db: Session = Depends(get_db)):
    """Most recent booked meeting → invite_accepted."""
    meeting = (
        db.query(MeetingRecord)
        .filter(MeetingRecord.state.in_([MeetingState.BOOKED, MeetingState.INVITE_SENT]))
        .order_by(MeetingRecord.created_at.desc())
        .first()
    )

    events = []
    jobs = []

    if meeting:
        meeting.state = MeetingState.ACCEPTED
        ev = _create_event(
            db,
            EventType.INVITE_ACCEPTED,
            meeting.account_ref,
            contact_ref=meeting.contact_ref,
            payload={"meeting_id": meeting.id},
        )
        events.append(ev)
    else:
        # No meeting found — create a standalone event for fixture contact
        contact = _pick_fixture_contact(db)
        ev = _create_event(
            db,
            EventType.INVITE_ACCEPTED,
            contact["account_ref"],
            contact_ref=contact["ref"],
        )
        events.append(ev)

    db.commit()
    return SimResponse(
        events_created=[_event_to_dict(e) for e in events],
        jobs_created=[_job_to_dict(j) for j in jobs],
    )


@router.post("/advance-clock", response_model=SimResponse)
def sim_advance_clock(body: AdvanceClockRequest, db: Session = Depends(get_db)):
    """{hours: int} — advances the scheduler's fake clock so timers fire."""
    _advance_clock(body.hours)

    events = []
    jobs = []

    # Check for meetings where T-24h/morning-of confirmations are due
    meetings = db.query(MeetingRecord).filter(
        MeetingRecord.state.in_([MeetingState.ACCEPTED, MeetingState.CONFIRMED_24H])
    ).all()

    for meeting in meetings:
        if meeting.meeting_start is None:
            continue
        time_until = meeting.meeting_start - _now()
        hours_until = time_until.total_seconds() / 3600

        if hours_until <= 24 and meeting.state == MeetingState.ACCEPTED:
            # T-24h confirmation due
            meeting.state = MeetingState.CONFIRMED_24H
            ev = _create_event(
                db,
                "timer_fired",
                meeting.account_ref,
                contact_ref=meeting.contact_ref,
                payload={"timer": "T-24h", "meeting_id": meeting.id},
            )
            events.append(ev)
            job = _create_job(
                db,
                job_type="reminder_24h",
                agent="show_rate_machine",
                funnel_stage=FunnelStage.HOLD,
                account_ref=meeting.account_ref,
                contact_ref=meeting.contact_ref,
                trigger={"type": "timer_T-24h", "meeting_id": meeting.id},
                input_payload={"meeting_id": meeting.id},
            )
            jobs.append(job)

        elif hours_until <= 4 and meeting.state == MeetingState.CONFIRMED_24H:
            # Morning-of confirmation
            meeting.state = MeetingState.CONFIRMED_AM
            ev = _create_event(
                db,
                "timer_fired",
                meeting.account_ref,
                contact_ref=meeting.contact_ref,
                payload={"timer": "morning_of", "meeting_id": meeting.id},
            )
            events.append(ev)
            job = _create_job(
                db,
                job_type="reminder_am",
                agent="show_rate_machine",
                funnel_stage=FunnelStage.HOLD,
                account_ref=meeting.account_ref,
                contact_ref=meeting.contact_ref,
                trigger={"type": "timer_morning_of", "meeting_id": meeting.id},
                input_payload={"meeting_id": meeting.id},
            )
            jobs.append(job)

    db.commit()
    return SimResponse(
        events_created=[_event_to_dict(e) for e in events],
        jobs_created=[_job_to_dict(j) for j in jobs],
    )


@router.post("/meeting-held", response_model=SimResponse)
def sim_meeting_held(db: Session = Depends(get_db)):
    """Most recent confirmed meeting → meeting_held."""
    meeting = (
        db.query(MeetingRecord)
        .filter(MeetingRecord.state.in_([
            MeetingState.CONFIRMED_24H,
            MeetingState.CONFIRMED_AM,
            MeetingState.ACCEPTED,
        ]))
        .order_by(MeetingRecord.created_at.desc())
        .first()
    )

    events = []
    jobs = []

    if meeting:
        meeting.state = MeetingState.HELD
        ev = _create_event(
            db,
            EventType.MEETING_HELD,
            meeting.account_ref,
            contact_ref=meeting.contact_ref,
            persona_tier=meeting.signal_kind,  # pass through for tracing
            payload={"meeting_id": meeting.id},
        )
        events.append(ev)
    else:
        contact = _pick_fixture_contact(db)
        ev = _create_event(
            db,
            EventType.MEETING_HELD,
            contact["account_ref"],
            contact_ref=contact["ref"],
        )
        events.append(ev)

    db.commit()
    return SimResponse(
        events_created=[_event_to_dict(e) for e in events],
        jobs_created=[_job_to_dict(j) for j in jobs],
    )


@router.post("/no-show", response_model=SimResponse)
def sim_no_show(db: Session = Depends(get_db)):
    """Most recent confirmed meeting → meeting_no_show → recovery agent fires."""
    meeting = (
        db.query(MeetingRecord)
        .filter(MeetingRecord.state.in_([
            MeetingState.CONFIRMED_24H,
            MeetingState.CONFIRMED_AM,
            MeetingState.ACCEPTED,
        ]))
        .order_by(MeetingRecord.created_at.desc())
        .first()
    )

    events = []
    jobs = []

    if meeting:
        meeting.state = MeetingState.NO_SHOW
        account_ref = meeting.account_ref
        contact_ref = meeting.contact_ref
    else:
        contact = _pick_fixture_contact(db)
        account_ref = contact["account_ref"]
        contact_ref = contact["ref"]

    ev = _create_event(
        db,
        EventType.MEETING_NO_SHOW,
        account_ref,
        contact_ref=contact_ref,
        payload={"meeting_id": meeting.id if meeting else None},
    )
    events.append(ev)

    # Recovery agent fires — 3-touch sequence as a single job
    recovery_job = _create_job(
        db,
        job_type="no_show_recovery",
        agent="no_show_recovery",
        funnel_stage=FunnelStage.HOLD,
        account_ref=account_ref,
        contact_ref=contact_ref,
        trigger={"event_id": ev.id, "type": "meeting_no_show"},
        input_payload={
            "meeting_id": meeting.id if meeting else None,
            "sequence": [
                {"delay": "T+10min", "channel": "email"},
                {"delay": "+1d", "channel": "email"},
                {"delay": "+3d", "channel": "call"},
            ],
        },
    )
    jobs.append(recovery_job)

    db.commit()
    return SimResponse(
        events_created=[_event_to_dict(e) for e in events],
        jobs_created=[_job_to_dict(j) for j in jobs],
    )


@router.post("/ad-accepts", response_model=SimResponse)
def sim_ad_accepts(db: Session = Depends(get_db)):
    """Most recent held meeting → ad_accepted → points move pending→credited."""
    meeting = (
        db.query(MeetingRecord)
        .filter(MeetingRecord.state == MeetingState.HELD)
        .order_by(MeetingRecord.created_at.desc())
        .first()
    )

    events = []
    jobs = []

    if meeting:
        account_ref = meeting.account_ref
        contact_ref = meeting.contact_ref
    else:
        contact = _pick_fixture_contact(db)
        account_ref = contact["account_ref"]
        contact_ref = contact["ref"]

    # Determine points value from meeting context or default VP
    points = PERSONA_POINTS.get("vp_level", 5.0)

    ev = _create_event(
        db,
        EventType.AD_ACCEPTED,
        account_ref,
        contact_ref=contact_ref,
        points_value=points,
        persona_tier="vp_level",
        payload={"meeting_id": meeting.id if meeting else None, "points_credited": points},
    )
    events.append(ev)

    db.commit()
    return SimResponse(
        events_created=[_event_to_dict(e) for e in events],
        jobs_created=[_job_to_dict(j) for j in jobs],
    )


@router.post("/new-signal", response_model=SimResponse)
def sim_new_signal(db: Session = Depends(get_db)):
    """Injects a fixture signal (rotate kinds) → research_brief job fires."""
    crm = _load_crm_fixtures()
    accounts = crm.get("accounts", [])
    account = random.choice(accounts) if accounts else {"ref": "acct-001", "name": "Acmecorp"}
    signal_kind = random.choice(_SIGNAL_KINDS)

    events = []
    jobs = []

    # Signal event (using a generic event type; signals don't have a dedicated EventType
    # but they trigger research_brief jobs through the enrichment path)
    ev = _create_event(
        db,
        "signal_detected",
        account["ref"],
        payload={
            "signal_kind": signal_kind,
            "strength": round(random.uniform(0.5, 0.95), 2),
            "evidence": f"{signal_kind} detected at {account.get('name', account['ref'])}",
        },
    )
    events.append(ev)

    # research_brief job fires
    rb_job = _create_job(
        db,
        job_type="research_brief",
        agent="research_brief",
        funnel_stage=FunnelStage.CREATE,
        account_ref=account["ref"],
        trigger={"type": "enrichment_signal", "signal_kind": signal_kind, "event_id": ev.id},
        input_payload={"signal_kind": signal_kind, "account_name": account.get("name")},
    )
    jobs.append(rb_job)

    # Outreach draft job chained from research_brief
    contacts = [c for c in crm.get("contacts", []) if c["account_ref"] == account["ref"]]
    contact = contacts[0] if contacts else None
    outreach_job = _create_job(
        db,
        job_type="outreach_draft",
        agent="copy",
        funnel_stage=FunnelStage.CREATE,
        account_ref=account["ref"],
        contact_ref=contact["ref"] if contact else None,
        expected_value=0.024,
        trigger={"type": "research_brief_complete", "chained_from": "research_brief"},
        input_payload={"signal_kind": signal_kind},
    )
    jobs.append(outreach_job)

    db.commit()
    return SimResponse(
        events_created=[_event_to_dict(e) for e in events],
        jobs_created=[_job_to_dict(j) for j in jobs],
    )


@router.post("/reset", response_model=SimResponse)
def sim_reset(db: Session = Depends(get_db)):
    """Restores fixture DB to pristine state."""
    # Delete all sim-created records
    db.query(EventLog).delete()
    db.query(Job).delete()
    db.query(MeetingRecord).delete()
    db.commit()

    # Reset clock
    _reset_clock()

    # Re-seed fixture jobs (same logic as main.py startup)
    crm = _load_crm_fixtures()
    jobs_created = []
    for acct in crm.get("accounts", [])[:5]:
        job = Job(
            id=str(uuid.uuid4()),
            job_type="research_brief",
            funnel_stage="create",
            agent="research_brief",
            trigger={"kind": "manual", "ref": acct["ref"]},
            account_ref=acct["ref"],
            status=JobStatus.PENDING,
            expected_value=0.024,
            priority_score=0.5,
        )
        db.add(job)
        jobs_created.append(job)

    db.commit()
    return SimResponse(
        events_created=[],
        jobs_created=[_job_to_dict(j) for j in jobs_created],
    )


@router.get("/state", response_model=SimStateResponse)
def sim_state(db: Session = Depends(get_db)):
    """Returns fake clock + last 5 events for the frontend's event ticker."""
    last_events = (
        db.query(EventLog)
        .order_by(EventLog.occurred_at.desc())
        .limit(5)
        .all()
    )
    return SimStateResponse(
        fake_clock_now=_now().isoformat(),
        last_5_events=[_event_to_dict(e) for e in last_events],
    )
