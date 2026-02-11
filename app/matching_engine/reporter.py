"""
Cycle reporting — builds structured reports for matching cycles.

Generates summary data for admin dashboards, audit logs,
and notification triggers after each matching cycle completes.
"""

from datetime import datetime
from decimal import Decimal


def build_cycle_report(
    cycle_id: str,
    started_at: datetime,
    completed_at: datetime,
    matches: list[dict],
    timed_out: list[dict],
    buy_pool_size: int,
    sell_pool_size: int,
) -> dict:
    """
    Build a structured report for a completed matching cycle.

    Summarizes matches created, volume matched, and remaining pool state.
    """
    total_volume = sum(
        Decimal(m.get("matched_amount", "0")) for m in matches
    )

    return {
        "cycle_id": cycle_id,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_seconds": (completed_at - started_at).total_seconds(),
        "pool_snapshot": {
            "buy_side": buy_pool_size,
            "sell_side": sell_pool_size,
            "total": buy_pool_size + sell_pool_size,
        },
        "results": {
            "exact_matches": len([m for m in matches if m.get("type") == "exact"]),
            "multi_matches": len([m for m in matches if m.get("type") == "multi"]),
            "partial_matches": len([m for m in matches if m.get("type") == "partial"]),
            "total_matches": len(matches),
            "total_volume_matched": str(total_volume),
        },
        "timeouts": {
            "count": len(timed_out),
            "routed_to_cips": len(timed_out),
        },
        "matches": matches,
    }
