"""Clawback provenance check per COMP_MODEL.md §3.

Provenance completeness for a meeting:
  - outbound evidence present (outbound_touches list non-empty)
  - Named Target validated OR dormancy ≥120 days
  - No duplicate within eligibility window

Returns credit_risk: none | warn | block_booking.

Pure functions, zero I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

DORMANCY_THRESHOLD_DAYS = 120
DEFAULT_ELIGIBILITY_WINDOW_DAYS = 90


class CreditRisk(StrEnum):
    NONE = "none"
    WARN = "warn"
    BLOCK_BOOKING = "block_booking"


@dataclass(frozen=True)
class MeetingProvenance:
    """Provenance record for a single meeting."""
    outbound_touches: tuple[str, ...]  # event IDs of outbound touches
    first_touch_channel: str | None
    named_target_validated: bool
    dormancy_days: int | None  # days since last activity, None if unknown
    contact_ref: str | None = None
    account_ref: str = ""


@dataclass(frozen=True)
class DuplicateCheck:
    """Info about prior meetings with same contact in eligibility window."""
    has_duplicate: bool
    prior_meeting_count: int = 0


@dataclass(frozen=True)
class CreditRiskResult:
    risk: CreditRisk
    reasons: tuple[str, ...]


def check_provenance(
    provenance: MeetingProvenance,
    duplicate_check: DuplicateCheck,
) -> CreditRiskResult:
    """Evaluate credit risk for a meeting.

    Rules:
      1. outbound_touches must be non-empty (evidence of outbound activity)
      2. named_target_validated OR dormancy ≥ 120 days
      3. no duplicate in eligibility window

    Risk levels:
      - block_booking: missing outbound provenance (hard block)
      - warn: duplicate in window or other soft issue
      - none: all checks pass
    """
    reasons: list[str] = []
    risk = CreditRisk.NONE

    # Check 1: outbound evidence
    if not provenance.outbound_touches:
        reasons.append("Missing outbound provenance — no outbound touch evidence")
        risk = CreditRisk.BLOCK_BOOKING

    # Check 2: Named Target or dormancy
    target_ok = provenance.named_target_validated
    dormancy_ok = (
        provenance.dormancy_days is not None
        and provenance.dormancy_days >= DORMANCY_THRESHOLD_DAYS
    )
    if not target_ok and not dormancy_ok:
        reasons.append(
            "Neither Named Target validated nor dormancy ≥120 days"
        )
        if risk != CreditRisk.BLOCK_BOOKING:
            risk = CreditRisk.WARN

    # Check 3: duplicate in window
    if duplicate_check.has_duplicate:
        reasons.append(
            f"Duplicate meeting within eligibility window "
            f"({duplicate_check.prior_meeting_count} prior)"
        )
        if risk == CreditRisk.NONE:
            risk = CreditRisk.WARN

    return CreditRiskResult(
        risk=risk,
        reasons=tuple(reasons),
    )


def find_duplicates_in_window(
    contact_ref: str,
    meeting_date: datetime,
    prior_meetings: list[datetime],
    *,
    window_days: int = DEFAULT_ELIGIBILITY_WINDOW_DAYS,
) -> DuplicateCheck:
    """Check for duplicate meetings with the same contact in the eligibility window."""
    window_start = meeting_date - timedelta(days=window_days)
    duplicates = [
        m for m in prior_meetings
        if window_start <= m < meeting_date
    ]
    return DuplicateCheck(
        has_duplicate=len(duplicates) > 0,
        prior_meeting_count=len(duplicates),
    )
