"""
Providus Bank webhook endpoint — receives payment notifications.

Validates HMAC signature, processes payment amounts, transitions
transactions from INITIATED → FUNDED, creates a MatchingPool DB record,
adds the entry to Redis sorted set + hash, and notifies traders.
"""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.matching_engine.pool_manager import pool_manager
from app.matching_engine.priority import calculate_priority
from app.models.matching_pool import MatchingPool
from app.models.trader import Trader
from app.models.transaction import Transaction, TransactionStatus
from app.services.payment_service import payment_service
from app.tasks.notification_tasks import send_status_update

logger = logging.getLogger(__name__)

router = APIRouter()

# Amount tolerance: payments within NGN 100 of expected are treated as exact
AMOUNT_TOLERANCE_NGN = Decimal("100")
# Minimum acceptable ratio (95%) before holding the payment
MIN_ACCEPT_RATIO = Decimal("0.95")
# Pool entries expire after this many hours
POOL_EXPIRY_HOURS = settings.MATCHING_POOL_TIMEOUT_HOURS  # default 24


async def _process_payment(
    paid_amount: Decimal,
    account_number: str,
    session_id: str,
    db: AsyncSession,
) -> dict:
    """
    Core payment processing logic, shared by webhook and dev endpoint.

    1. Decode reference from account number
    2. Look up transaction
    3. Duplicate guard
    4. Amount classification (exact, adjusted, held, overpayment)
    5. Transition to FUNDED
    6. Create MatchingPool DB record
    7. Add to Redis sorted set + hash via pool_manager
    8. Send notification
    """
    # 1. Decode reference: reverse TF{ref[4:]} → TXN-{suffix}
    if len(account_number) < 3 or not account_number.startswith("TF"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid account number format",
        )
    reference = "TXN-" + account_number[2:]

    # 2. Look up transaction by reference
    result = await db.execute(
        select(Transaction).where(Transaction.reference == reference)
    )
    txn = result.scalar_one_or_none()

    if txn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction not found for reference {reference}",
        )

    # 3. Duplicate guard
    if txn.status != TransactionStatus.INITIATED:
        return {
            "status": "duplicate",
            "reference": reference,
            "transaction_status": txn.status.value,
        }

    # 4. Calculate expected amount and classify payment
    expected_amount = txn.source_amount + txn.fee_amount
    paid = Decimal(str(paid_amount))
    diff = abs(paid - expected_amount)
    ratio = paid / expected_amount if expected_amount > 0 else Decimal("0")

    if diff <= AMOUNT_TOLERANCE_NGN:
        # Exact match (within tolerance) — accept as-is
        classification = "exact"
    elif paid > expected_amount:
        # Overpayment — accept as-is, no adjustment
        classification = "overpayment"
    elif ratio >= MIN_ACCEPT_RATIO:
        # 95-99%: accept but adjust source_amount and fee_amount proportionally
        classification = "adjusted"
        adjustment_ratio = paid / expected_amount
        txn.source_amount = (txn.source_amount * adjustment_ratio).quantize(Decimal("0.01"))
        txn.fee_amount = (txn.fee_amount * adjustment_ratio).quantize(Decimal("0.01"))
    else:
        # Below 95% — hold, notify trader to top up
        classification = "held"
        # Look up trader for notification
        trader_result = await db.execute(
            select(Trader).where(Trader.id == txn.trader_id)
        )
        trader = trader_result.scalar_one_or_none()
        if trader:
            send_status_update.delay(
                trader.phone,
                reference,
                f"underpaid — expected {expected_amount}, received {paid}. Please top up.",
            )
        return {
            "status": "held",
            "reference": reference,
            "expected_amount": str(expected_amount),
            "paid_amount": str(paid),
            "shortfall": str(expected_amount - paid),
        }

    # 5. Transition to FUNDED (auto-sets funded_at)
    txn.transition_to(TransactionStatus.FUNDED)
    await db.flush()

    # 6. Look up trader for pool data and notification
    trader_result = await db.execute(
        select(Trader).where(Trader.id == txn.trader_id)
    )
    trader = trader_result.scalar_one_or_none()

    # 7. Calculate priority
    now = datetime.now(timezone.utc)
    kyc_tier = trader.kyc_tier if trader else 0

    priority = calculate_priority(
        hours_in_pool=0.0,
        amount_usd=float(txn.source_amount),
        kyc_tier=kyc_tier,
    )

    # 8. Determine currency from direction
    direction_val = txn.direction.value if hasattr(txn.direction, "value") else txn.direction
    currency = "NGN" if direction_val == "ngn_to_cny" else "CNY"

    # 9. Create MatchingPool DB record
    expires_at = now + timedelta(hours=POOL_EXPIRY_HOURS)
    pool_entry = MatchingPool(
        transaction_id=txn.id,
        trader_id=txn.trader_id,
        direction=txn.direction,
        amount=txn.source_amount,
        currency=currency,
        priority_score=Decimal(str(priority)),
        is_active=True,
        entered_pool_at=now,
        expires_at=expires_at,
    )
    db.add(pool_entry)
    await db.flush()

    # 10. Add to Redis sorted set + hash via pool_manager
    await pool_manager.add_to_pool(
        pool_entry_id=str(pool_entry.id),
        transaction_id=str(txn.id),
        direction=direction_val,
        data={
            "reference": txn.reference,
            "source_amount": str(txn.source_amount),
            "target_amount": str(txn.target_amount),
            "direction": direction_val,
            "currency": currency,
            "trader_id": str(txn.trader_id),
            "entered_pool_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
        },
        score=priority,
    )

    # 11. Send notification
    if trader:
        send_status_update.delay(trader.phone, reference, "funded")

    logger.info(
        "Payment processed: %s → FUNDED (classification=%s, paid=%s, expected=%s, pool_entry=%s)",
        reference, classification, paid, expected_amount, pool_entry.id,
    )

    return {
        "status": "success",
        "reference": reference,
        "classification": classification,
        "paid_amount": str(paid),
        "expected_amount": str(expected_amount),
        "transaction_status": "funded",
        "pool_entry_id": str(pool_entry.id),
    }


@router.post("/providus")
async def providus_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Providus Bank payment notification webhook.

    No auth — validated by HMAC-SHA512 signature in X-Auth-Signature header.
    """
    body = await request.body()
    signature = request.headers.get("X-Auth-Signature", "")

    if not payment_service.verify_webhook_signature(body, signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    payload = await request.json()

    # Validate required fields
    account_number = payload.get("accountNumber")
    transaction_amount = payload.get("transactionAmount")
    session_id = payload.get("sessionId")

    if not account_number or transaction_amount is None or not session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required fields: accountNumber, transactionAmount, sessionId",
        )

    try:
        paid_amount = Decimal(str(transaction_amount))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid transactionAmount",
        )

    result = await _process_payment(paid_amount, account_number, session_id, db)
    return result
