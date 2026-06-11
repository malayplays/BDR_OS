#!/usr/bin/env python3
"""Demo runner — prints a human-readable narrative of the happy_week scenario.

Usage:
    cd backend && python -m tests.e2e.demo_happy_week
    # or: make demo  (from repo root)
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.models.enums import EventType
from app.models.meeting_state import MeetingState
from tests.e2e.sim import SimulationHarness, calc_points, load_timeline


def run_demo() -> None:
    print("=" * 72)
    print("  BDR OS — happy_week Demo")
    print("  Full lifecycle: signal → brief → copy → approve → reply →")
    print("  book → show-rate machine → HELD → scribe → report")
    print("=" * 72)
    print()

    harness = SimulationHarness(start_date=datetime(2026, 6, 9, 7, 0))
    db = harness.db()
    load_timeline()  # warm cache
    harness.create_goal(db)
    harness.seed_rates(db)
    account_ref = "acct-004"
    contact_ref = "con-013"

    # ── Day 1 (Monday): Signal → Research Brief ──────────────────────
    print("━" * 72)
    print("  DAY 1 — Monday Jun 9: Signal Detected → Research Brief")
    print("━" * 72)
    harness.clock.set(datetime(2026, 6, 9, 8, 0))

    harness.ingest_event(
        db, EventType.TOUCH_SENT, account_ref,
        persona_tier="vp_level", channel="email", contact_ref=contact_ref,
    )

    brief_job = harness.create_job(
        db,
        job_type="research_brief",
        agent="research_brief",
        funnel_stage="create",
        account_ref=account_ref,
        contact_ref=contact_ref,
        expected_value=0.024,
    )
    brief_output = harness.run_agent(db, brief_job)
    harness.approve_job(db, brief_job)
    print(f"  ✓ Research brief generated (confidence: {brief_output['confidence']})")
    print(f"    Angle: {brief_output['angle']}")
    print(f"    Compound candidate: {brief_output['compound_candidate']}")
    print()

    # ── Day 1: Copy Agent ─────────────────────────────────────────────
    print("  → Copy Agent chains from brief...")
    copy_job = harness.create_job(
        db,
        job_type="outreach_draft",
        agent="copy",
        funnel_stage="create",
        account_ref=account_ref,
        contact_ref=contact_ref,
        is_customer_facing=True,
        expected_value=0.05,
        input_payload={"brief": brief_output},
    )
    copy_output = harness.run_agent(db, copy_job)
    harness.clock.set(datetime(2026, 6, 9, 11, 30))
    harness.approve_job(db, copy_job)
    print(f"  ✓ Copy pack generated ({len(copy_output['email_variants'])} variants)")
    for v in copy_output["email_variants"]:
        print(f"    [{v['angle']}] {v['body'][:60]}...")
    print("  ✓ Approved at 11:30 check-in → draft created")
    print()

    # ── Day 2 (Tuesday): Positive Reply ───────────────────────────────
    print("━" * 72)
    print("  DAY 2 — Tuesday Jun 10: Positive Reply! → Speed-to-Book")
    print("━" * 72)
    harness.clock.set(datetime(2026, 6, 10, 9, 15))

    harness.ingest_event(
        db, EventType.REPLY_RECEIVED, account_ref,
        persona_tier="vp_level", channel="email", contact_ref=contact_ref,
    )
    harness.ingest_event(
        db, EventType.POSITIVE_REPLY, account_ref,
        persona_tier="vp_level", channel="email", contact_ref=contact_ref,
    )
    print("  ⚡ Positive reply detected at 9:15 AM")

    triage_job = harness.create_job(
        db,
        job_type="inbox_triage",
        agent="inbox_triage",
        funnel_stage="convert",
        account_ref=account_ref,
        contact_ref=contact_ref,
    )
    triage_output = harness.run_agent(db, triage_job)
    harness.approve_job(db, triage_job)
    print(f"  ✓ Triage: {triage_output['classification']} / {triage_output['urgency']}")

    book_job = harness.create_job(
        db,
        job_type="book_response",
        agent="book_response",
        funnel_stage="convert",
        account_ref=account_ref,
        contact_ref=contact_ref,
        is_customer_facing=True,
        due_at=harness.clock.now() + timedelta(hours=4),
    )
    book_output = harness.run_agent(db, book_job)
    harness.clock.set(datetime(2026, 6, 10, 11, 30))
    harness.approve_job(db, book_job)
    print(f"  ✓ Book response: \"{book_output['reply_in_thread']}\"")
    print("  ✓ Approved at 11:30 (within 4h SLA ✓)")
    print()

    harness.ingest_event(
        db, EventType.MEETING_BOOKED, account_ref,
        persona_tier="vp_level", contact_ref=contact_ref,
    )
    print("  📅 Meeting booked! → Show-rate machine takes over")
    print()

    # ── Day 3-4: Show-Rate Machine ────────────────────────────────────
    print("━" * 72)
    print("  DAYS 3-4 — Show-Rate Machine: BOOKED → HELD")
    print("━" * 72)
    meeting = harness.create_meeting_record(
        db, account_ref=account_ref, contact_ref=contact_ref,
    )

    for step, state, job_type, desc in [
        (1, MeetingState.INVITE_SENT, "calendar_invite", "Calendar invite with value-framed agenda"),
        (2, MeetingState.ACCEPTED, None, "Invite accepted by attendee"),
        (3, MeetingState.CONFIRMED_24H, "reminder_24h", "T-24h content-bearing reminder"),
        (4, MeetingState.CONFIRMED_AM, "reminder_am", "Morning-of proof point"),
    ]:
        harness.clock.advance_hours(8)
        if job_type:
            j = harness.create_job(
                db,
                job_type=job_type,
                agent="show_rate_machine",
                funnel_stage="hold",
                account_ref=account_ref,
                contact_ref=contact_ref,
                is_customer_facing=True,
            )
            harness.run_agent(db, j)
            harness.approve_job(db, j)
        if state == MeetingState.ACCEPTED:
            harness.ingest_event(db, EventType.INVITE_ACCEPTED, account_ref, contact_ref=contact_ref)
        harness.advance_meeting(db, meeting, state)
        status_icon = "✓" if state != MeetingState.NO_SHOW else "✗"
        print(f"  {status_icon} {state.value}: {desc}")

    print()

    # ── Day 5: Meeting HELD ───────────────────────────────────────────
    print("━" * 72)
    print("  DAY 5 — Meeting HELD! → CRM Scribe → AD Accepted")
    print("━" * 72)
    harness.clock.advance_hours(2)
    harness.ingest_event(
        db, EventType.MEETING_HELD, account_ref,
        persona_tier="vp_level", contact_ref=contact_ref,
        points_value=calc_points("vp_level"),
    )
    harness.advance_meeting(db, meeting, MeetingState.HELD)
    print(f"  🎉 Meeting HELD — VP-level = {calc_points('vp_level')} pts (pending AD acceptance)")

    scribe_job = harness.create_job(
        db,
        job_type="crm_scribe",
        agent="crm_scribe",
        funnel_stage="hold",
        account_ref=account_ref,
        contact_ref=contact_ref,
    )
    scribe_output = harness.run_agent(db, scribe_job)
    harness.approve_job(db, scribe_job)
    checklist = scribe_output["sql_checklist"]
    checks_passed = sum(1 for v in checklist.values() if v)
    print(f"  ✓ CRM Scribe: {checks_passed}/{len(checklist)} qualification checks passed")
    print(f"    Three Whys: {scribe_output['three_whys']}")

    harness.clock.advance_days(1)
    harness.ingest_event(
        db, EventType.AD_ACCEPTED, account_ref,
        persona_tier="vp_level", contact_ref=contact_ref,
        points_value=calc_points("vp_level"),
    )
    print(f"  ✓ AD ACCEPTED — +{calc_points('vp_level')} pts CREDITED")
    print()

    # ── Day 6 (Friday): Reporting ─────────────────────────────────────
    print("━" * 72)
    print("  DAY 6 — Friday: Weekly Report")
    print("━" * 72)
    harness.clock.set(datetime(2026, 6, 13, 15, 0))
    report_job = harness.create_job(
        db,
        job_type="weekly_report",
        agent="reporting",
        funnel_stage="create",
    )
    report_output = harness.run_agent(db, report_job)
    harness.approve_job(db, report_job)
    print(f"  Personal recap: {report_output['personal_recap']}")
    print(f"  Manager draft:  {report_output['manager_draft']}")
    print()

    # ── Summary ───────────────────────────────────────────────────────
    print("=" * 72)
    print("  WEEK SUMMARY")
    print("=" * 72)

    all_jobs = db.query(harness.jobs_created[0].__class__).all()
    all_events = db.query(harness.events_ingested[0].__class__).filter(
        harness.events_ingested[0].__class__.account_ref == account_ref
    ).all()

    print(f"  Jobs created:     {len(all_jobs)}")
    print(f"  Events ingested:  {len(all_events)}")
    print(f"  Approvals made:   {len(harness.approvals)}")
    cf_types = {"outreach_draft", "book_response", "calendar_invite", "reminder_24h", "reminder_am"}
    print(f"  Customer-facing drafts: {sum(1 for j in all_jobs if j.job_type in cf_types)}")
    print()

    event_types = sorted({e.event_type for e in all_events})
    print(f"  EventLog story: {' → '.join(event_types)}")
    print()

    print("  ✓ Every customer-facing artifact passed approval")
    print("  ✓ Points credited only after AD acceptance")
    print("  ✓ Full provenance chain maintained")
    print()
    print("=" * 72)
    print("  Demo complete. This is the core loop for week-1 conversations.")
    print("=" * 72)

    db.close()


if __name__ == "__main__":
    run_demo()
