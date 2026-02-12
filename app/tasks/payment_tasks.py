"""
Payment Celery tasks — expire stale unfunded transactions.

Runs on a schedule to transition INITIATED transactions that have
exceeded PAYMENT_EXPIRY_HOURS to EXPIRED status.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import settings
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _expire_stale_transactions_async() -> dict:
    """
    Async inner function that expires stale INITIATED transactions.

    Uses async_session() directly (not FastAPI deps — Celery runs
    outside request lifecycle).
    """
    from app.database import async_session
    from app.models.transaction import Transaction, TransactionStatus
    from app.models.trader import Trader
    from app.tasks.notification_tasks import send_status_update

    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.PAYMENT_EXPIRY_HOURS)
    expired_references = []

    async with async_session() as session:
        result = await session.execute(
            select(Transaction).where(
                Transaction.status == TransactionStatus.INITIATED,
                Transaction.created_at < cutoff,
            )
        )
        stale_txns = list(result.scalars().all())

        for txn in stale_txns:
            txn.transition_to(TransactionStatus.EXPIRED)
            expired_references.append(txn.reference)

            # Look up trader for notification
            trader_result = await session.execute(
                select(Trader).where(Trader.id == txn.trader_id)
            )
            trader = trader_result.scalar_one_or_none()
            if trader:
                send_status_update.delay(trader.phone, txn.reference, "expired")

            logger.info("Expired stale transaction %s", txn.reference)

        await session.commit()

    return {
        "expired_count": len(expired_references),
        "expired_references": expired_references,
        "cutoff": cutoff.isoformat(),
    }


@celery_app.task(name="app.tasks.payment_tasks.expire_stale_transactions")
def expire_stale_transactions():
    """
    Expire INITIATED transactions older than PAYMENT_EXPIRY_HOURS.

    Celery tasks are synchronous, so we run the async function
    in an event loop (same pattern as matching_tasks.py).
    """
    logger.info("Starting stale transaction expiry check")
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(_expire_stale_transactions_async())
        logger.info(
            "Expiry check completed: %d transactions expired",
            result["expired_count"],
        )
        return result
    except Exception:
        logger.exception("Stale transaction expiry failed")
        raise
    finally:
        loop.close()
