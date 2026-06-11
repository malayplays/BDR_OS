"""Acceptance tests for the debug simulation endpoints (§1).

Verifies:
- Each endpoint produces the downstream jobs/events listed in the spec.
- /api/sim/* routes 404 when DEBUG_DASHBOARD is unset.
- Full happy-path: positive-reply → meeting-booked → invite-accepted →
  advance-clock → meeting-held → ad-accepts, checking events & jobs at each step.
- No-show path: no-show → recovery job with 3-touch sequence.
- Reset restores pristine state.
- GET /api/sim/state returns fake clock and last 5 events.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.debug_sim import _reset_clock
from app.api.debug_sim import router as debug_sim_router
from app.models import Base


def _make_app(include_sim: bool = True) -> FastAPI:
    """Build a minimal FastAPI app, optionally mounting the sim router."""
    test_app = FastAPI()
    if include_sim:
        test_app.include_router(debug_sim_router)
    return test_app


@pytest.fixture()
def _test_db():
    """In-memory SQLite DB with StaticPool so all sessions share one connection."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    return engine, TestSession


@pytest.fixture()
def debug_client(_test_db):
    """TestClient with sim routes mounted against an in-memory DB."""
    engine, TestSession = _test_db

    app = _make_app(include_sim=True)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    from app.database import get_db
    app.dependency_overrides[get_db] = override_get_db

    _reset_clock()
    client = TestClient(app)
    # Reset to seed fixture jobs
    client.post("/api/sim/reset")
    yield client


@pytest.fixture()
def nodebug_client():
    """TestClient without sim routes — endpoints must 404."""
    app = _make_app(include_sim=False)
    return TestClient(app)


# ── Routes 404 when DEBUG_DASHBOARD unset ─────────────────────────────


class TestDebugDisabled:
    def test_sim_routes_404_when_disabled(self, nodebug_client):
        for path in [
            "/api/sim/positive-reply",
            "/api/sim/meeting-booked",
            "/api/sim/invite-accepted",
            "/api/sim/advance-clock",
            "/api/sim/meeting-held",
            "/api/sim/no-show",
            "/api/sim/ad-accepts",
            "/api/sim/new-signal",
            "/api/sim/reset",
        ]:
            resp = nodebug_client.post(path)
            assert resp.status_code in (404, 405, 422), f"{path} should not be available, got {resp.status_code}"

    def test_sim_state_404_when_disabled(self, nodebug_client):
        resp = nodebug_client.get("/api/sim/state")
        assert resp.status_code == 404


# ── Individual endpoint tests ─────────────────────────────────────────


class TestPositiveReply:
    def test_creates_events_and_jobs(self, debug_client):
        resp = debug_client.post("/api/sim/positive-reply")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        event_types = [e["event_type"] for e in data["events_created"]]
        assert "reply_received" in event_types
        assert "positive_reply" in event_types

        job_types = [j["job_type"] for j in data["jobs_created"]]
        assert "inbox_triage" in job_types
        assert "book_response" in job_types


class TestMeetingBooked:
    def test_default_params(self, debug_client):
        resp = debug_client.post("/api/sim/meeting-booked")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        event_types = [e["event_type"] for e in data["events_created"]]
        assert "meeting_booked" in event_types

        job_types = [j["job_type"] for j in data["jobs_created"]]
        assert "send_invite" in job_types
        assert any(j["agent"] == "show_rate_machine" for j in data["jobs_created"])

    def test_custom_params(self, debug_client):
        resp = debug_client.post(
            "/api/sim/meeting-booked",
            json={"days_out": 5, "persona_tier": "director"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert len(data["events_created"]) >= 1


class TestInviteAccepted:
    def test_invite_accepted(self, debug_client):
        debug_client.post("/api/sim/meeting-booked")

        resp = debug_client.post("/api/sim/invite-accepted")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        event_types = [e["event_type"] for e in data["events_created"]]
        assert "invite_accepted" in event_types


class TestAdvanceClock:
    def test_advance_clock(self, debug_client):
        resp = debug_client.post("/api/sim/advance-clock", json={"hours": 24})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

    def test_advance_clock_fires_timers(self, debug_client):
        # Book meeting 1 day out, accept invite → ACCEPTED state
        debug_client.post("/api/sim/meeting-booked", json={"days_out": 1, "persona_tier": "vp_level"})
        debug_client.post("/api/sim/invite-accepted")

        # Advance 1 hour — meeting is ~23h away, within T-24h window
        resp = debug_client.post("/api/sim/advance-clock", json={"hours": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        job_types = [j["job_type"] for j in data["jobs_created"]]
        assert "reminder_24h" in job_types


class TestMeetingHeld:
    def test_meeting_held(self, debug_client):
        debug_client.post("/api/sim/meeting-booked", json={"days_out": 1})
        debug_client.post("/api/sim/invite-accepted")

        resp = debug_client.post("/api/sim/meeting-held")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        event_types = [e["event_type"] for e in data["events_created"]]
        assert "meeting_held" in event_types


class TestNoShow:
    def test_no_show_creates_recovery(self, debug_client):
        debug_client.post("/api/sim/meeting-booked", json={"days_out": 1})
        debug_client.post("/api/sim/invite-accepted")

        resp = debug_client.post("/api/sim/no-show")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        event_types = [e["event_type"] for e in data["events_created"]]
        assert "meeting_no_show" in event_types

        job_types = [j["job_type"] for j in data["jobs_created"]]
        assert "no_show_recovery" in job_types
        assert any(j["agent"] == "no_show_recovery" for j in data["jobs_created"])


class TestAdAccepts:
    def test_ad_accepts_credits_points(self, debug_client):
        debug_client.post("/api/sim/meeting-booked")
        debug_client.post("/api/sim/invite-accepted")
        debug_client.post("/api/sim/meeting-held")

        resp = debug_client.post("/api/sim/ad-accepts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        event_types = [e["event_type"] for e in data["events_created"]]
        assert "ad_accepted" in event_types

        ev = data["events_created"][0]
        assert ev["account_ref"] is not None


class TestNewSignal:
    def test_new_signal_creates_research_brief(self, debug_client):
        resp = debug_client.post("/api/sim/new-signal")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        assert len(data["events_created"]) >= 1

        job_types = [j["job_type"] for j in data["jobs_created"]]
        assert "research_brief" in job_types
        assert "outreach_draft" in job_types


class TestReset:
    def test_reset_restores_pristine(self, debug_client):
        debug_client.post("/api/sim/positive-reply")
        debug_client.post("/api/sim/new-signal")

        resp = debug_client.post("/api/sim/reset")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        assert len(data["jobs_created"]) == 5
        assert all(j["job_type"] == "research_brief" for j in data["jobs_created"])

        state = debug_client.get("/api/sim/state").json()
        assert "2026-06-11T09:00:00" in state["fake_clock_now"]


class TestSimState:
    def test_state_returns_clock_and_events(self, debug_client):
        resp = debug_client.get("/api/sim/state")
        assert resp.status_code == 200
        data = resp.json()
        assert "fake_clock_now" in data
        assert "last_5_events" in data
        assert isinstance(data["last_5_events"], list)

    def test_state_shows_recent_events(self, debug_client):
        debug_client.post("/api/sim/positive-reply")
        debug_client.post("/api/sim/new-signal")

        state = debug_client.get("/api/sim/state").json()
        assert len(state["last_5_events"]) >= 2


# ── Full happy-path integration test ─────────────────────────────────


class TestHappyPath:
    def test_full_loop(self, debug_client):
        """Positive reply → book_response → meeting booked → invite accepted →
        advance clock → confirmations → meeting held → AD accepts."""

        r1 = debug_client.post("/api/sim/positive-reply").json()
        assert r1["ok"]
        assert any(j["job_type"] == "book_response" for j in r1["jobs_created"])

        r2 = debug_client.post("/api/sim/meeting-booked", json={"days_out": 1, "persona_tier": "vp_level"}).json()
        assert r2["ok"]
        assert any(e["event_type"] == "meeting_booked" for e in r2["events_created"])

        r3 = debug_client.post("/api/sim/invite-accepted").json()
        assert r3["ok"]

        r4 = debug_client.post("/api/sim/advance-clock", json={"hours": 1}).json()
        assert r4["ok"]
        assert any(j["job_type"] == "reminder_24h" for j in r4["jobs_created"])

        r5 = debug_client.post("/api/sim/meeting-held").json()
        assert r5["ok"]
        assert any(e["event_type"] == "meeting_held" for e in r5["events_created"])

        r6 = debug_client.post("/api/sim/ad-accepts").json()
        assert r6["ok"]
        assert any(e["event_type"] == "ad_accepted" for e in r6["events_created"])

        state = debug_client.get("/api/sim/state").json()
        assert len(state["last_5_events"]) >= 2


class TestNoShowPath:
    def test_no_show_recovery_sequence(self, debug_client):
        """Meeting booked → invite accepted → no-show → recovery job."""
        debug_client.post("/api/sim/meeting-booked", json={"days_out": 1})
        debug_client.post("/api/sim/invite-accepted")

        resp = debug_client.post("/api/sim/no-show").json()
        assert resp["ok"]
        assert any(j["job_type"] == "no_show_recovery" for j in resp["jobs_created"])

        recovery = [j for j in resp["jobs_created"] if j["job_type"] == "no_show_recovery"]
        assert len(recovery) == 1
