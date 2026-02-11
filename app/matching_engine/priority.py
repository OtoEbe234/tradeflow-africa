"""
Priority score calculation for the matching engine.

Computes a composite score for each transaction in the pool
to determine matching order. Higher scores are matched first.
"""

from datetime import datetime, timezone
from decimal import Decimal

from app.matching_engine.config import (
    WEIGHT_WAIT_TIME,
    WEIGHT_AMOUNT_SIZE,
    WEIGHT_KYC_LEVEL,
    WEIGHT_HISTORY,
)


KYC_SCORES = {
    "none": 0.0,
    "pending": 0.2,
    "verified": 1.0,
}


def calculate_priority(
    entered_pool_at: datetime,
    amount: Decimal,
    kyc_level: str,
    completed_transactions: int,
) -> float:
    """
    Calculate a composite priority score for pool ordering.

    Factors:
    - Wait time: normalized hours in pool (capped at 24h)
    - Amount size: log-scaled transaction size
    - KYC level: verified traders get priority
    - History: repeat traders with good track record rank higher

    Returns a float score where higher = matched first.
    """
    now = datetime.now(timezone.utc)
    wait_hours = (now - entered_pool_at).total_seconds() / 3600
    wait_score = min(wait_hours / 24.0, 1.0)

    # Log-scale amount (₦1M = 0.5, ₦10M = 0.75, ₦100M = 1.0)
    import math
    amount_float = float(amount)
    if amount_float > 0:
        amount_score = min(math.log10(amount_float) / 8.0, 1.0)
    else:
        amount_score = 0.0

    kyc_score = KYC_SCORES.get(kyc_level, 0.0)

    history_score = min(completed_transactions / 50.0, 1.0)

    return (
        WEIGHT_WAIT_TIME * wait_score
        + WEIGHT_AMOUNT_SIZE * amount_score
        + WEIGHT_KYC_LEVEL * kyc_score
        + WEIGHT_HISTORY * history_score
    )
