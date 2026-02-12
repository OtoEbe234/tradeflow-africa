"""
Priority score calculation for the matching engine.

Computes a composite score for each pool entry to determine
matching order. Higher scores are matched first.

Formula:
    priority = (age_score * 0.40) + (amount_score * 0.35) + (tier_score * 0.25)

Where:
    age_score    = min(hours_in_pool / 24, 1.0) * 100
    amount_score = min(amount_usd / 100_000, 1.0) * 100
    tier_score   = { 1: 25, 2: 60, 3: 100 }
"""

from app.matching_engine.config import WEIGHT_AGE, WEIGHT_AMOUNT, WEIGHT_TIER

TIER_SCORES = {
    1: 25,
    2: 60,
    3: 100,
}


def calculate_priority(
    hours_in_pool: float,
    amount_usd: float,
    kyc_tier: int,
) -> float:
    """
    Calculate a composite priority score for pool ordering.

    Args:
        hours_in_pool: How long the entry has been in the pool (hours).
        amount_usd:    Transaction amount in USD equivalent.
        kyc_tier:      Trader's KYC tier (1, 2, or 3).

    Returns:
        A float score where higher = matched first.
    """
    age_score = min(hours_in_pool / 24.0, 1.0) * 100
    amount_score = min(float(amount_usd) / 100_000, 1.0) * 100
    tier_score = TIER_SCORES.get(kyc_tier, 0)

    return (
        WEIGHT_AGE * age_score
        + WEIGHT_AMOUNT * amount_score
        + WEIGHT_TIER * tier_score
    )
