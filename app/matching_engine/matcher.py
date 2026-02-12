"""
Matching algorithms — exact, multi-leg, and partial matching.

Implements the core matching logic that pairs pool_a (buy-side) and
pool_b (sell-side) transactions.  All financial arithmetic uses
``Decimal`` to avoid floating-point rounding errors.

Pool entries are dicts as returned by ``PoolManager.get_pool_snapshot``::

    {
        "id": "pe-xxx",
        "source_amount": "1000000",  # the matchable amount
        "_score": 72.5,              # priority (highest first)
        ...
    }

Each algorithm returns a list of match dicts with ``type``,
``pool_a_entry`` / ``pool_b_entries``, ``matched_amount``, etc.
"""

from __future__ import annotations

from decimal import Decimal

from app.matching_engine.config import TOLERANCE_PERCENT

# ── Configurable thresholds ─────────────────────────────────────────────

EXACT_TOLERANCE_PCT = Decimal("0.5")      # 0.5% tolerance for "exact"
MULTI_MIN_FILL_PCT = Decimal("95")        # assembled total must be >= 95% of target
MULTI_MAX_LEGS = 10                       # max transactions per multi-match
PARTIAL_MIN_PCT = Decimal("10")           # overlap must be >= 10% of smaller side


# ── Helpers ─────────────────────────────────────────────────────────────

def _amount(entry: dict) -> Decimal:
    """Extract the matchable amount from a pool entry as Decimal.

    Supports both ``source_amount`` (production pool entries) and
    ``amount`` (legacy / simplified test entries).
    """
    raw = entry.get("source_amount") or entry.get("amount") or "0"
    return Decimal(str(raw))


# ── 1. Exact matching ──────────────────────────────────────────────────


def run_exact_matching(
    pool_a: list[dict],
    pool_b: list[dict],
    tolerance_pct: Decimal = EXACT_TOLERANCE_PCT,
) -> list[dict]:
    """
    Pair transactions whose amounts are within *tolerance_pct* %.

    * Pools are assumed sorted by descending priority (highest-score
      entries come first, as returned by ``get_pool_snapshot``).
    * For each entry in *pool_a*, the first eligible entry from
      *pool_b* is chosen — this respects priority ordering.
    * Both entries are consumed (added to used sets).

    Returns a list of match dicts::

        {
            "type": "exact",
            "pool_a_entry": { ... },
            "pool_b_entry": { ... },
            "matched_amount": Decimal,
        }
    """
    matches: list[dict] = []
    used_a: set[int] = set()
    used_b: set[int] = set()

    for i, a in enumerate(pool_a):
        if i in used_a:
            continue
        a_amt = _amount(a)
        if a_amt <= 0:
            continue

        for j, b in enumerate(pool_b):
            if j in used_b:
                continue
            b_amt = _amount(b)
            if b_amt <= 0:
                continue

            diff_pct = abs(a_amt - b_amt) / a_amt * 100
            if diff_pct <= tolerance_pct:
                matched = min(a_amt, b_amt)
                matches.append({
                    "type": "exact",
                    "pool_a_entry": a,
                    "pool_b_entry": b,
                    "matched_amount": matched,
                })
                used_a.add(i)
                used_b.add(j)
                break  # move to next pool_a entry

    return matches


# ── 2. Multi-leg matching ──────────────────────────────────────────────


def _greedy_multi(
    target: dict,
    candidates: list[dict],
    used_indices: set[int],
) -> dict | None:
    """
    Try to fill *target* with multiple smaller *candidates* (greedy knapsack).

    * Candidates are tried in order (highest-priority first).
    * At most ``MULTI_MAX_LEGS`` candidates are consumed.
    * Assembled total must reach ``MULTI_MIN_FILL_PCT`` % of *target*.
    * Returns a match dict or ``None`` if no viable combination exists.
    """
    target_amt = _amount(target)
    if target_amt <= 0:
        return None

    legs: list[dict] = []
    leg_indices: list[int] = []
    assembled = Decimal("0")

    for idx, c in enumerate(candidates):
        if idx in used_indices:
            continue
        c_amt = _amount(c)
        if c_amt <= 0:
            continue
        # Skip candidates that are larger than or within exact-match range of target
        # (those should have been caught by exact matching already)
        if c_amt >= target_amt:
            continue

        legs.append(c)
        leg_indices.append(idx)
        assembled += c_amt

        if len(legs) >= MULTI_MAX_LEGS:
            break
        if assembled >= target_amt:
            break

    if not legs:
        return None

    fill_pct = assembled / target_amt * 100
    if fill_pct < MULTI_MIN_FILL_PCT:
        return None

    matched = min(assembled, target_amt)

    # Mark all used
    for li in leg_indices:
        used_indices.add(li)

    return {
        "type": "multi",
        "pool_a_entry": target,
        "pool_b_entries": legs,
        "matched_amount": matched,
        "leg_count": len(legs),
        "fill_pct": fill_pct,
    }


def run_multi_matching(
    pool_a: list[dict],
    pool_b: list[dict],
) -> list[dict]:
    """
    Greedy multi-leg matching: one large vs. multiple small.

    Runs in both directions:
      1. Try each pool_a entry as the target, fill from pool_b.
      2. Try each pool_b entry as the target, fill from pool_a.

    Returns a list of match dicts with ``type="multi"``.
    """
    matches: list[dict] = []
    used_a: set[int] = set()
    used_b: set[int] = set()

    # Direction 1: pool_a targets, pool_b fills
    for i, a_entry in enumerate(pool_a):
        if i in used_a:
            continue
        result = _greedy_multi(a_entry, pool_b, used_b)
        if result:
            used_a.add(i)
            matches.append(result)

    # Direction 2: pool_b targets, pool_a fills
    for j, b_entry in enumerate(pool_b):
        if j in used_b:
            continue
        result = _greedy_multi(b_entry, pool_a, used_a)
        if result:
            used_b.add(j)
            # Swap naming so pool_a_entry is the target from pool_b
            matches.append(result)

    return matches


# ── 3. Partial matching ────────────────────────────────────────────────


def run_partial_matching(
    pool_a: list[dict],
    pool_b: list[dict],
) -> list[dict]:
    """
    Partial matching: largest vs. largest from opposite pool.

    * Only matches if the overlap is >= ``PARTIAL_MIN_PCT`` % of the
      smaller transaction's amount.
    * The matched amount is ``min(a, b)``.
    * The remainder (difference) stays in pool for the next cycle.

    Returns a list of match dicts with ``type="partial"`` and
    a ``remainder`` field.
    """
    matches: list[dict] = []
    used_a: set[int] = set()
    used_b: set[int] = set()

    for i, a in enumerate(pool_a):
        if i in used_a:
            continue
        a_amt = _amount(a)
        if a_amt <= 0:
            continue

        for j, b in enumerate(pool_b):
            if j in used_b:
                continue
            b_amt = _amount(b)
            if b_amt <= 0:
                continue

            matched = min(a_amt, b_amt)
            smaller = min(a_amt, b_amt)

            # Only match if overlap >= 10% of the smaller transaction
            if smaller > 0 and (matched / smaller * 100) < PARTIAL_MIN_PCT:
                continue

            # Also reject if the match itself is < 10% of the *larger* side
            larger = max(a_amt, b_amt)
            if larger > 0 and (matched / larger * 100) < PARTIAL_MIN_PCT:
                continue

            remainder_a = a_amt - matched
            remainder_b = b_amt - matched

            matches.append({
                "type": "partial",
                "pool_a_entry": a,
                "pool_b_entry": b,
                "matched_amount": matched,
                "remainder": {
                    "pool_a_id": a.get("id"),
                    "pool_a_remaining": remainder_a,
                    "pool_b_id": b.get("id"),
                    "pool_b_remaining": remainder_b,
                },
            })
            used_a.add(i)
            used_b.add(j)
            break  # move to next pool_a entry

    return matches


# ── Backward-compatible aliases ─────────────────────────────────────────
# The engine.py and existing tests use the old names.

def find_exact_matches(
    buy_pool: list[dict],
    sell_pool: list[dict],
) -> list[dict]:
    """Legacy wrapper — delegates to ``run_exact_matching``.

    Re-maps output to the old format (``buy``/``sell`` keys,
    ``matched_amount`` as str) so existing callers keep working.
    """
    results = run_exact_matching(buy_pool, sell_pool)
    legacy = []
    for m in results:
        legacy.append({
            "type": "exact",
            "buy": m["pool_a_entry"],
            "sell": m["pool_b_entry"],
            "matched_amount": str(m["matched_amount"]),
        })
    return legacy


def find_multi_matches(
    buy_pool: list[dict],
    sell_pool: list[dict],
) -> list[dict]:
    """Legacy wrapper — delegates to ``run_multi_matching``."""
    return run_multi_matching(buy_pool, sell_pool)


def find_partial_matches(
    buy_pool: list[dict],
    sell_pool: list[dict],
    tolerance: float = TOLERANCE_PERCENT,
) -> list[dict]:
    """Legacy wrapper — delegates to ``run_partial_matching``."""
    return run_partial_matching(buy_pool, sell_pool)
