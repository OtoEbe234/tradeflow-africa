"""Tests for the matching engine — matcher algorithms and priority scoring."""

import pytest
from decimal import Decimal

from app.matching_engine.matcher import (
    find_exact_matches,
    run_exact_matching,
    run_multi_matching,
    run_partial_matching,
    EXACT_TOLERANCE_PCT,
    MULTI_MAX_LEGS,
    MULTI_MIN_FILL_PCT,
    PARTIAL_MIN_PCT,
)
from app.matching_engine.priority import calculate_priority, TIER_SCORES


# ── Helpers ────────────────────────────────────────────────────────────────

def _e(id: str, amount, score: float = 50.0) -> dict:
    """Shorthand to build a pool entry dict."""
    return {"id": id, "source_amount": str(amount), "_score": score}


# ===========================================================================
# EXACT MATCHING
# ===========================================================================


class TestExactMatchingLegacy:
    """Legacy find_exact_matches wrapper — backward compat."""

    def test_exact_match_same_amount(self):
        buy_pool = [{"id": "buy1", "amount": "1000000"}]
        sell_pool = [{"id": "sell1", "amount": "1000000"}]
        matches = find_exact_matches(buy_pool, sell_pool)
        assert len(matches) == 1
        assert matches[0]["type"] == "exact"

    def test_no_match_different_amounts(self):
        buy_pool = [{"id": "buy1", "amount": "1000000"}]
        sell_pool = [{"id": "sell1", "amount": "2000000"}]
        matches = find_exact_matches(buy_pool, sell_pool)
        assert len(matches) == 0

    def test_multiple_matches(self):
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


class TestRunExactMatching:
    """run_exact_matching with Decimal precision."""

    def test_equal_value_exact_match(self):
        """Two transactions of equal value -> exact match."""
        pool_a = [_e("a1", "100000")]
        pool_b = [_e("b1", "100000")]

        matches = run_exact_matching(pool_a, pool_b)
        assert len(matches) == 1
        assert matches[0]["type"] == "exact"
        assert matches[0]["matched_amount"] == Decimal("100000")
        assert matches[0]["pool_a_entry"]["id"] == "a1"
        assert matches[0]["pool_b_entry"]["id"] == "b1"

    def test_within_half_percent_matches(self):
        """Two transactions within 0.5% -> exact match.

        $100,000 * 0.5% = $500.  So $100,000 vs $99,600 (0.4% diff) matches.
        """
        pool_a = [_e("a1", "100000")]
        pool_b = [_e("b1", "99600")]  # 0.4% below

        matches = run_exact_matching(pool_a, pool_b)
        assert len(matches) == 1
        assert matches[0]["matched_amount"] == Decimal("99600")

    def test_exactly_half_percent_matches(self):
        """Boundary: exactly 0.5% difference -> still matches.

        $100,000 * 0.5% = $500.  $100,000 vs $99,500 = 0.5% diff.
        """
        pool_a = [_e("a1", "100000")]
        pool_b = [_e("b1", "99500")]

        matches = run_exact_matching(pool_a, pool_b)
        assert len(matches) == 1

    def test_one_percent_apart_no_match(self):
        """Two transactions 1% apart -> no match.

        $100,000 vs $99,000 = 1% diff.
        """
        pool_a = [_e("a1", "100000")]
        pool_b = [_e("b1", "99000")]

        matches = run_exact_matching(pool_a, pool_b)
        assert len(matches) == 0

    def test_over_half_percent_no_match(self):
        """Just over 0.5% (0.6%) -> no match.

        $100,000 vs $99,400 = 0.6% diff.
        """
        pool_a = [_e("a1", "100000")]
        pool_b = [_e("b1", "99400")]

        matches = run_exact_matching(pool_a, pool_b)
        assert len(matches) == 0

    def test_highest_priority_first(self):
        """Multiple possible matches -> highest priority pool_b entry matched first.

        pool_a has one $100K entry.
        pool_b has two $100K entries — high-priority b1 should be chosen.
        """
        pool_a = [_e("a1", "100000", score=50)]
        pool_b = [
            _e("b1", "100000", score=90),   # highest prio — first in list
            _e("b2", "100000", score=30),
        ]

        matches = run_exact_matching(pool_a, pool_b)
        assert len(matches) == 1
        assert matches[0]["pool_b_entry"]["id"] == "b1"

    def test_pool_a_priority_respected(self):
        """Higher-priority pool_a entries match before lower ones."""
        pool_a = [
            _e("a-high", "100000", score=90),  # will match first
            _e("a-low", "100000", score=30),
        ]
        pool_b = [_e("b1", "100000")]

        matches = run_exact_matching(pool_a, pool_b)
        assert len(matches) == 1
        assert matches[0]["pool_a_entry"]["id"] == "a-high"

    def test_each_entry_used_once(self):
        """An entry can only be matched once."""
        pool_a = [_e("a1", "100000"), _e("a2", "100000")]
        pool_b = [_e("b1", "100000")]  # only 1 available

        matches = run_exact_matching(pool_a, pool_b)
        assert len(matches) == 1

    def test_empty_pools(self):
        assert run_exact_matching([], []) == []
        assert run_exact_matching([_e("a1", "100000")], []) == []
        assert run_exact_matching([], [_e("b1", "100000")]) == []

    def test_uses_decimal_not_float(self):
        """Matched amounts must be Decimal, not float."""
        pool_a = [_e("a1", "100000.50")]
        pool_b = [_e("b1", "100000.50")]

        matches = run_exact_matching(pool_a, pool_b)
        assert isinstance(matches[0]["matched_amount"], Decimal)


# ===========================================================================
# MULTI-LEG MATCHING
# ===========================================================================


class TestRunMultiMatching:
    """run_multi_matching — one large vs. multiple small."""

    def test_full_multi_match_three_legs(self):
        """$100K vs $40K + $35K + $25K -> full multi-match (100%)."""
        pool_a = [_e("a1", "100000")]
        pool_b = [
            _e("b1", "40000", score=90),
            _e("b2", "35000", score=80),
            _e("b3", "25000", score=70),
        ]

        matches = run_multi_matching(pool_a, pool_b)
        assert len(matches) == 1
        m = matches[0]
        assert m["type"] == "multi"
        assert m["matched_amount"] == Decimal("100000")
        assert m["leg_count"] == 3
        assert m["pool_a_entry"]["id"] == "a1"
        leg_ids = [leg["id"] for leg in m["pool_b_entries"]]
        assert "b1" in leg_ids
        assert "b2" in leg_ids
        assert "b3" in leg_ids

    def test_partial_fill_below_95_rejected(self):
        """$100K vs $40K + $35K = $75K (75%) -> below 95%, rejected."""
        pool_a = [_e("a1", "100000")]
        pool_b = [
            _e("b1", "40000"),
            _e("b2", "35000"),
        ]

        matches = run_multi_matching(pool_a, pool_b)
        assert len(matches) == 0

    def test_fill_at_95_accepted(self):
        """$100K vs $50K + $45K = $95K (95%) -> exactly at threshold, accepted."""
        pool_a = [_e("a1", "100000")]
        pool_b = [
            _e("b1", "50000"),
            _e("b2", "45000"),
        ]

        matches = run_multi_matching(pool_a, pool_b)
        assert len(matches) == 1
        assert matches[0]["matched_amount"] == Decimal("95000")
        assert matches[0]["fill_pct"] == Decimal("95")

    def test_over_fill_capped_at_target(self):
        """$100K vs $60K + $60K = $120K assembled, matched_amount capped at $100K."""
        pool_a = [_e("a1", "100000")]
        pool_b = [
            _e("b1", "60000"),
            _e("b2", "60000"),
        ]

        matches = run_multi_matching(pool_a, pool_b)
        assert len(matches) == 1
        assert matches[0]["matched_amount"] == Decimal("100000")

    def test_capped_at_10_legs(self):
        """More than 10 candidates -> capped at MULTI_MAX_LEGS (10).

        $100K target vs 15 x $10K candidates.  Only first 10 used = $100K.
        """
        pool_a = [_e("a1", "100000")]
        pool_b = [_e(f"b{i}", "10000", score=100 - i) for i in range(15)]

        matches = run_multi_matching(pool_a, pool_b)
        assert len(matches) == 1
        assert matches[0]["leg_count"] == 10
        assert matches[0]["matched_amount"] == Decimal("100000")

    def test_direction_reversal_b_vs_a(self):
        """Direction 2: pool_b entry as target, filled from pool_a.

        pool_a has small entries, pool_b has one large entry.
        """
        pool_a = [
            _e("a1", "30000", score=90),
            _e("a2", "35000", score=80),
            _e("a3", "35000", score=70),
        ]
        pool_b = [_e("b1", "100000")]

        matches = run_multi_matching(pool_a, pool_b)
        assert len(matches) == 1
        m = matches[0]
        assert m["type"] == "multi"
        # The target is b1
        assert m["pool_a_entry"]["id"] == "b1"
        assert m["matched_amount"] == Decimal("100000")

    def test_skip_candidates_larger_than_target(self):
        """Candidates >= target amount are skipped (they're exact-match material)."""
        pool_a = [_e("a1", "100000")]
        pool_b = [
            _e("b1", "100000"),  # same size — skipped
            _e("b2", "200000"),  # larger — skipped
        ]

        matches = run_multi_matching(pool_a, pool_b)
        assert len(matches) == 0

    def test_empty_pools(self):
        assert run_multi_matching([], []) == []
        assert run_multi_matching([_e("a1", "100000")], []) == []

    def test_no_viable_combination(self):
        """All candidates too small to reach 95% even with 10 legs.

        $100K target vs 10 x $5K = $50K (50%) -> rejected.
        """
        pool_a = [_e("a1", "100000")]
        pool_b = [_e(f"b{i}", "5000") for i in range(10)]

        matches = run_multi_matching(pool_a, pool_b)
        assert len(matches) == 0

    def test_uses_decimal(self):
        """Matched amounts must be Decimal."""
        pool_a = [_e("a1", "100000")]
        pool_b = [_e("b1", "50000"), _e("b2", "50000")]

        matches = run_multi_matching(pool_a, pool_b)
        assert isinstance(matches[0]["matched_amount"], Decimal)


# ===========================================================================
# PARTIAL MATCHING
# ===========================================================================


class TestRunPartialMatching:
    """run_partial_matching — largest vs. largest, remainder stays."""

    def test_100k_vs_60k_partial(self):
        """$100K vs $60K -> $60K matched, $40K remains.

        $60K is 60% of $100K (>= 10%), so it's accepted.
        """
        pool_a = [_e("a1", "100000")]
        pool_b = [_e("b1", "60000")]

        matches = run_partial_matching(pool_a, pool_b)
        assert len(matches) == 1
        m = matches[0]
        assert m["type"] == "partial"
        assert m["matched_amount"] == Decimal("60000")
        assert m["remainder"]["pool_a_remaining"] == Decimal("40000")
        assert m["remainder"]["pool_b_remaining"] == Decimal("0")

    def test_100k_vs_5k_below_threshold_no_match(self):
        """$100K vs $5K -> below 10% threshold (5%), no match.

        $5K / $100K = 5% < 10% -> rejected.
        """
        pool_a = [_e("a1", "100000")]
        pool_b = [_e("b1", "5000")]

        matches = run_partial_matching(pool_a, pool_b)
        assert len(matches) == 0

    def test_exactly_10_percent_matches(self):
        """$100K vs $10K -> exactly 10%, accepted.

        $10K / $100K = 10%.
        """
        pool_a = [_e("a1", "100000")]
        pool_b = [_e("b1", "10000")]

        matches = run_partial_matching(pool_a, pool_b)
        assert len(matches) == 1
        assert matches[0]["matched_amount"] == Decimal("10000")

    def test_9_percent_rejected(self):
        """$100K vs $9K -> 9% < 10%, rejected."""
        pool_a = [_e("a1", "100000")]
        pool_b = [_e("b1", "9000")]

        matches = run_partial_matching(pool_a, pool_b)
        assert len(matches) == 0

    def test_symmetric_amounts_full_match(self):
        """$100K vs $100K -> both consumed, no remainder."""
        pool_a = [_e("a1", "100000")]
        pool_b = [_e("b1", "100000")]

        matches = run_partial_matching(pool_a, pool_b)
        assert len(matches) == 1
        m = matches[0]
        assert m["matched_amount"] == Decimal("100000")
        assert m["remainder"]["pool_a_remaining"] == Decimal("0")
        assert m["remainder"]["pool_b_remaining"] == Decimal("0")

    def test_b_larger_than_a(self):
        """$60K vs $100K -> $60K matched, $40K remains on B side."""
        pool_a = [_e("a1", "60000")]
        pool_b = [_e("b1", "100000")]

        matches = run_partial_matching(pool_a, pool_b)
        assert len(matches) == 1
        m = matches[0]
        assert m["matched_amount"] == Decimal("60000")
        assert m["remainder"]["pool_a_remaining"] == Decimal("0")
        assert m["remainder"]["pool_b_remaining"] == Decimal("40000")

    def test_multiple_partials_in_sequence(self):
        """Multiple partial matches from pool_a against different pool_b entries.

        A1 ($100K) matches B1 ($70K) -> $70K matched, $30K remains on A1
        A2 ($80K) matches B2 ($50K) -> $50K matched
        """
        pool_a = [
            _e("a1", "100000", score=90),
            _e("a2", "80000", score=80),
        ]
        pool_b = [
            _e("b1", "70000", score=90),
            _e("b2", "50000", score=80),
        ]

        matches = run_partial_matching(pool_a, pool_b)
        assert len(matches) == 2

        m1 = matches[0]
        assert m1["pool_a_entry"]["id"] == "a1"
        assert m1["pool_b_entry"]["id"] == "b1"
        assert m1["matched_amount"] == Decimal("70000")

        m2 = matches[1]
        assert m2["pool_a_entry"]["id"] == "a2"
        assert m2["pool_b_entry"]["id"] == "b2"
        assert m2["matched_amount"] == Decimal("50000")

    def test_each_entry_used_once(self):
        """Each pool entry is consumed at most once per partial pass."""
        pool_a = [_e("a1", "100000"), _e("a2", "100000")]
        pool_b = [_e("b1", "80000")]

        matches = run_partial_matching(pool_a, pool_b)
        assert len(matches) == 1
        assert matches[0]["pool_a_entry"]["id"] == "a1"

    def test_empty_pools(self):
        assert run_partial_matching([], []) == []

    def test_remainder_ids(self):
        """Remainder dict contains the correct entry IDs."""
        pool_a = [_e("a1", "100000")]
        pool_b = [_e("b1", "60000")]

        matches = run_partial_matching(pool_a, pool_b)
        r = matches[0]["remainder"]
        assert r["pool_a_id"] == "a1"
        assert r["pool_b_id"] == "b1"

    def test_uses_decimal(self):
        pool_a = [_e("a1", "100000.75")]
        pool_b = [_e("b1", "60000.25")]

        matches = run_partial_matching(pool_a, pool_b)
        assert isinstance(matches[0]["matched_amount"], Decimal)
        assert isinstance(matches[0]["remainder"]["pool_a_remaining"], Decimal)


# ===========================================================================
# PRIORITY SCORING (unchanged from Prompt #14)
# ===========================================================================

# ---------------------------------------------------------------------------
# Priority scoring — formula:
#   priority = (age_score * 0.40) + (amount_score * 0.35) + (tier_score * 0.25)
#
# Where:
#   age_score    = min(hours_in_pool / 24, 1.0) * 100
#   amount_score = min(amount_usd / 100_000, 1.0) * 100
#   tier_score   = { 1: 25, 2: 60, 3: 100 }
# ---------------------------------------------------------------------------


class TestPriorityScenarios:
    """Named scenarios with hand-calculated expected scores."""

    def test_new_entry_50k_tier2_0h(self):
        score = calculate_priority(hours_in_pool=0, amount_usd=50_000, kyc_tier=2)
        assert score == 32.5

    def test_old_entry_50k_tier2_12h(self):
        score = calculate_priority(hours_in_pool=12, amount_usd=50_000, kyc_tier=2)
        assert score == 52.5

    def test_large_entry_200k_tier3_6h(self):
        score = calculate_priority(hours_in_pool=6, amount_usd=200_000, kyc_tier=3)
        assert score == 70.0

    def test_small_old_entry_5k_tier1_23h(self):
        score = calculate_priority(hours_in_pool=23, amount_usd=5_000, kyc_tier=1)
        expected = 0.40 * (23 / 24 * 100) + 0.35 * 5 + 0.25 * 25
        assert score == pytest.approx(expected)
        assert score == pytest.approx(46.3333, abs=0.001)

    def test_scenario_ordering(self):
        new_t2 = calculate_priority(hours_in_pool=0, amount_usd=50_000, kyc_tier=2)
        old_t2 = calculate_priority(hours_in_pool=12, amount_usd=50_000, kyc_tier=2)
        large_t3 = calculate_priority(hours_in_pool=6, amount_usd=200_000, kyc_tier=3)
        small_old_t1 = calculate_priority(hours_in_pool=23, amount_usd=5_000, kyc_tier=1)
        assert large_t3 > old_t2 > small_old_t1 > new_t2


class TestAgeEdgeCases:
    def test_0_hours(self):
        assert calculate_priority(hours_in_pool=0, amount_usd=50_000, kyc_tier=2) == 32.5

    def test_24_hours(self):
        assert calculate_priority(hours_in_pool=24, amount_usd=50_000, kyc_tier=2) == 72.5

    def test_25_hours_capped(self):
        assert calculate_priority(hours_in_pool=25, amount_usd=50_000, kyc_tier=2) == 72.5

    def test_25h_equals_24h(self):
        s24 = calculate_priority(hours_in_pool=24, amount_usd=50_000, kyc_tier=2)
        s25 = calculate_priority(hours_in_pool=25, amount_usd=50_000, kyc_tier=2)
        assert s24 == s25

    def test_48_hours_still_capped(self):
        s24 = calculate_priority(hours_in_pool=24, amount_usd=50_000, kyc_tier=2)
        s48 = calculate_priority(hours_in_pool=48, amount_usd=50_000, kyc_tier=2)
        assert s24 == s48


class TestAmountEdgeCases:
    def test_zero_amount(self):
        assert calculate_priority(hours_in_pool=12, amount_usd=0, kyc_tier=2) == 35.0

    def test_1m_amount_capped(self):
        assert calculate_priority(hours_in_pool=12, amount_usd=1_000_000, kyc_tier=2) == 70.0

    def test_1m_equals_100k(self):
        s100k = calculate_priority(hours_in_pool=12, amount_usd=100_000, kyc_tier=2)
        s1m = calculate_priority(hours_in_pool=12, amount_usd=1_000_000, kyc_tier=2)
        assert s100k == s1m

    def test_exact_100k(self):
        assert calculate_priority(hours_in_pool=12, amount_usd=100_000, kyc_tier=2) == 70.0

    def test_amount_accepts_decimal(self):
        score = calculate_priority(hours_in_pool=0, amount_usd=Decimal("50000"), kyc_tier=2)
        assert score == 32.5


class TestTierScores:
    def test_tier_1_score(self):
        assert calculate_priority(hours_in_pool=0, amount_usd=0, kyc_tier=1) == 6.25

    def test_tier_2_score(self):
        assert calculate_priority(hours_in_pool=0, amount_usd=0, kyc_tier=2) == 15.0

    def test_tier_3_score(self):
        assert calculate_priority(hours_in_pool=0, amount_usd=0, kyc_tier=3) == 25.0

    def test_unknown_tier_0(self):
        assert calculate_priority(hours_in_pool=0, amount_usd=0, kyc_tier=0) == 0.0

    def test_tier_ordering(self):
        t1 = calculate_priority(hours_in_pool=6, amount_usd=50_000, kyc_tier=1)
        t2 = calculate_priority(hours_in_pool=6, amount_usd=50_000, kyc_tier=2)
        t3 = calculate_priority(hours_in_pool=6, amount_usd=50_000, kyc_tier=3)
        assert t3 > t2 > t1

    def test_tier_scores_constant(self):
        assert TIER_SCORES == {1: 25, 2: 60, 3: 100}


class TestWeightDistribution:
    def test_max_score(self):
        assert calculate_priority(hours_in_pool=24, amount_usd=100_000, kyc_tier=3) == 100.0

    def test_min_score(self):
        assert calculate_priority(hours_in_pool=0, amount_usd=0, kyc_tier=0) == 0.0

    def test_age_only(self):
        assert calculate_priority(hours_in_pool=24, amount_usd=0, kyc_tier=0) == 40.0

    def test_amount_only(self):
        assert calculate_priority(hours_in_pool=0, amount_usd=100_000, kyc_tier=0) == 35.0

    def test_tier_only(self):
        assert calculate_priority(hours_in_pool=0, amount_usd=0, kyc_tier=3) == 25.0

    def test_weights_sum_to_100(self):
        age_only = calculate_priority(hours_in_pool=24, amount_usd=0, kyc_tier=0)
        amount_only = calculate_priority(hours_in_pool=0, amount_usd=100_000, kyc_tier=0)
        tier_only = calculate_priority(hours_in_pool=0, amount_usd=0, kyc_tier=3)
        assert age_only + amount_only + tier_only == 100.0
