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

# Priority score weights
WEIGHT_WAIT_TIME = 0.4       # Longer wait = higher priority
WEIGHT_AMOUNT_SIZE = 0.3     # Larger amounts = higher priority
WEIGHT_KYC_LEVEL = 0.2       # Higher KYC tier = higher priority
WEIGHT_HISTORY = 0.1         # More completed transactions = higher priority

# Pool timeout — transactions exceeding this are routed to CIPS
POOL_TIMEOUT_HOURS = settings.MATCHING_POOL_TIMEOUT_HOURS

# Maximum number of transactions to process per cycle
MAX_PER_CYCLE = 500
