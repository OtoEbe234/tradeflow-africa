"""
Pydantic schemas for matching engine requests and responses.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class MatchingPoolStatus(BaseModel):
    """Current state of the matching pool."""
    total_entries: int
    ngn_to_cny_count: int
    cny_to_ngn_count: int
    ngn_to_cny_volume: Decimal
    cny_to_ngn_volume: Decimal
    oldest_entry_at: datetime | None


class MatchResult(BaseModel):
    """A single match result within a cycle."""
    match_id: UUID
    buy_transaction_id: UUID
    sell_transaction_id: UUID
    match_type: str
    matched_amount: Decimal
    matched_rate: Decimal


class MatchingCycleResult(BaseModel):
    """Summary of a matching cycle run."""
    cycle_id: str
    started_at: datetime
    completed_at: datetime
    transactions_processed: int
    matches_created: int
    total_matched_volume: Decimal
    unmatched_remaining: int
    matches: list[MatchResult]
