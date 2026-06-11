"""Promotion scorecard per COMP_MODEL.md §7.

Tracked continuously:
  - Rolling attainment streak (months ≥130%)
  - Sourced S2 count (target 2–3 by M6)
  - Consecutive months above Sr. SDR quota (40 pts)
  - m6_case_ready: bool + evidence table struct

Pure functions, zero I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

SR_SDR_QUOTA = 40.0
STREAK_THRESHOLD_PCT = 1.30  # ≥130%


@dataclass(frozen=True)
class MonthRecord:
    """One month of performance data for scorecard evaluation."""
    month: int           # M1, M2, ...
    points: float        # credited + pending
    quota: float         # that month's quota
    sourced_s2_count: int = 0


@dataclass(frozen=True)
class EvidenceRow:
    metric: str
    value: str
    target: str
    status: str  # "met" | "not_met" | "on_track"


@dataclass(frozen=True)
class PromotionScorecard:
    rolling_130_streak: int
    sourced_s2_total: int
    consecutive_above_sr_quota: int
    m6_case_ready: bool
    evidence_table: tuple[EvidenceRow, ...]
    months_evaluated: int


def compute_scorecard(
    history: list[MonthRecord],
    *,
    target_month: int = 6,
) -> PromotionScorecard:
    """Build promotion scorecard from monthly history.

    Args:
        history: chronologically ordered monthly records (M2 onward).
        target_month: the month by which promotion is targeted (default M6).

    Returns:
        PromotionScorecard with streak/count metrics and evidence table.
    """
    # Rolling ≥130% streak: consecutive months from the END of history
    rolling_130_streak = 0
    for rec in reversed(history):
        if rec.quota > 0 and (rec.points / rec.quota) >= STREAK_THRESHOLD_PCT:
            rolling_130_streak += 1
        else:
            break

    # Sourced S2 total
    sourced_s2_total = sum(r.sourced_s2_count for r in history)

    # Consecutive months above Sr. SDR quota (40 pts) from the END
    consecutive_above_sr = 0
    for rec in reversed(history):
        if rec.points > SR_SDR_QUOTA:
            consecutive_above_sr += 1
        else:
            break

    # Evidence table
    evidence: list[EvidenceRow] = []

    # 1. Rolling ≥130% streak
    evidence.append(EvidenceRow(
        metric="Rolling ≥130% attainment streak",
        value=f"{rolling_130_streak} months",
        target=f"≥{len(history)} consecutive months",
        status="met" if rolling_130_streak >= len(history) else "not_met",
    ))

    # 2. Sourced S2 count
    s2_target = 2
    evidence.append(EvidenceRow(
        metric="Sourced S2 count",
        value=str(sourced_s2_total),
        target=f"≥{s2_target} by M{target_month}",
        status="met" if sourced_s2_total >= s2_target else "on_track" if sourced_s2_total >= 1 else "not_met",
    ))

    # 3. Consecutive months above Sr. SDR quota
    evidence.append(EvidenceRow(
        metric="Consecutive months >40 pts (Sr. SDR quota)",
        value=f"{consecutive_above_sr} months",
        target="≥2 consecutive months",
        status="met" if consecutive_above_sr >= 2 else "not_met",
    ))

    # 4. Overall framing line
    if consecutive_above_sr >= 2:
        framing = f"Performing above Sr. quota for {consecutive_above_sr} straight months"
    else:
        framing = "Not yet performing above Sr. quota for 2+ months"
    evidence.append(EvidenceRow(
        metric="Framing line",
        value=framing,
        target="Strong narrative for promotion ask",
        status="met" if consecutive_above_sr >= 2 else "not_met",
    ))

    # m6_case_ready: all key criteria met
    m6_case_ready = (
        rolling_130_streak >= len(history)
        and sourced_s2_total >= s2_target
        and consecutive_above_sr >= 2
    )

    return PromotionScorecard(
        rolling_130_streak=rolling_130_streak,
        sourced_s2_total=sourced_s2_total,
        consecutive_above_sr_quota=consecutive_above_sr,
        m6_case_ready=m6_case_ready,
        evidence_table=tuple(evidence),
        months_evaluated=len(history),
    )
