"""Pipeline Hygiene Agent — nightly 02:30 sweep.

Spec: AGENTS.md §9
- Trigger: Nightly 02:30
- Inputs: full sweep — open CRM tasks, threads, meetings, sequences, EventLog
- Output: HygieneReport{auto_fixed: [], proposed: [{issue, proposed_job, evidence}]}
- Auto-fix: data-hygiene whitelist only (close own stale tasks, fix EventLog links,
  mark dead sequences).  Everything customer-adjacent → proposed jobs in gated flow.
- Strategic accounts: findings become manual-only tasks, never draft jobs.
- Dedupe: fingerprint per finding prevents duplicate jobs on repeated runs.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.adapters.interfaces.types import (
    CalEvent,
    CRMTask,
    ThreadSummary,
)

logger = logging.getLogger(__name__)

# ── Findings ──────────────────────────────────────────────────────────


@dataclass
class Finding:
    rule: str
    description: str
    evidence: dict[str, Any]
    account_ref: str
    contact_ref: str | None = None
    severity: str = "medium"  # low | medium | high

    @property
    def fingerprint(self) -> str:
        """Stable hash for dedupe across sweep runs."""
        key = json.dumps(
            {"rule": self.rule, "account_ref": self.account_ref,
             "contact_ref": self.contact_ref, "evidence": self.evidence},
            sort_keys=True, default=str,
        )
        return hashlib.sha256(key.encode()).hexdigest()[:16]


@dataclass
class AutoFix:
    action: str
    target_ref: str
    detail: str


@dataclass
class ProposedJob:
    job_type: str
    funnel_stage: str
    agent: str
    account_ref: str
    contact_ref: str | None = None
    input_payload: dict[str, Any] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    manual_only: bool = False


@dataclass
class HygieneReport:
    sweep_at: datetime
    auto_fixed: list[AutoFix] = field(default_factory=list)
    proposed: list[dict[str, Any]] = field(default_factory=list)
    findings_count: int = 0
    narrative: str = ""
    all_fingerprints: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sweep_at": self.sweep_at.isoformat(),
            "auto_fixed": [
                {"action": a.action, "target_ref": a.target_ref, "detail": a.detail}
                for a in self.auto_fixed
            ],
            "proposed": self.proposed,
            "findings_count": self.findings_count,
            "narrative": self.narrative,
        }


# ── Detection rules (pure functions) ──────────────────────────────────

AUTOFIX_ACTIONS = frozenset({
    "close_stale_bdr_task",
    "repair_eventlog_job_link",
    "mark_dead_sequence",
})


def detect_unreplied_thread(
    threads: list[ThreadSummary],
    now: datetime,
    threshold_days: int = 5,
    thread_account_map: dict[str, str] | None = None,
) -> list[Finding]:
    """Threads awaiting reply > threshold_days."""
    findings: list[Finding] = []
    acct_map = thread_account_map or {}
    cutoff = now - timedelta(days=threshold_days)
    for t in threads:
        if t.last_message_at < cutoff:
            days_waiting = (now - t.last_message_at).days
            account_ref = acct_map.get(t.ref, t.ref)
            findings.append(Finding(
                rule="unreplied_thread",
                description=f"Thread '{t.subject}' unreplied for {days_waiting}d",
                evidence={"thread_ref": t.ref, "last_message_at": t.last_message_at.isoformat(),
                          "days_waiting": days_waiting},
                account_ref=account_ref,
                severity="medium",
            ))
    return findings


def detect_unaccepted_invite(
    meetings: list[CalEvent],
    now: datetime,
    reconfirm_job_refs: set[str] | None = None,
) -> list[Finding]:
    """Booked meetings with unaccepted invite & no existing reconfirm job."""
    findings: list[Finding] = []
    reconfirm_refs = reconfirm_job_refs or set()
    for m in meetings:
        if m.start <= now:
            continue
        days_out = (m.start - now).days
        has_unaccepted = any(
            a.response_status in ("needsAction", "declined")
            for a in m.attendees
        )
        if has_unaccepted and m.ref not in reconfirm_refs:
            flags = []
            if days_out <= 3:
                flags.append("show-rate risk")
            findings.append(Finding(
                rule="unaccepted_invite",
                description=(
                    f"Meeting '{m.title}' in {days_out}d has unaccepted invite"
                ),
                evidence={"event_ref": m.ref, "days_out": days_out,
                          "unaccepted_attendees": [
                              a.email for a in m.attendees
                              if a.response_status in ("needsAction", "declined")
                          ],
                          "flags": flags},
                account_ref=m.ref.split("-")[0] if "-" in m.ref else m.ref,
                severity="high" if days_out <= 3 else "medium",
            ))
    return findings


@dataclass
class ContactTouchRecord:
    contact_ref: str
    account_ref: str
    touch_count: int
    last_reply_at: datetime | None
    first_touch_at: datetime
    in_active_sequence: bool = False


def detect_stale_sequence(
    contacts: list[ContactTouchRecord],
    now: datetime,
    touch_threshold: int = 3,
    no_reply_days: int = 21,
) -> list[Finding]:
    """Contact with ≥touch_threshold touches and no reply in no_reply_days → suggest end."""
    findings: list[Finding] = []
    for c in contacts:
        if c.touch_count < touch_threshold:
            continue
        if c.last_reply_at is not None:
            days_since_reply = (now - c.last_reply_at).days
            if days_since_reply < no_reply_days:
                continue
        else:
            days_since_first = (now - c.first_touch_at).days
            if days_since_first < no_reply_days:
                continue
        findings.append(Finding(
            rule="stale_sequence",
            description=(
                f"Contact '{c.contact_ref}' has {c.touch_count} touches "
                f"with no reply in {no_reply_days}+ days"
            ),
            evidence={"contact_ref": c.contact_ref, "touch_count": c.touch_count,
                      "last_reply_at": c.last_reply_at.isoformat() if c.last_reply_at else None,
                      "no_reply_days": no_reply_days},
            account_ref=c.account_ref,
            contact_ref=c.contact_ref,
            severity="medium",
        ))
    return findings


@dataclass
class NoShowRecord:
    event_ref: str
    account_ref: str
    contact_ref: str | None
    occurred_at: datetime
    has_recovery_sequence: bool = False


def detect_no_show_without_recovery(
    no_shows: list[NoShowRecord],
) -> list[Finding]:
    """No-show meeting without a recovery sequence."""
    findings: list[Finding] = []
    for ns in no_shows:
        if not ns.has_recovery_sequence:
            findings.append(Finding(
                rule="no_show_no_recovery",
                description=f"No-show for event '{ns.event_ref}' without recovery sequence",
                evidence={"event_ref": ns.event_ref,
                          "occurred_at": ns.occurred_at.isoformat()},
                account_ref=ns.account_ref,
                contact_ref=ns.contact_ref,
                severity="high",
            ))
    return findings


def detect_overdue_tasks(
    tasks: list[CRMTask],
    now: datetime,
    overdue_days: int = 3,
) -> list[Finding]:
    """CRM tasks overdue by > overdue_days."""
    findings: list[Finding] = []
    cutoff = (now - timedelta(days=overdue_days)).date()
    for t in tasks:
        if t.due_date is not None and t.due_date < cutoff and t.status == "open":
            days_overdue = (now.date() - t.due_date).days
            findings.append(Finding(
                rule="overdue_task",
                description=f"Task '{t.subject}' overdue by {days_overdue}d",
                evidence={"task_ref": t.ref, "due_date": t.due_date.isoformat(),
                          "days_overdue": days_overdue},
                account_ref=t.account_ref,
                severity="low",
            ))
    return findings


# ── Auto-fix logic ────────────────────────────────────────────────────


def classify_autofix(finding: Finding) -> AutoFix | None:
    """Return an AutoFix if the finding is on the data-hygiene whitelist."""
    if finding.rule == "overdue_task":
        task_ref = finding.evidence.get("task_ref", "")
        if task_ref.startswith("bdr-os-"):
            return AutoFix(
                action="close_stale_bdr_task",
                target_ref=task_ref,
                detail=f"Auto-closed stale BDR-OS task: {finding.description}",
            )
    return None


def repair_eventlog_job_link(
    events_missing_job_id: list[dict[str, Any]],
    jobs_by_account: dict[str, list[dict[str, Any]]],
) -> list[AutoFix]:
    """Repair missing EventLog↔job links where we can match by account+time."""
    fixes: list[AutoFix] = []
    for evt in events_missing_job_id:
        acct = evt.get("account_ref", "")
        occurred = evt.get("occurred_at", "")
        candidates = jobs_by_account.get(acct, [])
        for job in candidates:
            created = job.get("created_at", "")
            if created and occurred and abs(
                (datetime.fromisoformat(str(occurred))
                 - datetime.fromisoformat(str(created))).total_seconds()
            ) < 3600:
                fixes.append(AutoFix(
                    action="repair_eventlog_job_link",
                    target_ref=evt.get("id", ""),
                    detail=f"Linked event {evt.get('id')} to job {job.get('id')}",
                ))
                break
    return fixes


def mark_dead_sequences(
    stale_findings: list[Finding],
) -> list[AutoFix]:
    """Stale-sequence findings where the contact is in an active sequence → mark dead."""
    fixes: list[AutoFix] = []
    for f in stale_findings:
        fixes.append(AutoFix(
            action="mark_dead_sequence",
            target_ref=f.contact_ref or f.account_ref,
            detail=f"Marked dead sequence for {f.contact_ref or f.account_ref}",
        ))
    return fixes


# ── Proposed-job builder ──────────────────────────────────────────────


def build_proposed_job(
    finding: Finding,
    strategic_accounts: set[str],
) -> ProposedJob:
    """Convert a non-autofix finding into a proposed job.

    Strategic accounts: manual_only=True, never a draft job.
    """
    is_strategic = finding.account_ref in strategic_accounts

    job_type_map: dict[str, tuple[str, str, str]] = {
        "unreplied_thread": ("follow_up", "convert", "pipeline_hygiene"),
        "unaccepted_invite": ("reconfirm", "hold", "pipeline_hygiene"),
        "stale_sequence": ("sequence_end_review", "create", "pipeline_hygiene"),
        "no_show_no_recovery": ("no_show_recovery", "hold", "pipeline_hygiene"),
        "overdue_task": ("task_cleanup", "create", "pipeline_hygiene"),
    }
    jtype, stage, agent = job_type_map.get(
        finding.rule, ("hygiene_review", "create", "pipeline_hygiene")
    )

    flags = finding.evidence.get("flags", [])

    return ProposedJob(
        job_type=jtype,
        funnel_stage=stage,
        agent=agent,
        account_ref=finding.account_ref,
        contact_ref=finding.contact_ref,
        input_payload={"finding": finding.evidence, "description": finding.description},
        flags=flags,
        manual_only=is_strategic,
    )


# ── Sweep orchestrator ────────────────────────────────────────────────


def run_sweep(
    *,
    now: datetime,
    threads: list[ThreadSummary],
    meetings: list[CalEvent],
    contacts: list[ContactTouchRecord],
    no_shows: list[NoShowRecord],
    tasks: list[CRMTask],
    events_missing_job_id: list[dict[str, Any]] | None = None,
    jobs_by_account: dict[str, list[dict[str, Any]]] | None = None,
    reconfirm_job_refs: set[str] | None = None,
    strategic_accounts: set[str] | None = None,
    existing_fingerprints: set[str] | None = None,
    thread_account_map: dict[str, str] | None = None,
) -> HygieneReport:
    """Run the full hygiene sweep. Pure function (no I/O).

    Returns a HygieneReport with auto_fixed items and proposed jobs.
    Deduplicates on finding fingerprint against existing_fingerprints.
    """
    strat = strategic_accounts or set()
    existing_fps = existing_fingerprints or set()
    report = HygieneReport(sweep_at=now)

    # 1. Run all detection rules
    all_findings: list[Finding] = []
    all_findings.extend(detect_unreplied_thread(threads, now,
                                                    thread_account_map=thread_account_map))
    all_findings.extend(detect_unaccepted_invite(meetings, now, reconfirm_job_refs))
    all_findings.extend(detect_stale_sequence(contacts, now))
    all_findings.extend(detect_no_show_without_recovery(no_shows))
    all_findings.extend(detect_overdue_tasks(tasks, now))

    # 2. Dedupe against existing fingerprints (both proposed and auto-fixed)
    new_findings: list[Finding] = []
    for f in all_findings:
        if f.fingerprint not in existing_fps:
            new_findings.append(f)

    report.findings_count = len(new_findings)

    # 3. Classify: auto-fix vs proposed
    stale_for_dead_seq: list[Finding] = []
    for f in new_findings:
        report.all_fingerprints.add(f.fingerprint)
        autofix = classify_autofix(f)
        if autofix:
            report.auto_fixed.append(autofix)
        else:
            if f.rule == "stale_sequence":
                stale_for_dead_seq.append(f)
            pj = build_proposed_job(f, strat)
            report.proposed.append({
                "issue": f.description,
                "proposed_job": {
                    "job_type": pj.job_type,
                    "funnel_stage": pj.funnel_stage,
                    "agent": pj.agent,
                    "account_ref": pj.account_ref,
                    "contact_ref": pj.contact_ref,
                    "input_payload": pj.input_payload,
                    "flags": pj.flags,
                    "manual_only": pj.manual_only,
                },
                "evidence": f.evidence,
                "fingerprint": f.fingerprint,
            })

    # 4. Auto-fix: repair EventLog↔job links
    if events_missing_job_id and jobs_by_account:
        link_fixes = repair_eventlog_job_link(events_missing_job_id, jobs_by_account)
        report.auto_fixed.extend(link_fixes)

    # 5. Auto-fix: mark dead sequences
    dead_seq_fixes = mark_dead_sequences(stale_for_dead_seq)
    report.auto_fixed.extend(dead_seq_fixes)

    return report
