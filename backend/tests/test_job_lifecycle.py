"""test_job_lifecycle — legal transitions pass, illegal raise."""

from datetime import datetime

import pytest

from app.models.enums import JobStatus
from app.models.job import InvalidTransitionError, Job


def _make_job(**kwargs) -> Job:
    defaults = {
        "job_type": "research_brief",
        "funnel_stage": "create",
        "agent": "research_brief",
        "status": JobStatus.PENDING,
        "expected_value": 0.0,
        "priority_score": 0.0,
        "created_at": datetime(2026, 6, 1),
        "updated_at": datetime(2026, 6, 1),
    }
    defaults.update(kwargs)
    return Job(**defaults)


def test_legal_transitions_pending_to_in_progress(db_session):
    j = _make_job()
    db_session.add(j)
    db_session.commit()
    j.transition_to(JobStatus.IN_PROGRESS)
    assert j.status == JobStatus.IN_PROGRESS


def test_legal_transitions_full_happy_path(db_session):
    j = _make_job()
    db_session.add(j)
    db_session.commit()

    j.transition_to(JobStatus.IN_PROGRESS)
    j.transition_to(JobStatus.AWAITING_APPROVAL)
    j.transition_to(JobStatus.APPROVED)
    j.transition_to(JobStatus.WRITTEN_BACK)
    assert j.status == JobStatus.WRITTEN_BACK


def test_legal_transitions_rejection_path(db_session):
    j = _make_job()
    db_session.add(j)
    db_session.commit()

    j.transition_to(JobStatus.IN_PROGRESS)
    j.transition_to(JobStatus.AWAITING_APPROVAL)
    j.transition_to(JobStatus.REJECTED)
    assert j.status == JobStatus.REJECTED


def test_legal_transitions_skip(db_session):
    j = _make_job()
    db_session.add(j)
    db_session.commit()
    j.transition_to(JobStatus.SKIPPED)
    assert j.status == JobStatus.SKIPPED


def test_illegal_transition_pending_to_approved(db_session):
    j = _make_job()
    db_session.add(j)
    db_session.commit()
    with pytest.raises(InvalidTransitionError):
        j.transition_to(JobStatus.APPROVED)


def test_illegal_transition_written_back_to_pending(db_session):
    j = _make_job(status=JobStatus.WRITTEN_BACK)
    db_session.add(j)
    db_session.commit()
    with pytest.raises(InvalidTransitionError):
        j.transition_to(JobStatus.PENDING)


def test_illegal_transition_rejected_to_anything(db_session):
    j = _make_job(status=JobStatus.REJECTED)
    db_session.add(j)
    db_session.commit()
    for target in JobStatus:
        if target != JobStatus.REJECTED:
            with pytest.raises(InvalidTransitionError):
                j.transition_to(target)
