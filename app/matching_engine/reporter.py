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
        Decimal(str(m.get("matched_amount", "0"))) for m in matches
    )

    exact_count = len([m for m in matches if m.get("type") == "exact"])
    multi_count = len([m for m in matches if m.get("type") == "multi"])
    partial_count = len([m for m in matches if m.get("type") == "partial"])
    total_pool = buy_pool_size + sell_pool_size

    duration = completed_at - started_at
    duration_ms = int(duration.total_seconds() * 1000)

    # Count unique entry IDs involved in matches
    matched_entry_ids: set[str] = set()
    for m in matches:
        a_entry = m.get("pool_a_entry") or m.get("buy")
        b_entry = m.get("pool_b_entry") or m.get("sell")
        if a_entry:
            matched_entry_ids.add(a_entry.get("id", ""))
        if b_entry:
            matched_entry_ids.add(b_entry.get("id", ""))
        for leg in m.get("pool_b_entries", []):
            matched_entry_ids.add(leg.get("id", ""))
    matched_entry_ids.discard("")

    if total_pool > 0:
        efficiency = str(Decimal(str(len(matched_entry_ids))) / Decimal(str(total_pool)) * 100)
    else:
        efficiency = "0"

    return {
        "cycle_id": cycle_id,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_seconds": duration.total_seconds(),
        # New top-level fields
        "pool_size_start": {
            "buy": buy_pool_size,
            "sell": sell_pool_size,
            "total": total_pool,
        },
        "exact_matches": exact_count,
        "multi_matches": multi_count,
        "partial_matches": partial_count,
        "timeouts": len(timed_out),
        "total_matched_usd": str(total_volume),
        "matching_efficiency": efficiency,
        "cycle_duration_ms": duration_ms,
        # Backward-compatible nested results (matching_tasks.py uses results.total_matches)
        "pool_snapshot": {
            "buy_side": buy_pool_size,
            "sell_side": sell_pool_size,
            "total": total_pool,
        },
        "results": {
            "exact_matches": exact_count,
            "multi_matches": multi_count,
            "partial_matches": partial_count,
            "total_matches": len(matches),
            "total_volume_matched": str(total_volume),
        },
        "timeout_details": {
            "count": len(timed_out),
            "routed_to_cips": len(timed_out),
        },
        "matches": matches,
    }
