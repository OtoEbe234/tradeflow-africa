"""
Main matching engine orchestrator.

Coordinates a full matching cycle: loads pools, runs matching
algorithms in priority order, records matches, and updates statuses.
"""

import uuid
from datetime import datetime, timezone

from app.matching_engine.pool_manager import pool_manager
from app.matching_engine.matcher import find_exact_matches, find_multi_matches, find_partial_matches
from app.matching_engine.timeout_handler import check_timeouts
from app.matching_engine.reporter import build_cycle_report


class MatchingEngine:
    """Orchestrates the P2P matching cycle."""

    async def run_cycle(self) -> dict:
        """
        Execute a full matching cycle.

        Steps:
        1. Check for timed-out transactions -> route to CIPS
        2. Load buy and sell pools from Redis
        3. Run exact matching (highest priority)
        4. Run multi-leg matching
        5. Run partial matching
        6. Record all matches in the database
        7. Update transaction statuses
        8. Notify affected traders
        9. Return cycle report
        """
        cycle_id = f"cycle_{uuid.uuid4().hex[:12]}"
        started_at = datetime.now(timezone.utc)

        # Step 1: Handle timeouts
        timed_out = await check_timeouts()

        # Step 2: Load pools
        buy_pool = await pool_manager.get_buy_pool()
        sell_pool = await pool_manager.get_sell_pool()

        all_matches = []

        # Step 3: Exact matching
        exact = find_exact_matches(buy_pool, sell_pool)
        all_matches.extend(exact)

        # Step 4: Multi-leg matching (on remaining pool entries)
        # TODO: Remove matched entries from pools before multi-leg
        multi = find_multi_matches(buy_pool, sell_pool)
        all_matches.extend(multi)

        # Step 5: Partial matching (on remaining pool entries)
        partial = find_partial_matches(buy_pool, sell_pool)
        all_matches.extend(partial)

        # Steps 6-8: Record, update, notify
        # TODO: Persist matches to database
        # TODO: Update transaction statuses to MATCHED
        # TODO: Remove matched entries from Redis pools
        # TODO: Send notifications to traders

        completed_at = datetime.now(timezone.utc)

        return build_cycle_report(
            cycle_id=cycle_id,
            started_at=started_at,
            completed_at=completed_at,
            matches=all_matches,
            timed_out=timed_out,
            buy_pool_size=len(buy_pool),
            sell_pool_size=len(sell_pool),
        )


matching_engine = MatchingEngine()
