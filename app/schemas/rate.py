"""
Pydantic schemas for FX rate quotes and rate data.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class RateData(BaseModel):
    """Current exchange rate data."""
    ngn_per_usd: Decimal
    cny_per_usd: Decimal
    ngn_per_cny: Decimal
    timestamp: datetime
    source: str


class RateQuoteResponse(BaseModel):
    """Full rate quote with fees and breakdown."""
    quote_id: str
    source_currency: str
    target_currency: str
    source_amount: Decimal
    target_amount: Decimal
    mid_market_rate: Decimal
    tradeflow_rate: Decimal
    fee_tier: str
    fee_percentage: Decimal
    fee_amount: Decimal
    total_cost: Decimal
    savings_vs_bank: Decimal
    quote_valid_until: datetime
