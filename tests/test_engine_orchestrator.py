"""Tests for the matching engine orchestrator (engine.py).

Covers locking, cycle ID format, pool cleanup between passes,
match persistence, timeout handling, notifications, and a full
20-transaction integration cycle.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from app.matching_engine.engine import MatchingEngine
from app.models.match import Match, MatchType, MatchStatus
from app.models.transaction import (
    Transaction,
    TransactionDirection,
    TransactionStatus,
    SettlementMethod,
)
from app.models.matching_pool import MatchingPool


# ── Helpers ──────────────────────────────────────────────────────────────


def _pe(
    id: str,
    txn_id: str,
    direction: str,
    amount,
    score: float = 50.0,
    target_amount=None,
) -> dict:
    """Build a pool entry dict matching PoolManager.get_pool_snapshot output."""
    entry = {
        "id": id,
        "transaction_id": txn_id,
        "direction": direction,
        "source_amount": str(amount),
        "_score": score,
    }
    if target_amount is not None:
        entry["target_amount"] = str(target_amount)
    return entry


def _funded_txn(trader_id, direction, amount, txn_id=None) -> Transaction:
    """Create a FUNDED Transaction ORM object for test use."""
    tid = txn_id or uuid.uuid4()
    txn = Transaction(
        id=tid,
        trader_id=trader_id,
        direction=TransactionDirection(direction),
        source_amount=Decimal(str(amount)),
        target_amount=Decimal(str(amount)),
        status=TransactionStatus.INITIATED,
    )
    # Transition to FUNDED
    txn.transition_to(TransactionStatus.FUNDED)
    return txn


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def mock_pool_mgr():
    """Mock PoolManager with common methods."""
    mgr = AsyncMock()
    mgr.acquire_lock = AsyncMock(return_value=MagicMock())  # returns a mock lock
    mgr.release_lock = AsyncMock()
    mgr.get_pool_snapshot = AsyncMock(return_value=[])
    mgr.remove_from_pool = AsyncMock()
    mgr.update_entry_amount = AsyncMock()
    return mgr


@pytest.fixture
def mock_session():
    """Mock async DB session supporting session.begin() context manager."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    # Make session.begin() return an async context manager
    begin_cm = AsyncMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin_cm)

    return session


@pytest.fixture
def mock_session_factory(mock_session):
    """Session factory that returns a context manager yielding mock_session."""

    class _SessionCM:
        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, *args):
            pass

    def factory():
        return _SessionCM()

    return factory


def _make_execute_side_effects(txn_map, pool_entry_map=None):
    """
    Build a side_effect function for session.execute that returns
    Transaction or MatchingPool objects based on the query's WHERE clause.

    txn_map: dict mapping str(uuid) -> Transaction object
    pool_entry_map: dict mapping str(uuid) -> MatchingPool object (optional)
    """
    pool_entry_map = pool_entry_map or {}
    all_objects = {**txn_map, **pool_entry_map}

    async def _execute(stmt):
        result = MagicMock()
        # Try to extract the bound value from the WHERE clause
        try:
            val = stmt.whereclause.right.value
            key = str(val)
            if key in all_objects:
                result.scalar_one_or_none = MagicMock(return_value=all_objects[key])
                return result
        except (AttributeError, TypeError):
            pass

        # Fallback: search for any UUID from the map in the statement repr
        try:
            stmt_repr = repr(stmt)
            for key, obj in all_objects.items():
                if key in stmt_repr:
                    result.scalar_one_or_none = MagicMock(return_value=obj)
                    return result
        except Exception:
            pass

        result.scalar_one_or_none = MagicMock(return_value=None)
        return result

    return _execute


# ===========================================================================
# TEST: Cycle Locking
# ===========================================================================


class TestCycleLocking:
    """Distributed lock acquisition and release."""

    @pytest.mark.asyncio
    async def test_skip_when_locked(self, mock_pool_mgr, mock_session_factory):
        """run_cycle returns skipped=True when lock cannot be acquired."""
        mock_pool_mgr.acquire_lock = AsyncMock(return_value=None)
        engine = MatchingEngine(pool_mgr=mock_pool_mgr, session_factory=mock_session_factory)

        result = await engine.run_cycle()
        assert result == {"skipped": True}
        mock_pool_mgr.release_lock.assert_not_called()

    @pytest.mark.asyncio
    async def test_lock_released_on_success(self, mock_pool_mgr, mock_session_factory):
        """Lock is released after a successful cycle."""
        mock_lock = MagicMock()
        mock_pool_mgr.acquire_lock = AsyncMock(return_value=mock_lock)
        engine = MatchingEngine(pool_mgr=mock_pool_mgr, session_factory=mock_session_factory)

        with patch("app.matching_engine.engine.check_timeouts", new_callable=AsyncMock, return_value=[]):
            result = await engine.run_cycle()

        assert "cycle_id" in result
        mock_pool_mgr.release_lock.assert_called_once_with(mock_lock)

    @pytest.mark.asyncio
    async def test_lock_released_on_error(self, mock_pool_mgr, mock_session_factory):
        """Lock is released even when the cycle raises an exception."""
        mock_lock = MagicMock()
        mock_pool_mgr.acquire_lock = AsyncMock(return_value=mock_lock)
        mock_pool_mgr.get_pool_snapshot = AsyncMock(side_effect=RuntimeError("boom"))
        engine = MatchingEngine(pool_mgr=mock_pool_mgr, session_factory=mock_session_factory)

        with pytest.raises(RuntimeError, match="boom"):
            await engine.run_cycle()

        mock_pool_mgr.release_lock.assert_called_once_with(mock_lock)


# ===========================================================================
# TEST: Cycle ID Format
# ===========================================================================


class TestCycleIdFormat:
    """Cycle ID follows MC-YYYYMMDD-HHMM format."""

    @pytest.mark.asyncio
    async def test_cycle_id_format(self, mock_pool_mgr, mock_session_factory):
        engine = MatchingEngine(pool_mgr=mock_pool_mgr, session_factory=mock_session_factory)

        with patch("app.matching_engine.engine.check_timeouts", new_callable=AsyncMock, return_value=[]):
            result = await engine.run_cycle()

        cycle_id = result["cycle_id"]
        assert cycle_id.startswith("MC-")
        # MC-YYYYMMDD-HHMM = 16 chars
        assert len(cycle_id) == 16
        # Validate it can be parsed
        date_part = cycle_id[3:]  # YYYYMMDD-HHMM
        datetime.strptime(date_part, "%Y%m%d-%H%M")


# ===========================================================================
# TEST: Remove Matched Entries
# ===========================================================================


class TestRemoveMatchedEntries:
    """_remove_matched_entries filters consumed IDs from pool lists."""

    def test_exact_removes_both(self):
        buy = [_pe("b1", "t1", "ngn_to_cny", 1000), _pe("b2", "t2", "ngn_to_cny", 2000)]
        sell = [_pe("s1", "t3", "cny_to_ngn", 1000), _pe("s2", "t4", "cny_to_ngn", 2000)]
        matches = [
            {"type": "exact", "pool_a_entry": buy[0], "pool_b_entry": sell[0], "matched_amount": Decimal("1000")},
        ]

        new_buy, new_sell = MatchingEngine._remove_matched_entries(buy, sell, matches)
        assert len(new_buy) == 1
        assert new_buy[0]["id"] == "b2"
        assert len(new_sell) == 1
        assert new_sell[0]["id"] == "s2"

    def test_multi_removes_target_and_legs(self):
        buy = [_pe("b1", "t1", "ngn_to_cny", 5000)]
        sell = [
            _pe("s1", "t2", "cny_to_ngn", 2000),
            _pe("s2", "t3", "cny_to_ngn", 1500),
            _pe("s3", "t4", "cny_to_ngn", 1500),
            _pe("s4", "t5", "cny_to_ngn", 500),
        ]
        matches = [{
            "type": "multi",
            "pool_a_entry": buy[0],
            "pool_b_entries": [sell[0], sell[1], sell[2]],
            "matched_amount": Decimal("5000"),
        }]

        new_buy, new_sell = MatchingEngine._remove_matched_entries(buy, sell, matches)
        assert len(new_buy) == 0
        assert len(new_sell) == 1
        assert new_sell[0]["id"] == "s4"

    def test_unmatched_preserved(self):
        buy = [_pe("b1", "t1", "ngn_to_cny", 1000)]
        sell = [_pe("s1", "t2", "cny_to_ngn", 1000)]

        new_buy, new_sell = MatchingEngine._remove_matched_entries(buy, sell, [])
        assert len(new_buy) == 1
        assert len(new_sell) == 1


# ===========================================================================
# TEST: Pool Cleanup Between Passes
# ===========================================================================


class TestPoolCleanupBetweenPasses:
    """Matched entries from one pass are excluded from the next."""

    @pytest.mark.asyncio
    async def test_exact_matched_excluded_from_multi(self, mock_pool_mgr, mock_session_factory):
        """Entries consumed by exact matching don't appear in multi matching."""
        b1 = _pe("b1", str(uuid.uuid4()), "ngn_to_cny", "1000000", 90)
        b2 = _pe("b2", str(uuid.uuid4()), "ngn_to_cny", "5000000", 80)
        s1 = _pe("s1", str(uuid.uuid4()), "cny_to_ngn", "1000000", 90)
        s2 = _pe("s2", str(uuid.uuid4()), "cny_to_ngn", "2500000", 80)
        s3 = _pe("s3", str(uuid.uuid4()), "cny_to_ngn", "2500000", 70)

        mock_pool_mgr.get_pool_snapshot = AsyncMock(
            side_effect=[
                [b1, b2],  # buy pool
                [s1, s2, s3],  # sell pool
            ]
        )

        engine = MatchingEngine(pool_mgr=mock_pool_mgr, session_factory=mock_session_factory)

        with (
            patch("app.matching_engine.engine.check_timeouts", new_callable=AsyncMock, return_value=[]),
            patch.object(engine, "_persist_exact_match", new_callable=AsyncMock, return_value=([], [])),
            patch.object(engine, "_persist_multi_match", new_callable=AsyncMock, return_value=([], [])) as mock_multi_persist,
            patch.object(engine, "_persist_partial_match", new_callable=AsyncMock, return_value=([], [])),
            patch.object(engine, "_handle_partial_remainders", new_callable=AsyncMock),
            patch.object(engine, "_handle_timeouts", new_callable=AsyncMock),
        ):
            result = await engine.run_cycle()

        # b1 and s1 should be exact matched (1M vs 1M)
        # After removal: buy=[b2(5M)], sell=[s2(2.5M), s3(2.5M)]
        # b2 (5M) should multi-match with s2+s3
        exact_count = result.get("exact_matches", 0)
        multi_count = result.get("multi_matches", 0)
        assert exact_count == 1  # b1-s1
        assert multi_count == 1  # b2 target, s2+s3 legs

    @pytest.mark.asyncio
    async def test_multi_matched_excluded_from_partial(self, mock_pool_mgr, mock_session_factory):
        """Entries consumed by multi matching don't appear in partial matching."""
        b1 = _pe("b1", str(uuid.uuid4()), "ngn_to_cny", "5000000", 90)
        b2 = _pe("b2", str(uuid.uuid4()), "ngn_to_cny", "800000", 80)
        s1 = _pe("s1", str(uuid.uuid4()), "cny_to_ngn", "2500000", 90)
        s2 = _pe("s2", str(uuid.uuid4()), "cny_to_ngn", "2500000", 80)
        s3 = _pe("s3", str(uuid.uuid4()), "cny_to_ngn", "500000", 70)

        mock_pool_mgr.get_pool_snapshot = AsyncMock(
            side_effect=[
                [b1, b2],       # buy pool
                [s1, s2, s3],   # sell pool
            ]
        )

        engine = MatchingEngine(pool_mgr=mock_pool_mgr, session_factory=mock_session_factory)

        with (
            patch("app.matching_engine.engine.check_timeouts", new_callable=AsyncMock, return_value=[]),
            patch.object(engine, "_persist_exact_match", new_callable=AsyncMock, return_value=([], [])),
            patch.object(engine, "_persist_multi_match", new_callable=AsyncMock, return_value=([], [])),
            patch.object(engine, "_persist_partial_match", new_callable=AsyncMock, return_value=([], [])),
            patch.object(engine, "_handle_partial_remainders", new_callable=AsyncMock),
            patch.object(engine, "_handle_timeouts", new_callable=AsyncMock),
        ):
            result = await engine.run_cycle()

        # No exact matches (no amounts within 0.5%)
        # Multi: b1(5M) target, s1(2.5M)+s2(2.5M) legs
        # After removal: buy=[b2(800K)], sell=[s3(500K)]
        # Partial: b2(800K) vs s3(500K) -> 500K matched (62.5% > 10%)
        assert result.get("exact_matches", 0) == 0
        assert result.get("multi_matches", 0) == 1
        assert result.get("partial_matches", 0) == 1


# ===========================================================================
# TEST: Persist Exact Match
# ===========================================================================


class TestPersistExactMatch:
    """_persist_exact_match creates Match record and updates transactions."""

    @pytest.mark.asyncio
    async def test_exact_match_record_fields(self, mock_session):
        """Match record has correct type, amount, and transaction IDs."""
        buy_txn_id = str(uuid.uuid4())
        sell_txn_id = str(uuid.uuid4())
        trader_id = uuid.uuid4()

        buy_entry = _pe("pe-b1", buy_txn_id, "ngn_to_cny", "1000000", target_amount="150000")
        sell_entry = _pe("pe-s1", sell_txn_id, "cny_to_ngn", "1000000")

        buy_txn = _funded_txn(trader_id, "ngn_to_cny", "1000000", uuid.UUID(buy_txn_id))
        sell_txn = _funded_txn(trader_id, "cny_to_ngn", "1000000", uuid.UUID(sell_txn_id))

        txn_map = {buy_txn_id: buy_txn, sell_txn_id: sell_txn}
        mock_session.execute = AsyncMock(side_effect=_make_execute_side_effects(txn_map))

        engine = MatchingEngine()
        match_data = {
            "type": "exact",
            "pool_a_entry": buy_entry,
            "pool_b_entry": sell_entry,
            "matched_amount": Decimal("1000000"),
        }

        redis_ops, notifications = await engine._persist_exact_match(
            mock_session, "MC-20260212-1430", match_data,
        )

        # Verify session.add was called with a Match record
        assert mock_session.add.called
        match_record = mock_session.add.call_args[0][0]
        assert isinstance(match_record, Match)
        assert match_record.match_type == MatchType.EXACT
        assert match_record.matched_amount == Decimal("1000000")
        assert match_record.cycle_id == "MC-20260212-1430"
        assert str(match_record.buy_transaction_id) == buy_txn_id
        assert str(match_record.sell_transaction_id) == sell_txn_id

    @pytest.mark.asyncio
    async def test_exact_match_txn_status_matched(self, mock_session):
        """Both transactions transition to MATCHED."""
        buy_txn_id = str(uuid.uuid4())
        sell_txn_id = str(uuid.uuid4())
        trader_id = uuid.uuid4()

        buy_entry = _pe("pe-b1", buy_txn_id, "ngn_to_cny", "1000000")
        sell_entry = _pe("pe-s1", sell_txn_id, "cny_to_ngn", "1000000")

        buy_txn = _funded_txn(trader_id, "ngn_to_cny", "1000000", uuid.UUID(buy_txn_id))
        sell_txn = _funded_txn(trader_id, "cny_to_ngn", "1000000", uuid.UUID(sell_txn_id))

        txn_map = {buy_txn_id: buy_txn, sell_txn_id: sell_txn}
        mock_session.execute = AsyncMock(side_effect=_make_execute_side_effects(txn_map))

        engine = MatchingEngine()
        match_data = {
            "type": "exact",
            "pool_a_entry": buy_entry,
            "pool_b_entry": sell_entry,
            "matched_amount": Decimal("1000000"),
        }

        await engine._persist_exact_match(mock_session, "MC-20260212-1430", match_data)

        assert buy_txn.status == TransactionStatus.MATCHED
        assert sell_txn.status == TransactionStatus.MATCHED
        assert buy_txn.settlement_method == SettlementMethod.MATCHED
        assert sell_txn.settlement_method == SettlementMethod.MATCHED

    @pytest.mark.asyncio
    async def test_exact_match_redis_removals(self, mock_session):
        """Exact match collects 2 Redis removal ops."""
        buy_txn_id = str(uuid.uuid4())
        sell_txn_id = str(uuid.uuid4())
        trader_id = uuid.uuid4()

        buy_entry = _pe("pe-b1", buy_txn_id, "ngn_to_cny", "1000000")
        sell_entry = _pe("pe-s1", sell_txn_id, "cny_to_ngn", "1000000")

        buy_txn = _funded_txn(trader_id, "ngn_to_cny", "1000000", uuid.UUID(buy_txn_id))
        sell_txn = _funded_txn(trader_id, "cny_to_ngn", "1000000", uuid.UUID(sell_txn_id))

        txn_map = {buy_txn_id: buy_txn, sell_txn_id: sell_txn}
        mock_session.execute = AsyncMock(side_effect=_make_execute_side_effects(txn_map))

        engine = MatchingEngine()
        match_data = {
            "type": "exact",
            "pool_a_entry": buy_entry,
            "pool_b_entry": sell_entry,
            "matched_amount": Decimal("1000000"),
        }

        redis_ops, _ = await engine._persist_exact_match(
            mock_session, "MC-20260212-1430", match_data,
        )

        assert len(redis_ops) == 2
        assert all(op["action"] == "remove" for op in redis_ops)


# ===========================================================================
# TEST: Persist Multi Match
# ===========================================================================


class TestPersistMultiMatch:
    """_persist_multi_match creates N Match records (one per leg)."""

    @pytest.mark.asyncio
    async def test_multi_match_creates_n_records(self, mock_session):
        """3-leg multi match creates 3 Match records."""
        trader_id = uuid.uuid4()
        target_txn_id = str(uuid.uuid4())
        leg1_txn_id = str(uuid.uuid4())
        leg2_txn_id = str(uuid.uuid4())
        leg3_txn_id = str(uuid.uuid4())

        target = _pe("pe-t", target_txn_id, "ngn_to_cny", "5000000")
        leg1 = _pe("pe-l1", leg1_txn_id, "cny_to_ngn", "2000000")
        leg2 = _pe("pe-l2", leg2_txn_id, "cny_to_ngn", "1500000")
        leg3 = _pe("pe-l3", leg3_txn_id, "cny_to_ngn", "1500000")

        target_txn = _funded_txn(trader_id, "ngn_to_cny", "5000000", uuid.UUID(target_txn_id))
        leg1_txn = _funded_txn(trader_id, "cny_to_ngn", "2000000", uuid.UUID(leg1_txn_id))
        leg2_txn = _funded_txn(trader_id, "cny_to_ngn", "1500000", uuid.UUID(leg2_txn_id))
        leg3_txn = _funded_txn(trader_id, "cny_to_ngn", "1500000", uuid.UUID(leg3_txn_id))

        txn_map = {
            target_txn_id: target_txn,
            leg1_txn_id: leg1_txn,
            leg2_txn_id: leg2_txn,
            leg3_txn_id: leg3_txn,
        }
        mock_session.execute = AsyncMock(side_effect=_make_execute_side_effects(txn_map))

        engine = MatchingEngine()
        match_data = {
            "type": "multi",
            "pool_a_entry": target,
            "pool_b_entries": [leg1, leg2, leg3],
            "matched_amount": Decimal("5000000"),
            "leg_count": 3,
        }

        redis_ops, notifications = await engine._persist_multi_match(
            mock_session, "MC-20260212-1430", match_data,
        )

        # 3 Match records + 3 leg txn queries + 1 target txn query = multiple add calls
        match_records = [
            call[0][0] for call in mock_session.add.call_args_list
            if isinstance(call[0][0], Match)
        ]
        assert len(match_records) == 3
        assert all(m.match_type == MatchType.MULTI for m in match_records)
        assert all(m.cycle_id == "MC-20260212-1430" for m in match_records)

    @pytest.mark.asyncio
    async def test_multi_match_all_txns_matched(self, mock_session):
        """All transactions (target + legs) transition to MATCHED."""
        trader_id = uuid.uuid4()
        target_txn_id = str(uuid.uuid4())
        leg1_txn_id = str(uuid.uuid4())
        leg2_txn_id = str(uuid.uuid4())

        target = _pe("pe-t", target_txn_id, "ngn_to_cny", "5000000")
        leg1 = _pe("pe-l1", leg1_txn_id, "cny_to_ngn", "2500000")
        leg2 = _pe("pe-l2", leg2_txn_id, "cny_to_ngn", "2500000")

        target_txn = _funded_txn(trader_id, "ngn_to_cny", "5000000", uuid.UUID(target_txn_id))
        leg1_txn = _funded_txn(trader_id, "cny_to_ngn", "2500000", uuid.UUID(leg1_txn_id))
        leg2_txn = _funded_txn(trader_id, "cny_to_ngn", "2500000", uuid.UUID(leg2_txn_id))

        txn_map = {
            target_txn_id: target_txn,
            leg1_txn_id: leg1_txn,
            leg2_txn_id: leg2_txn,
        }
        mock_session.execute = AsyncMock(side_effect=_make_execute_side_effects(txn_map))

        engine = MatchingEngine()
        match_data = {
            "type": "multi",
            "pool_a_entry": target,
            "pool_b_entries": [leg1, leg2],
            "matched_amount": Decimal("5000000"),
            "leg_count": 2,
        }

        await engine._persist_multi_match(mock_session, "MC-20260212-1430", match_data)

        assert target_txn.status == TransactionStatus.MATCHED
        assert leg1_txn.status == TransactionStatus.MATCHED
        assert leg2_txn.status == TransactionStatus.MATCHED


# ===========================================================================
# TEST: Persist Partial Match
# ===========================================================================


class TestPersistPartialMatch:
    """_persist_partial_match handles remainders correctly."""

    @pytest.mark.asyncio
    async def test_partial_match_both_sides_partial_matched(self, mock_session):
        """Both transactions transition to PARTIAL_MATCHED."""
        trader_id = uuid.uuid4()
        buy_txn_id = str(uuid.uuid4())
        sell_txn_id = str(uuid.uuid4())

        buy_entry = _pe("pe-b1", buy_txn_id, "ngn_to_cny", "800000")
        sell_entry = _pe("pe-s1", sell_txn_id, "cny_to_ngn", "500000")

        buy_txn = _funded_txn(trader_id, "ngn_to_cny", "800000", uuid.UUID(buy_txn_id))
        sell_txn = _funded_txn(trader_id, "cny_to_ngn", "500000", uuid.UUID(sell_txn_id))

        txn_map = {buy_txn_id: buy_txn, sell_txn_id: sell_txn}
        mock_session.execute = AsyncMock(side_effect=_make_execute_side_effects(txn_map))

        engine = MatchingEngine()
        match_data = {
            "type": "partial",
            "pool_a_entry": buy_entry,
            "pool_b_entry": sell_entry,
            "matched_amount": Decimal("500000"),
            "remainder": {
                "pool_a_id": "pe-b1",
                "pool_a_remaining": Decimal("300000"),
                "pool_b_id": "pe-s1",
                "pool_b_remaining": Decimal("0"),
            },
        }

        redis_ops, _ = await engine._persist_partial_match(
            mock_session, "MC-20260212-1430", match_data,
        )

        assert buy_txn.status == TransactionStatus.PARTIAL_MATCHED
        assert sell_txn.status == TransactionStatus.PARTIAL_MATCHED
        assert buy_txn.settlement_method == SettlementMethod.PARTIAL_MATCHED

    @pytest.mark.asyncio
    async def test_partial_match_remainder_updated_consumed_removed(self, mock_session):
        """Redis: remainder side gets update, consumed side gets remove."""
        trader_id = uuid.uuid4()
        buy_txn_id = str(uuid.uuid4())
        sell_txn_id = str(uuid.uuid4())

        buy_entry = _pe("pe-b1", buy_txn_id, "ngn_to_cny", "800000")
        sell_entry = _pe("pe-s1", sell_txn_id, "cny_to_ngn", "500000")

        buy_txn = _funded_txn(trader_id, "ngn_to_cny", "800000", uuid.UUID(buy_txn_id))
        sell_txn = _funded_txn(trader_id, "cny_to_ngn", "500000", uuid.UUID(sell_txn_id))

        txn_map = {buy_txn_id: buy_txn, sell_txn_id: sell_txn}
        mock_session.execute = AsyncMock(side_effect=_make_execute_side_effects(txn_map))

        engine = MatchingEngine()
        match_data = {
            "type": "partial",
            "pool_a_entry": buy_entry,
            "pool_b_entry": sell_entry,
            "matched_amount": Decimal("500000"),
            "remainder": {
                "pool_a_id": "pe-b1",
                "pool_a_remaining": Decimal("300000"),
                "pool_b_id": "pe-s1",
                "pool_b_remaining": Decimal("0"),
            },
        }

        redis_ops, _ = await engine._persist_partial_match(
            mock_session, "MC-20260212-1430", match_data,
        )

        # Buy side (pool_a, ngn_to_cny) has remainder → update
        # Sell side (pool_b, cny_to_ngn) is consumed → remove
        update_ops = [op for op in redis_ops if op["action"] == "update"]
        remove_ops = [op for op in redis_ops if op["action"] == "remove"]

        assert len(update_ops) == 1
        assert update_ops[0]["entry_id"] == "pe-b1"
        assert update_ops[0]["new_amount"] == "300000"

        assert len(remove_ops) == 1
        assert remove_ops[0]["entry_id"] == "pe-s1"


# ===========================================================================
# TEST: Handle Timeouts
# ===========================================================================


class TestHandleTimeouts:
    """_handle_timeouts transitions timed-out txns to EXPIRED."""

    @pytest.mark.asyncio
    async def test_timeout_txn_expired(self, mock_session):
        """Timed-out transaction transitions to EXPIRED with CIPS settlement."""
        trader_id = uuid.uuid4()
        txn_id = str(uuid.uuid4())
        pe_id = str(uuid.uuid4())

        txn = _funded_txn(trader_id, "ngn_to_cny", "1000000", uuid.UUID(txn_id))

        txn_map = {txn_id: txn}
        mock_session.execute = AsyncMock(side_effect=_make_execute_side_effects(txn_map))

        engine = MatchingEngine()
        timed_out = [{
            "pool_entry_id": pe_id,
            "transaction_id": txn_id,
            "direction": "ngn_to_cny",
            "amount": "1000000",
            "reason": "pool_timeout",
            "fallback": "cips",
        }]

        await engine._handle_timeouts(mock_session, timed_out)

        assert txn.status == TransactionStatus.EXPIRED
        assert txn.settlement_method == SettlementMethod.CIPS_SETTLED

    @pytest.mark.asyncio
    async def test_timeout_empty_list(self, mock_session):
        """No error when timed_out list is empty."""
        engine = MatchingEngine()
        await engine._handle_timeouts(mock_session, [])
        # Should not raise


# ===========================================================================
# TEST: Notifications
# ===========================================================================


class TestNotifications:
    """Notifications are dispatched after commit and don't raise on failure."""

    def test_dispatch_calls_celery_task(self):
        """Notifications fire send_match_notification.delay for each entry."""
        with patch("app.tasks.notification_tasks.send_match_notification") as mock_task:
            notifications = [
                {
                    "transaction_id": "txn-1",
                    "trader_id": "trader-1",
                    "reference": "TXN-ABC",
                    "matched_amount": "1000000",
                    "status": "matched",
                },
            ]
            MatchingEngine._dispatch_notifications(notifications)
            mock_task.delay.assert_called_once()

    def test_dispatch_failure_does_not_raise(self):
        """Notification failures are swallowed (fire-and-forget)."""
        with patch(
            "app.tasks.notification_tasks.send_match_notification",
            side_effect=Exception("Celery down"),
        ):
            # Should not raise
            MatchingEngine._dispatch_notifications([
                {"transaction_id": "t1", "trader_id": "tr1", "reference": "R1",
                 "matched_amount": "100", "status": "matched"},
            ])

    def test_dispatch_empty_list(self):
        """No error when notifications list is empty."""
        with patch("app.tasks.notification_tasks.send_match_notification") as mock_task:
            MatchingEngine._dispatch_notifications([])
            mock_task.delay.assert_not_called()


# ===========================================================================
# TEST: Classify Buy/Sell
# ===========================================================================


class TestClassifyBuySell:
    """_classify_buy_sell uses direction field, not position."""

    def test_a_is_buy(self):
        a = _pe("a1", "t1", "ngn_to_cny", 1000)
        b = _pe("b1", "t2", "cny_to_ngn", 1000)
        buy, sell = MatchingEngine._classify_buy_sell(a, b)
        assert buy["id"] == "a1"
        assert sell["id"] == "b1"

    def test_a_is_sell_swaps(self):
        """When pool_a_entry is sell-side (direction-2 reversal), swap."""
        a = _pe("a1", "t1", "cny_to_ngn", 1000)
        b = _pe("b1", "t2", "ngn_to_cny", 1000)
        buy, sell = MatchingEngine._classify_buy_sell(a, b)
        assert buy["id"] == "b1"
        assert sell["id"] == "a1"


# ===========================================================================
# TEST: Derive Rate
# ===========================================================================


class TestDeriveRate:
    """_derive_rate computes source_amount / target_amount."""

    def test_rate_calculation(self):
        entry = _pe("e1", "t1", "ngn_to_cny", "1000000", target_amount="150000")
        rate = MatchingEngine._derive_rate(entry)
        expected = Decimal("1000000") / Decimal("150000")
        assert rate == expected

    def test_missing_target_returns_one(self):
        entry = _pe("e1", "t1", "ngn_to_cny", "1000000")
        rate = MatchingEngine._derive_rate(entry)
        assert rate == Decimal("1")

    def test_zero_target_returns_one(self):
        entry = _pe("e1", "t1", "ngn_to_cny", "1000000", target_amount="0")
        rate = MatchingEngine._derive_rate(entry)
        assert rate == Decimal("1")


# ===========================================================================
# TEST: Full Cycle with 20 Transactions
# ===========================================================================


class TestFullCycle20Transactions:
    """
    Seed 20 pool entries, run a full cycle, verify match counts
    and statuses in the report.

    Buy pool (10, ngn_to_cny):
      B1: 1,000,000 (score 90) — exact with S1
      B2: 2,000,000 (score 85) — exact with S2 (2,005,000 within 0.25%)
      B3: 500,000 (score 80) — exact with S3 (498,000 within 0.4%)
      B4: 5,000,000 (score 75) — multi target (S4+S5+S6 = 5M)
      B5: 800,000 (score 70) — partial with S7 (500K)
      B6: 300,000 (score 65) — partial with S8 (200K)
      B7-B10: small amounts that won't match

    Sell pool (10, cny_to_ngn):
      S1: 1,000,000 (score 90) — exact with B1
      S2: 2,005,000 (score 85) — exact with B2
      S3: 498,000 (score 80) — exact with B3
      S4: 2,000,000 (score 75) — multi leg
      S5: 1,500,000 (score 70) — multi leg
      S6: 1,500,000 (score 65) — multi leg
      S7: 500,000 (score 60) — partial with B5
      S8: 200,000 (score 55) — partial with B6
      S9-S10: small amounts that won't match
    """

    @pytest.mark.asyncio
    async def test_full_cycle_counts(self, mock_pool_mgr, mock_session_factory, mock_session):
        """Run full cycle and verify match type counts."""
        # Build pool entries
        buy_pool = [
            _pe("B1", str(uuid.uuid4()), "ngn_to_cny", "1000000", 90),
            _pe("B2", str(uuid.uuid4()), "ngn_to_cny", "2000000", 85),
            _pe("B3", str(uuid.uuid4()), "ngn_to_cny", "500000", 80),
            _pe("B4", str(uuid.uuid4()), "ngn_to_cny", "5000000", 75),
            _pe("B5", str(uuid.uuid4()), "ngn_to_cny", "800000", 70),
            _pe("B6", str(uuid.uuid4()), "ngn_to_cny", "300000", 65),
            _pe("B7", str(uuid.uuid4()), "ngn_to_cny", "10000", 30),
            _pe("B8", str(uuid.uuid4()), "ngn_to_cny", "15000", 25),
            _pe("B9", str(uuid.uuid4()), "ngn_to_cny", "20000", 20),
            _pe("B10", str(uuid.uuid4()), "ngn_to_cny", "25000", 15),
        ]

        sell_pool = [
            _pe("S1", str(uuid.uuid4()), "cny_to_ngn", "1000000", 90),
            _pe("S2", str(uuid.uuid4()), "cny_to_ngn", "2005000", 85),
            _pe("S3", str(uuid.uuid4()), "cny_to_ngn", "498000", 80),
            _pe("S4", str(uuid.uuid4()), "cny_to_ngn", "2000000", 75),
            _pe("S5", str(uuid.uuid4()), "cny_to_ngn", "1500000", 70),
            _pe("S6", str(uuid.uuid4()), "cny_to_ngn", "1500000", 65),
            _pe("S7", str(uuid.uuid4()), "cny_to_ngn", "500000", 60),
            _pe("S8", str(uuid.uuid4()), "cny_to_ngn", "200000", 55),
            _pe("S9", str(uuid.uuid4()), "cny_to_ngn", "5000", 10),
            _pe("S10", str(uuid.uuid4()), "cny_to_ngn", "8000", 5),
        ]

        mock_pool_mgr.get_pool_snapshot = AsyncMock(
            side_effect=[buy_pool, sell_pool]
        )

        # Build funded transactions for each pool entry
        trader_id = uuid.uuid4()
        all_entries = buy_pool + sell_pool
        txn_map = {}
        for entry in all_entries:
            txn = _funded_txn(
                trader_id,
                entry["direction"],
                entry["source_amount"],
                uuid.UUID(entry["transaction_id"]),
            )
            txn_map[entry["transaction_id"]] = txn

        mock_session.execute = AsyncMock(side_effect=_make_execute_side_effects(txn_map))

        engine = MatchingEngine(pool_mgr=mock_pool_mgr, session_factory=mock_session_factory)

        with (
            patch("app.matching_engine.engine.check_timeouts", new_callable=AsyncMock, return_value=[]),
            patch("app.tasks.notification_tasks.send_match_notification"),
        ):
            result = await engine.run_cycle()

        # Verify counts
        assert result["exact_matches"] == 3   # B1-S1, B2-S2, B3-S3
        assert result["multi_matches"] == 2   # B4+[S4,S5,S6], B7+[S9,S10]
        assert result["partial_matches"] == 2  # B5-S7, B6-S8

        # total matches in results (backward compat)
        # exact(3) + multi(2) + partial(2) = 7 match dicts
        assert result["results"]["total_matches"] == 7

        # Pool size captured at start
        assert result["pool_size_start"]["buy"] == 10
        assert result["pool_size_start"]["sell"] == 10
        assert result["pool_size_start"]["total"] == 20

        # Cycle ID format
        assert result["cycle_id"].startswith("MC-")

    @pytest.mark.asyncio
    async def test_full_cycle_transaction_statuses(self, mock_pool_mgr, mock_session_factory, mock_session):
        """Verify that matched transactions have correct statuses after cycle."""
        buy_pool = [
            _pe("B1", str(uuid.uuid4()), "ngn_to_cny", "1000000", 90),
            _pe("B2", str(uuid.uuid4()), "ngn_to_cny", "800000", 70),
        ]
        sell_pool = [
            _pe("S1", str(uuid.uuid4()), "cny_to_ngn", "1000000", 90),
            _pe("S2", str(uuid.uuid4()), "cny_to_ngn", "500000", 60),
        ]

        mock_pool_mgr.get_pool_snapshot = AsyncMock(
            side_effect=[buy_pool, sell_pool]
        )

        trader_id = uuid.uuid4()
        txn_map = {}
        for entry in buy_pool + sell_pool:
            txn = _funded_txn(
                trader_id,
                entry["direction"],
                entry["source_amount"],
                uuid.UUID(entry["transaction_id"]),
            )
            txn_map[entry["transaction_id"]] = txn

        mock_session.execute = AsyncMock(side_effect=_make_execute_side_effects(txn_map))

        engine = MatchingEngine(pool_mgr=mock_pool_mgr, session_factory=mock_session_factory)

        with (
            patch("app.matching_engine.engine.check_timeouts", new_callable=AsyncMock, return_value=[]),
            patch("app.tasks.notification_tasks.send_match_notification"),
        ):
            result = await engine.run_cycle()

        # B1 and S1: exact match → MATCHED
        b1_txn = txn_map[buy_pool[0]["transaction_id"]]
        s1_txn = txn_map[sell_pool[0]["transaction_id"]]
        assert b1_txn.status == TransactionStatus.MATCHED
        assert s1_txn.status == TransactionStatus.MATCHED

        # B2 and S2: partial match → PARTIAL_MATCHED then remainder → MATCHING
        b2_txn = txn_map[buy_pool[1]["transaction_id"]]
        s2_txn = txn_map[sell_pool[1]["transaction_id"]]
        # B2 has remainder (800K - 500K = 300K) → transitions back to MATCHING
        assert b2_txn.status == TransactionStatus.MATCHING
        # S2 is fully consumed (0 remainder) → stays PARTIAL_MATCHED
        assert s2_txn.status == TransactionStatus.PARTIAL_MATCHED

    @pytest.mark.asyncio
    async def test_full_cycle_report_fields(self, mock_pool_mgr, mock_session_factory, mock_session):
        """Report includes all required fields."""
        mock_pool_mgr.get_pool_snapshot = AsyncMock(return_value=[])

        engine = MatchingEngine(pool_mgr=mock_pool_mgr, session_factory=mock_session_factory)

        with patch("app.matching_engine.engine.check_timeouts", new_callable=AsyncMock, return_value=[]):
            result = await engine.run_cycle()

        # Verify all expected report fields exist
        assert "cycle_id" in result
        assert "started_at" in result
        assert "completed_at" in result
        assert "duration_seconds" in result
        assert "pool_size_start" in result
        assert "exact_matches" in result
        assert "multi_matches" in result
        assert "partial_matches" in result
        assert "timeouts" in result
        assert "total_matched_usd" in result
        assert "matching_efficiency" in result
        assert "cycle_duration_ms" in result
        assert "results" in result
        assert "results" in result and "total_matches" in result["results"]


# ===========================================================================
# TEST: Redis Ops After Commit
# ===========================================================================


class TestRedisOpsAfterCommit:
    """Redis operations are executed only after DB commit."""

    @pytest.mark.asyncio
    async def test_remove_ops_call_pool_mgr(self, mock_pool_mgr):
        """Remove ops delegate to pool_mgr.remove_from_pool."""
        engine = MatchingEngine(pool_mgr=mock_pool_mgr)
        ops = [
            {"action": "remove", "entry_id": "pe-1", "direction": "ngn_to_cny"},
            {"action": "remove", "entry_id": "pe-2", "direction": "cny_to_ngn"},
        ]
        await engine._execute_redis_ops(ops)

        assert mock_pool_mgr.remove_from_pool.call_count == 2

    @pytest.mark.asyncio
    async def test_update_ops_call_pool_mgr(self, mock_pool_mgr):
        """Update ops delegate to pool_mgr.update_entry_amount."""
        engine = MatchingEngine(pool_mgr=mock_pool_mgr)
        ops = [
            {"action": "update", "entry_id": "pe-1", "new_amount": "300000"},
        ]
        await engine._execute_redis_ops(ops)

        mock_pool_mgr.update_entry_amount.assert_called_once_with("pe-1", "300000")

    @pytest.mark.asyncio
    async def test_redis_failure_does_not_raise(self, mock_pool_mgr):
        """Redis failures are logged but don't propagate."""
        mock_pool_mgr.remove_from_pool = AsyncMock(side_effect=ConnectionError("Redis down"))
        engine = MatchingEngine(pool_mgr=mock_pool_mgr)
        ops = [{"action": "remove", "entry_id": "pe-1", "direction": "ngn_to_cny"}]

        # Should not raise
        await engine._execute_redis_ops(ops)
