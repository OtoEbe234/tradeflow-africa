"""
Matching engine configuration constants.

Defines tolerance thresholds, pool keys, and scoring weights
used by the matching algorithm.
"""

from app.config import settings

# Redis key prefixes for pool management
POOL_KEY_BUY = "pool:ngn_to_cny"
POOL_KEY_SELL = "pool:cny_to_ngn"
POOL_LOCK_KEY = "pool:lock"

# Matching tolerance — maximum percentage difference for partial matches
TOLERANCE_PERCENT = settings.MATCHING_TOLERANCE_PERCENT

# Priority score weights (must sum to 1.0)
WEIGHT_AGE = 0.40            # Longer wait = higher priority
WEIGHT_AMOUNT = 0.35         # Larger amounts = higher priority
WEIGHT_TIER = 0.25           # Higher KYC tier = higher priority

# Pool timeout — transactions exceeding this are routed to CIPS
POOL_TIMEOUT_HOURS = settings.MATCHING_POOL_TIMEOUT_HOURS

# Maximum number of transactions to process per cycle
MAX_PER_CYCLE = 500
