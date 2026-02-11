"""
Timeout handler — routes stale pool entries to CIPS fallback.

Transactions that exceed the pool timeout threshold are removed
from the P2P pool and routed to Afrexim CIPS for direct settlement.
"""

from datetime import datetime, timezone, timedelta

from app.matching_engine.config import POOL_TIMEOUT_HOURS
from app.matching_engine.pool_manager import pool_manager


async def check_timeouts() -> list[dict]:
    """
    Identify and handle timed-out transactions in the matching pool.

    Transactions older than POOL_TIMEOUT_HOURS are:
    1. Removed from the Redis pool
    2. Flagged for CIPS settlement
    3. Returned for the engine to process
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=POOL_TIMEOUT_HOURS)
    timed_out = []

    for pool_getter, direction in [
        (pool_manager.get_buy_pool, "ngn_to_cny"),
        (pool_manager.get_sell_pool, "cny_to_ngn"),
    ]:
        entries = await pool_getter()
        for entry in entries:
            entered_at_str = entry.get("entered_pool_at")
            if not entered_at_str:
                continue
            entered_at = datetime.fromisoformat(entered_at_str)
            if entered_at < cutoff:
                await pool_manager.remove_from_pool(entry["id"], direction)
                timed_out.append({
                    "transaction_id": entry["id"],
                    "direction": direction,
                    "amount": entry.get("amount"),
                    "reason": "pool_timeout",
                    "fallback": "cips",
                })

    # TODO: Dispatch CIPS settlement for each timed-out transaction
    return timed_out
