"""
Main matching engine orchestrator.

Coordinates a full matching cycle: acquires a distributed lock,
loads pools, runs matching algorithms in priority order (exact → multi → partial),
persists matches to DB, updates transaction statuses, cleans up Redis,
and dispatches notifications.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.matching_engine.matcher import (
    run_exact_matching,
    run_multi_matching,
    run_partial_matching,
)
from app.matching_engine.timeout_handler import check_timeouts
from app.matching_engine.reporter import build_cycle_report
from app.models.match import Match, MatchType, MatchStatus
from app.models.transaction import (
    Transaction,
    TransactionStatus,
    SettlementMethod,
)
from app.models.matching_pool import MatchingPool

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class MatchingEngine:
    """Orchestrates the P2P matching cycle."""

    def __init__(self, pool_mgr=None, session_factory=None):
        """
        Args:
            pool_mgr: PoolManager instance (defaults to module-level singleton).
            session_factory: Async session factory for DB access
                             (defaults to ``app.database.async_session``).
        """
        self._pool_mgr = pool_mgr
        self._session_factory = session_factory

    @property
    def pool_mgr(self):
        if self._pool_mgr is not None:
            return self._pool_mgr
        from app.matching_engine.pool_manager import pool_manager
        return pool_manager

    @property
    def session_factory(self):
        if self._session_factory is not None:
            return self._session_factory
        from app.database import async_session
        return async_session

    # ── Public entry point ───────────────────────────────────────────────

    async def run_cycle(self) -> dict:
        """
        Execute a full matching cycle.

        Acquires a distributed lock to prevent concurrent cycles.
        Returns ``{"skipped": True}`` if the lock is already held.
        """
        lock = await self.pool_mgr.acquire_lock()
        if lock is None:
            logger.warning("Matching cycle skipped — lock held by another process")
            return {"skipped": True}

        try:
            return await self._execute_cycle()
        finally:
            await self.pool_mgr.release_lock(lock)

    # ── Core cycle logic ─────────────────────────────────────────────────

    async def _execute_cycle(self) -> dict:
        """Run the full matching pipeline."""
        started_at = datetime.now(timezone.utc)
        cycle_id = f"MC-{started_at:%Y%m%d-%H%M}"

        # 1. Load pool snapshots
        buy_pool = await self.pool_mgr.get_pool_snapshot("ngn_to_cny")
        sell_pool = await self.pool_mgr.get_pool_snapshot("cny_to_ngn")
        initial_buy_size = len(buy_pool)
        initial_sell_size = len(sell_pool)

        # 2. Exact matching
        exact_matches = run_exact_matching(buy_pool, sell_pool)

        # 3. Remove consumed entries before multi matching
        buy_pool, sell_pool = self._remove_matched_entries(
            buy_pool, sell_pool, exact_matches,
        )

        # 4. Multi-leg matching
        multi_matches = run_multi_matching(buy_pool, sell_pool)

        # 5. Remove consumed entries before partial matching
        buy_pool, sell_pool = self._remove_matched_entries(
            buy_pool, sell_pool, multi_matches,
        )

        # 6. Partial matching
        partial_matches = run_partial_matching(buy_pool, sell_pool)

        # 7. Timeouts
        timed_out = await check_timeouts()

        # Combine all matches (with type markers already set by matchers)
        all_matches = exact_matches + multi_matches + partial_matches

        # 8. Persist to DB + collect deferred operations
        redis_ops: list[dict] = []
        notifications: list[dict] = []

        async with self.session_factory() as session:
            async with session.begin():
                # Persist matches
                for match in exact_matches:
                    ops, notifs = await self._persist_exact_match(
                        session, cycle_id, match,
                    )
                    redis_ops.extend(ops)
                    notifications.extend(notifs)

                for match in multi_matches:
                    ops, notifs = await self._persist_multi_match(
                        session, cycle_id, match,
                    )
                    redis_ops.extend(ops)
                    notifications.extend(notifs)

                for match in partial_matches:
                    ops, notifs = await self._persist_partial_match(
                        session, cycle_id, match,
                    )
                    redis_ops.extend(ops)
                    notifications.extend(notifs)

                # Handle partial remainders
                await self._handle_partial_remainders(session, partial_matches)

                # Handle timeouts
                await self._handle_timeouts(session, timed_out)

        # 9. Execute Redis operations AFTER DB commit
        await self._execute_redis_ops(redis_ops)

        # 10. Dispatch notifications (fire-and-forget)
        self._dispatch_notifications(notifications)

        # 11. Build report
        completed_at = datetime.now(timezone.utc)
        return build_cycle_report(
            cycle_id=cycle_id,
            started_at=started_at,
            completed_at=completed_at,
            matches=all_matches,
            timed_out=timed_out,
            buy_pool_size=initial_buy_size,
            sell_pool_size=initial_sell_size,
        )

    # ── Pool cleanup ─────────────────────────────────────────────────────

    @staticmethod
    def _remove_matched_entries(
        buy_pool: list[dict],
        sell_pool: list[dict],
        matches: list[dict],
    ) -> tuple[list[dict], list[dict]]:
        """
        Remove consumed entries from both pool lists after a matching pass.

        Collects all entry IDs referenced in match results and filters
        them out of the in-memory lists.
        """
        consumed_ids: set[str] = set()
        for m in matches:
            # Exact / partial: pool_a_entry + pool_b_entry
            a_entry = m.get("pool_a_entry")
            if a_entry:
                consumed_ids.add(a_entry.get("id", ""))
            b_entry = m.get("pool_b_entry")
            if b_entry:
                consumed_ids.add(b_entry.get("id", ""))
            # Multi: pool_a_entry (target) + pool_b_entries (legs)
            for leg in m.get("pool_b_entries", []):
                consumed_ids.add(leg.get("id", ""))
        consumed_ids.discard("")

        new_buy = [e for e in buy_pool if e.get("id") not in consumed_ids]
        new_sell = [e for e in sell_pool if e.get("id") not in consumed_ids]
        return new_buy, new_sell

    # ── Match persistence ────────────────────────────────────────────────

    async def _persist_exact_match(
        self,
        session: "AsyncSession",
        cycle_id: str,
        match: dict,
    ) -> tuple[list[dict], list[dict]]:
        """Persist an exact match: 1 Match record, 2 transaction updates."""
        redis_ops: list[dict] = []
        notifications: list[dict] = []

        a_entry = match["pool_a_entry"]
        b_entry = match["pool_b_entry"]
        buy_entry, sell_entry = self._classify_buy_sell(a_entry, b_entry)

        rate = self._derive_rate(buy_entry)

        match_record = Match(
            cycle_id=cycle_id,
            buy_transaction_id=uuid.UUID(buy_entry["transaction_id"]),
            sell_transaction_id=uuid.UUID(sell_entry["transaction_id"]),
            match_type=MatchType.EXACT,
            matched_amount=Decimal(str(match["matched_amount"])),
            matched_rate=rate,
            status=MatchStatus.PENDING_SETTLEMENT,
        )
        session.add(match_record)
        await session.flush()

        # Update buy transaction
        notif = await self._update_transaction(
            session, buy_entry, match_record,
            TransactionStatus.MATCHED, SettlementMethod.MATCHED,
        )
        if notif:
            notifications.append(notif)

        # Update sell transaction
        notif = await self._update_transaction(
            session, sell_entry, match_record,
            TransactionStatus.MATCHED, SettlementMethod.MATCHED,
        )
        if notif:
            notifications.append(notif)

        # Collect Redis removals
        redis_ops.append({"action": "remove", "entry_id": buy_entry["id"], "direction": buy_entry.get("direction", "ngn_to_cny")})
        redis_ops.append({"action": "remove", "entry_id": sell_entry["id"], "direction": sell_entry.get("direction", "cny_to_ngn")})

        return redis_ops, notifications

    async def _persist_multi_match(
        self,
        session: "AsyncSession",
        cycle_id: str,
        match: dict,
    ) -> tuple[list[dict], list[dict]]:
        """
        Persist a multi-leg match.

        Creates N separate Match records (one per leg pairing),
        all with match_type=MULTI and the same cycle_id.
        """
        redis_ops: list[dict] = []
        notifications: list[dict] = []

        target_entry = match["pool_a_entry"]
        legs = match["pool_b_entries"]

        # Classify target
        target_is_buy = self._is_buy_side(target_entry)

        # Update target transaction once (across all legs)
        first_match_record = None

        for leg in legs:
            if target_is_buy:
                buy_entry, sell_entry = target_entry, leg
            else:
                buy_entry, sell_entry = leg, target_entry

            rate = self._derive_rate(buy_entry)
            leg_amount = Decimal(str(leg.get("source_amount") or leg.get("amount", "0")))

            match_record = Match(
                cycle_id=cycle_id,
                buy_transaction_id=uuid.UUID(buy_entry["transaction_id"]),
                sell_transaction_id=uuid.UUID(sell_entry["transaction_id"]),
                match_type=MatchType.MULTI,
                matched_amount=leg_amount,
                matched_rate=rate,
                status=MatchStatus.PENDING_SETTLEMENT,
            )
            session.add(match_record)
            await session.flush()

            if first_match_record is None:
                first_match_record = match_record

            # Update leg transaction
            notif = await self._update_transaction(
                session, leg, match_record,
                TransactionStatus.MATCHED, SettlementMethod.MATCHED,
            )
            if notif:
                notifications.append(notif)

            # Collect Redis removal for leg
            leg_direction = leg.get("direction", "cny_to_ngn" if target_is_buy else "ngn_to_cny")
            redis_ops.append({"action": "remove", "entry_id": leg["id"], "direction": leg_direction})

        # Update target transaction (use first match record)
        if first_match_record:
            notif = await self._update_transaction(
                session, target_entry, first_match_record,
                TransactionStatus.MATCHED, SettlementMethod.MATCHED,
            )
            if notif:
                notifications.append(notif)

        # Collect Redis removal for target
        target_direction = target_entry.get("direction", "ngn_to_cny" if target_is_buy else "cny_to_ngn")
        redis_ops.append({"action": "remove", "entry_id": target_entry["id"], "direction": target_direction})

        return redis_ops, notifications

    async def _persist_partial_match(
        self,
        session: "AsyncSession",
        cycle_id: str,
        match: dict,
    ) -> tuple[list[dict], list[dict]]:
        """Persist a partial match: 1 Match record, 2 transaction updates."""
        redis_ops: list[dict] = []
        notifications: list[dict] = []

        a_entry = match["pool_a_entry"]
        b_entry = match["pool_b_entry"]
        buy_entry, sell_entry = self._classify_buy_sell(a_entry, b_entry)
        remainder = match["remainder"]

        rate = self._derive_rate(buy_entry)

        match_record = Match(
            cycle_id=cycle_id,
            buy_transaction_id=uuid.UUID(buy_entry["transaction_id"]),
            sell_transaction_id=uuid.UUID(sell_entry["transaction_id"]),
            match_type=MatchType.PARTIAL,
            matched_amount=Decimal(str(match["matched_amount"])),
            matched_rate=rate,
            status=MatchStatus.PENDING_SETTLEMENT,
        )
        session.add(match_record)
        await session.flush()

        # Determine statuses based on remainders
        a_remaining = remainder.get("pool_a_remaining", Decimal("0"))
        b_remaining = remainder.get("pool_b_remaining", Decimal("0"))

        # Identify which side has remainder based on direction
        buy_remaining = a_remaining if self._is_buy_side(a_entry) else b_remaining
        sell_remaining = b_remaining if self._is_buy_side(a_entry) else a_remaining

        buy_status = TransactionStatus.PARTIAL_MATCHED
        sell_status = TransactionStatus.PARTIAL_MATCHED

        notif = await self._update_transaction(
            session, buy_entry, match_record,
            buy_status, SettlementMethod.PARTIAL_MATCHED,
        )
        if notif:
            notifications.append(notif)

        notif = await self._update_transaction(
            session, sell_entry, match_record,
            sell_status, SettlementMethod.PARTIAL_MATCHED,
        )
        if notif:
            notifications.append(notif)

        # Redis ops: remove fully consumed, update remainder
        buy_direction = buy_entry.get("direction", "ngn_to_cny")
        sell_direction = sell_entry.get("direction", "cny_to_ngn")

        if buy_remaining == Decimal("0"):
            redis_ops.append({"action": "remove", "entry_id": buy_entry["id"], "direction": buy_direction})
        else:
            redis_ops.append({"action": "update", "entry_id": buy_entry["id"], "new_amount": str(buy_remaining)})

        if sell_remaining == Decimal("0"):
            redis_ops.append({"action": "remove", "entry_id": sell_entry["id"], "direction": sell_direction})
        else:
            redis_ops.append({"action": "update", "entry_id": sell_entry["id"], "new_amount": str(sell_remaining)})

        return redis_ops, notifications

    # ── Transaction helpers ──────────────────────────────────────────────

    async def _update_transaction(
        self,
        session: "AsyncSession",
        entry: dict,
        match_record: Match,
        target_status: TransactionStatus,
        settlement_method: SettlementMethod,
    ) -> dict | None:
        """
        Load a transaction by its ID from a pool entry and update its status.

        Handles the two-step transition: FUNDED → MATCHING → target_status.
        Returns notification data dict, or None if transaction not found.
        """
        txn_id = entry.get("transaction_id")
        if not txn_id:
            return None

        result = await session.execute(
            select(Transaction).where(Transaction.id == uuid.UUID(str(txn_id)))
        )
        txn = result.scalar_one_or_none()
        if txn is None:
            logger.warning("Transaction %s not found for pool entry %s", txn_id, entry.get("id"))
            return None

        # Two-step transition: FUNDED → MATCHING first if needed
        if txn.status == TransactionStatus.FUNDED:
            txn.transition_to(TransactionStatus.MATCHING)

        # Now transition to target status
        if txn.status == TransactionStatus.MATCHING:
            txn.transition_to(target_status)

        txn.match_id = match_record.id
        txn.settlement_method = settlement_method

        # Also deactivate the MatchingPool DB record
        pool_entry_id = entry.get("id")
        if pool_entry_id:
            try:
                pe_id = uuid.UUID(str(pool_entry_id))
                pe_result = await session.execute(
                    select(MatchingPool).where(MatchingPool.id == pe_id)
                )
                pool_entry = pe_result.scalar_one_or_none()
                if pool_entry:
                    pool_entry.is_active = False
            except (ValueError, AttributeError):
                logger.debug("Could not deactivate pool entry %s", pool_entry_id)

        return {
            "transaction_id": str(txn.id),
            "trader_id": str(txn.trader_id),
            "reference": txn.reference,
            "matched_amount": str(match_record.matched_amount),
            "status": target_status.value,
        }

    # ── Partial remainder handling ───────────────────────────────────────

    async def _handle_partial_remainders(
        self,
        session: "AsyncSession",
        partial_matches: list[dict],
    ) -> None:
        """
        Transition remainder transactions back to MATCHING
        so they re-enter the pool for the next cycle.
        """
        for match in partial_matches:
            remainder = match.get("remainder", {})
            a_entry = match["pool_a_entry"]
            b_entry = match["pool_b_entry"]

            a_remaining = remainder.get("pool_a_remaining", Decimal("0"))
            b_remaining = remainder.get("pool_b_remaining", Decimal("0"))

            # If pool_a side has remainder, transition back to MATCHING
            if a_remaining > 0:
                await self._transition_remainder(session, a_entry)

            # If pool_b side has remainder, transition back to MATCHING
            if b_remaining > 0:
                await self._transition_remainder(session, b_entry)

    async def _transition_remainder(
        self,
        session: "AsyncSession",
        entry: dict,
    ) -> None:
        """Transition a partial remainder transaction: PARTIAL_MATCHED → MATCHING."""
        txn_id = entry.get("transaction_id")
        if not txn_id:
            return

        result = await session.execute(
            select(Transaction).where(Transaction.id == uuid.UUID(str(txn_id)))
        )
        txn = result.scalar_one_or_none()
        if txn and txn.status == TransactionStatus.PARTIAL_MATCHED:
            txn.transition_to(TransactionStatus.MATCHING)

    # ── Timeout handling ─────────────────────────────────────────────────

    async def _handle_timeouts(
        self,
        session: "AsyncSession",
        timed_out: list[dict],
    ) -> None:
        """Transition timed-out transactions to EXPIRED and set CIPS settlement."""
        for entry in timed_out:
            txn_id = entry.get("transaction_id")
            if not txn_id:
                continue

            result = await session.execute(
                select(Transaction).where(Transaction.id == uuid.UUID(str(txn_id)))
            )
            txn = result.scalar_one_or_none()
            if txn is None:
                continue

            # Transition to EXPIRED (valid from FUNDED, MATCHING)
            if Transaction.is_valid_transition(txn.status, TransactionStatus.EXPIRED):
                txn.transition_to(TransactionStatus.EXPIRED)
                txn.settlement_method = SettlementMethod.CIPS_SETTLED

            # Deactivate pool entry in DB
            pool_entry_id = entry.get("pool_entry_id")
            if pool_entry_id:
                try:
                    pe_id = uuid.UUID(str(pool_entry_id))
                    pe_result = await session.execute(
                        select(MatchingPool).where(MatchingPool.id == pe_id)
                    )
                    pool_entry = pe_result.scalar_one_or_none()
                    if pool_entry:
                        pool_entry.is_active = False
                except (ValueError, AttributeError):
                    logger.debug("Could not deactivate pool entry %s", pool_entry_id)

    # ── Redis operations (after commit) ──────────────────────────────────

    async def _execute_redis_ops(self, ops: list[dict]) -> None:
        """Execute collected Redis operations after DB commit succeeds."""
        for op in ops:
            try:
                if op["action"] == "remove":
                    await self.pool_mgr.remove_from_pool(
                        op["entry_id"], op["direction"],
                    )
                elif op["action"] == "update":
                    await self.pool_mgr.update_entry_amount(
                        op["entry_id"], op["new_amount"],
                    )
            except Exception:
                logger.exception(
                    "Redis op failed: %s %s", op["action"], op.get("entry_id"),
                )

    # ── Notifications ────────────────────────────────────────────────────

    @staticmethod
    def _dispatch_notifications(notifications: list[dict]) -> None:
        """Fire Celery notification tasks (fire-and-forget)."""
        try:
            from app.tasks.notification_tasks import send_match_notification

            for notif in notifications:
                try:
                    send_match_notification.delay(
                        notif.get("trader_id", ""),
                        {
                            "reference": notif.get("reference", ""),
                            "amount": notif.get("matched_amount", "0"),
                            "status": notif.get("status", ""),
                        },
                    )
                except Exception:
                    logger.exception(
                        "Failed to dispatch notification for txn %s",
                        notif.get("transaction_id"),
                    )
        except Exception:
            logger.exception("Failed to import notification tasks")

    # ── Classification helpers ───────────────────────────────────────────

    @staticmethod
    def _classify_buy_sell(a: dict, b: dict) -> tuple[dict, dict]:
        """
        Determine which entry is buy-side and which is sell-side.

        Checks the ``direction`` field rather than relying on
        pool_a/pool_b position (multi-matcher direction-2 reversal
        puts sell-pool entries into pool_a_entry).
        """
        if MatchingEngine._is_buy_side(a):
            return a, b
        return b, a

    @staticmethod
    def _is_buy_side(entry: dict) -> bool:
        """Return True if entry is a buy-side (ngn_to_cny) transaction."""
        return entry.get("direction") == "ngn_to_cny"

    @staticmethod
    def _derive_rate(buy_entry: dict) -> Decimal:
        """
        Derive exchange rate from pool entry amounts.

        Rate = source_amount / target_amount (same rate locked at txn creation).
        Falls back to Decimal("1") if target_amount is missing or zero.
        """
        source = Decimal(str(buy_entry.get("source_amount") or buy_entry.get("amount") or "0"))
        target = Decimal(str(buy_entry.get("target_amount", "0") or "0"))
        if target == 0:
            return Decimal("1")
        return source / target


# Module-level singleton (uses default pool_mgr and session_factory)
matching_engine = MatchingEngine()
