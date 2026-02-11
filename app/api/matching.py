"""
Matching engine admin endpoints.

Provides administrative controls for the P2P matching engine
including manual cycle triggers, pool inspection, and stats.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.matching import MatchingPoolStatus, MatchingCycleResult

router = APIRouter()


@router.post("/trigger", response_model=MatchingCycleResult)
async def trigger_matching_cycle(db: AsyncSession = Depends(get_db)):
    """
    Manually trigger a matching cycle (admin only).

    Runs the matching engine immediately instead of waiting
    for the next scheduled cycle.
    """
    # TODO: Verify admin role from JWT
    # TODO: Dispatch matching cycle via Celery task
    # TODO: Return cycle summary
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/pool", response_model=MatchingPoolStatus)
async def get_pool_status():
    """Get the current state of the matching pool."""
    # TODO: Read pool statistics from Redis
    # TODO: Return counts by direction, total volume, etc.
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/history")
async def get_matching_history(
    page: int = 1, page_size: int = 20, db: AsyncSession = Depends(get_db)
):
    """Get historical matching cycle results with pagination."""
    # TODO: Query match records from database
    raise HTTPException(status_code=501, detail="Not implemented")
