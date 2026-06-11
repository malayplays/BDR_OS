"""Point valuation from comp_plan.yaml — meeting points by persona tier,
opp points by type/stage, SPIFF detection, three buckets, clawback reversal.

Pure functions, zero I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.engine.types import (
    Event,
    EventType,
    PersonaTier,
)

# ── Comp plan config (from comp_plan.yaml / COMP_MODEL.md §2) ─────────

MEETING_POINTS: dict[str, float] = {
    PersonaTier.GLOBAL_C_SUITE: 8.0,
    PersonaTier.VP_LEVEL: 5.0,
    PersonaTier.DIRECTOR: 3.0,
    PersonaTier.MANAGER: 1.0,
    PersonaTier.IC: 0.5,
}

OPP_POINTS: dict[str, dict[str, float]] = {
    "sourced_net_new": {"S1": 5.0, "S2": 10.0},
    "sourced_engaged": {"S1": 3.0, "S2": 6.0},
    "influenced": {"S1": 3.0, "S2": 6.0},
    "inbound_sr_only": {"S2": 2.0},
}

SPIFF_SOURCED_S2_CASH: float = 1000.0


# ── Data structures ────────────────────────────────────────────────────


@dataclass(frozen=True)
class LedgerEntry:
    """One line in the points ledger — additive or negative (clawback)."""
    event_type: str
    points: float
    persona_tier: str | None = None
    account_ref: str = ""
    contact_ref: str | None = None
    opp_type: str | None = None
    stage: str | None = None
    reverses_event_id: str | None = None


@dataclass(frozen=True)
class PointsBuckets:
    credited: float = 0.0
    pending: float = 0.0
    projected: float = 0.0


@dataclass(frozen=True)
class PointsSummary:
    buckets: PointsBuckets
    ledger: tuple[LedgerEntry, ...]
    spiff_cash: float = 0.0


# ── Point valuation helpers ────────────────────────────────────────────


def meeting_points_for_tier(persona_tier: str) -> float:
    return MEETING_POINTS.get(persona_tier, 0.0)


def opp_points_for_type_stage(opp_type: str, stage: str) -> float:
    type_map = OPP_POINTS.get(opp_type, {})
    return type_map.get(stage, 0.0)


def is_inbound_locked(*, is_promoted: bool) -> bool:
    return not is_promoted


def is_spiff_eligible(opp_type: str, stage: str) -> bool:
    return opp_type == "sourced_net_new" and stage == "S2"


# ── Core ledger computation ────────────────────────────────────────────


def compute_points(
    events: Sequence[Event],
    *,
    ad_accept_rate: float = 0.90,
    show_rate: float = 0.70,
    is_promoted: bool = False,
) -> PointsSummary:
    """Build a points ledger from an event stream.

    Three buckets per COMP_MODEL.md §3:
      - credited: AD-accepted meetings + opp stage events
      - pending:  occurred (meeting_held) awaiting AD acceptance
      - projected: booked × show_rate × accept_rate

    Clawback handling: credit_clawed_back → negative ledger entry
    (never deletion). The ledger preserves full history.
    """
    ledger: list[LedgerEntry] = []
    credited = 0.0
    pending = 0.0
    projected = 0.0
    spiff_cash = 0.0

    # Track meeting lifecycle per (account, contact)
    held_keys: set[tuple[str, str | None]] = set()
    accepted_keys: set[tuple[str, str | None]] = set()
    no_show_keys: set[tuple[str, str | None]] = set()
    clawed_back_keys: set[tuple[str, str | None]] = set()

    # First pass: identify lifecycle states
    for e in events:
        key = (e.account_ref, e.contact_ref)
        if e.event_type == EventType.MEETING_HELD:
            held_keys.add(key)
        elif e.event_type == EventType.AD_ACCEPTED:
            accepted_keys.add(key)
        elif e.event_type == EventType.MEETING_NO_SHOW:
            no_show_keys.add(key)
        elif e.event_type == EventType.CREDIT_CLAWED_BACK:
            clawed_back_keys.add(key)

    # Second pass: build ledger entries
    for e in events:
        key = (e.account_ref, e.contact_ref)

        if e.event_type == EventType.AD_ACCEPTED:
            pts = e.points_value if e.points_value is not None else meeting_points_for_tier(e.persona_tier or "")
            ledger.append(LedgerEntry(
                event_type=e.event_type,
                points=pts,
                persona_tier=e.persona_tier,
                account_ref=e.account_ref,
                contact_ref=e.contact_ref,
            ))
            credited += pts

        elif e.event_type == EventType.MEETING_HELD:
            if key not in accepted_keys and key not in clawed_back_keys:
                pts = meeting_points_for_tier(e.persona_tier or "")
                ledger.append(LedgerEntry(
                    event_type=e.event_type,
                    points=pts,
                    persona_tier=e.persona_tier,
                    account_ref=e.account_ref,
                    contact_ref=e.contact_ref,
                ))
                pending += pts

        elif e.event_type == EventType.MEETING_BOOKED:
            if key not in held_keys and key not in no_show_keys:
                pts = meeting_points_for_tier(e.persona_tier or "")
                proj_pts = pts * show_rate * ad_accept_rate
                ledger.append(LedgerEntry(
                    event_type=e.event_type,
                    points=proj_pts,
                    persona_tier=e.persona_tier,
                    account_ref=e.account_ref,
                    contact_ref=e.contact_ref,
                ))
                projected += proj_pts

        elif e.event_type == EventType.CREDIT_CLAWED_BACK:
            pts = e.points_value if e.points_value is not None else meeting_points_for_tier(e.persona_tier or "")
            ledger.append(LedgerEntry(
                event_type=e.event_type,
                points=-pts,
                persona_tier=e.persona_tier,
                account_ref=e.account_ref,
                contact_ref=e.contact_ref,
                reverses_event_id=e.reverses_event_id,
            ))
            credited -= pts

        elif e.event_type in (EventType.S1_REACHED, EventType.S2_REACHED):
            opp_type = (e.payload or {}).get("opp_type", "")
            stage = "S1" if e.event_type == EventType.S1_REACHED else "S2"

            if opp_type == "inbound_sr_only" and is_inbound_locked(is_promoted=is_promoted):
                continue

            pts = e.points_value if e.points_value is not None else opp_points_for_type_stage(opp_type, stage)
            if pts > 0:
                ledger.append(LedgerEntry(
                    event_type=e.event_type,
                    points=pts,
                    opp_type=opp_type,
                    stage=stage,
                    account_ref=e.account_ref,
                    contact_ref=e.contact_ref,
                ))
                credited += pts

            if is_spiff_eligible(opp_type, stage):
                spiff_cash += SPIFF_SOURCED_S2_CASH

    return PointsSummary(
        buckets=PointsBuckets(
            credited=credited,
            pending=pending,
            projected=projected,
        ),
        ledger=tuple(ledger),
        spiff_cash=spiff_cash,
    )


def compute_compounding_play_ev(
    persona_tier: str,
    *,
    opp_type: str = "sourced_net_new",
    p_held: float = 1.0,
    p_ad_accept: float = 1.0,
) -> float:
    """EV of a compounding play: meeting + S1 + S2 path.

    COMP_MODEL.md §5 example: net-new VP meeting → S1 → S2 = 20 pts.
    VP meeting (5) + sourced_net_new S1 (5) + sourced_net_new S2 (10) = 20
    """
    meeting_pts = meeting_points_for_tier(persona_tier) * p_held * p_ad_accept
    s1_pts = opp_points_for_type_stage(opp_type, "S1")
    s2_pts = opp_points_for_type_stage(opp_type, "S2")
    return meeting_pts + s1_pts + s2_pts
