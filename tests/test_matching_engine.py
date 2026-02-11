"""Tests for the matching engine."""

import pytest
from decimal import Decimal
from datetime import datetime, timezone

from app.matching_engine.matcher import find_exact_matches
from app.matching_engine.priority import calculate_priority


class TestExactMatching:
    """Tests for exact matching algorithm."""

    def test_exact_match_same_amount(self):
        """Two transactions with the same amount should match."""
        buy_pool = [{"id": "buy1", "amount": "1000000"}]
        sell_pool = [{"id": "sell1", "amount": "1000000"}]

        matches = find_exact_matches(buy_pool, sell_pool)
        assert len(matches) == 1
        assert matches[0]["type"] == "exact"

    def test_no_match_different_amounts(self):
        """Transactions with >0.5% difference should not exact-match."""
        buy_pool = [{"id": "buy1", "amount": "1000000"}]
        sell_pool = [{"id": "sell1", "amount": "2000000"}]

        matches = find_exact_matches(buy_pool, sell_pool)
        assert len(matches) == 0

    def test_multiple_matches(self):
        """Multiple exact matches should be found in one pass."""
        buy_pool = [
            {"id": "buy1", "amount": "1000000"},
            {"id": "buy2", "amount": "2000000"},
        ]
        sell_pool = [
            {"id": "sell1", "amount": "1000000"},
            {"id": "sell2", "amount": "2000000"},
        ]

        matches = find_exact_matches(buy_pool, sell_pool)
        assert len(matches) == 2


class TestPriorityCalculation:
    """Tests for priority score calculation."""

    def test_verified_trader_gets_higher_score(self):
        """A verified trader should have a higher priority than unverified."""
        now = datetime.now(timezone.utc)
        amount = Decimal("1000000")

        verified_score = calculate_priority(now, amount, "verified", 0)
        unverified_score = calculate_priority(now, amount, "none", 0)

        assert verified_score > unverified_score

    def test_longer_wait_increases_priority(self):
        """Transactions waiting longer should have higher priority."""
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        amount = Decimal("1000000")

        recent = calculate_priority(now, amount, "verified", 0)
        old = calculate_priority(now - timedelta(hours=12), amount, "verified", 0)

        assert old > recent
