# engine/ — PURE FUNCTIONS, no I/O — fully unit-testable.
# Zero imports of SQLAlchemy, FastAPI, or any adapter/agent/api module.

from app.engine.bottleneck import identify_bottleneck
from app.engine.cascade import avg_pts_per_held, compute_plan, validate_persona_mix
from app.engine.catchup import compute_catchup
from app.engine.clawback import check_provenance, find_duplicates_in_window
from app.engine.earnings import project_earnings
from app.engine.pace import compute_funnel_state
from app.engine.points import compute_compounding_play_ev, compute_points
from app.engine.promotion import compute_scorecard
from app.engine.rates import blend, compute_rates, get_blended_rate
from app.engine.replan import check_triggers

__all__ = [
    "compute_rates",
    "get_blended_rate",
    "blend",
    "compute_plan",
    "avg_pts_per_held",
    "validate_persona_mix",
    "compute_funnel_state",
    "check_triggers",
    "identify_bottleneck",
    "compute_catchup",
    "compute_points",
    "compute_compounding_play_ev",
    "project_earnings",
    "compute_scorecard",
    "check_provenance",
    "find_duplicates_in_window",
]
