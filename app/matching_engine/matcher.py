"""
Matching algorithms — exact, multi-leg, and partial matching.

Implements the core matching logic that pairs buy-side and sell-side
transactions from the pool.
"""

from decimal import Decimal

from app.matching_engine.config import TOLERANCE_PERCENT


def find_exact_matches(buy_pool: list[dict], sell_pool: list[dict]) -> list[dict]:
    """
    Find exact matches where buy amount equals sell amount.

    An exact match occurs when two transactions have the same
    amount (converted at the current rate) within a tight tolerance.
    """
    matches = []
    used_buys = set()
    used_sells = set()

    for i, buy in enumerate(buy_pool):
        if i in used_buys:
            continue
        buy_amount = Decimal(str(buy["amount"]))

        for j, sell in enumerate(sell_pool):
            if j in used_sells:
                continue
            sell_amount = Decimal(str(sell["amount"]))

            diff_percent = abs(buy_amount - sell_amount) / buy_amount * 100
            if diff_percent <= Decimal("0.5"):  # 0.5% tolerance for "exact"
                matches.append({
                    "type": "exact",
                    "buy": buy,
                    "sell": sell,
                    "matched_amount": str(min(buy_amount, sell_amount)),
                })
                used_buys.add(i)
                used_sells.add(j)
                break

    return matches


def find_multi_matches(buy_pool: list[dict], sell_pool: list[dict]) -> list[dict]:
    """
    Find multi-leg matches where one large transaction matches multiple smaller ones.

    Example: one ₦50M buy matches five ₦10M sells.
    """
    # TODO: Implement greedy bin-packing algorithm
    # TODO: Try to fill large orders with combinations of smaller counterparties
    return []


def find_partial_matches(
    buy_pool: list[dict], sell_pool: list[dict], tolerance: float = TOLERANCE_PERCENT
) -> list[dict]:
    """
    Find partial matches within the configured tolerance.

    A partial match fills part of a transaction, leaving
    the remainder in the pool for the next cycle.
    """
    # TODO: Match partial amounts when no exact match is possible
    # TODO: Create residual pool entries for unmatched remainders
    return []
