"""
Transaction endpoints — create, list, get, and cancel cross-border payments.

Create flow:
  1. Verify PIN
  2. Validate quote or generate fresh rate
  3. Check monthly limit
  4. Create transaction record (INITIATED)
  5. Return details + mock deposit instructions
"""

import json
import logging
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_trader
from app.core.security import verify_pin
from app.database import get_db
from app.models.trader import Trader
from app.models.transaction import (
    Transaction,
    TransactionDirection,
    TransactionStatus,
)
from app.redis_client import get_redis
from app.schemas.transaction import (
    CancelRequest,
    DepositInstructions,
    TransactionCreateRequest,
    TransactionListResponse,
    TransactionResponse,
)
from app.services.rate_service import MIN_FEE_NGN, RateService

logger = logging.getLogger(__name__)

router = APIRouter()

# Minimum transaction amounts
MIN_AMOUNT_NGN = Decimal("10000")
MIN_AMOUNT_CNY = Decimal("100")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_response(
    txn: Transaction,
    deposit_instructions: DepositInstructions | None = None,
) -> TransactionResponse:
    """Build a TransactionResponse from an ORM Transaction object."""
    direction = txn.direction
    if isinstance(direction, TransactionDirection):
        direction = direction.value

    if direction == "ngn_to_cny":
        src, tgt = "NGN", "CNY"
    else:
        src, tgt = "CNY", "NGN"

    status_val = txn.status
    if isinstance(status_val, TransactionStatus):
        status_val = status_val.value

    return TransactionResponse(
        id=txn.id,
        reference=txn.reference,
        trader_id=txn.trader_id,
        direction=direction,
        source_currency=src,
        target_currency=tgt,
        source_amount=txn.source_amount,
        target_amount=txn.target_amount,
        exchange_rate=txn.exchange_rate,
        fee_amount=txn.fee_amount,
        fee_percentage=txn.fee_percentage,
        supplier_name=txn.supplier_name,
        supplier_bank=txn.supplier_bank,
        status=status_val,
        funded_at=txn.funded_at,
        matched_at=txn.matched_at,
        settled_at=txn.settled_at,
        created_at=txn.created_at,
        deposit_instructions=deposit_instructions,
    )


def _verify_trader_pin(pin: str, trader: Trader) -> None:
    """Verify PIN or raise appropriate HTTP error."""
    if trader.pin_hash is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="PIN has not been set. Complete registration first.",
        )
    if not verify_pin(pin, trader.pin_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid PIN",
        )


# ---------------------------------------------------------------------------
# POST / — Create transaction
# ---------------------------------------------------------------------------


@router.post("/", response_model=TransactionResponse, status_code=status.HTTP_201_CREATED)
async def create_transaction(
    payload: TransactionCreateRequest,
    trader: Trader = Depends(get_current_trader),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """
    Create a new cross-border payment transaction.

    1. Verify PIN
    2. Validate quote_id (if provided) or generate fresh rate
    3. Check trader monthly limit
    4. Validate supplier account (10-20 digits, enforced by schema)
    5. Calculate fee based on trader's volume tier
    6. Create transaction record (INITIATED)
    7. Generate mock deposit instructions
    8. Return full transaction details
    """
    # 1. Verify PIN
    _verify_trader_pin(payload.pin, trader)

    # 2. Validate currency pair
    src = payload.source_currency.upper()
    tgt = payload.target_currency.upper()
    if (src, tgt) not in (("NGN", "CNY"), ("CNY", "NGN")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only NGN/CNY and CNY/NGN pairs are supported.",
        )

    # 3. Validate minimum amount
    if src == "NGN" and payload.source_amount < MIN_AMOUNT_NGN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Minimum transaction amount is NGN {MIN_AMOUNT_NGN:,.0f}.",
        )
    if src == "CNY" and payload.source_amount < MIN_AMOUNT_CNY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Minimum transaction amount is CNY {MIN_AMOUNT_CNY:,.0f}.",
        )

    # 4. Fetch current rates (needed for USD conversion + fresh quote fallback)
    svc = RateService(redis)
    rates = await svc.get_rates()
    ngn_per_usd = Decimal(rates["ngn_per_usd"])
    ngn_per_cny = Decimal(rates["ngn_per_cny"])

    # 5. Convert source amount to USD for monthly limit check
    if src == "NGN":
        amount_usd = (payload.source_amount / ngn_per_usd).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP,
        )
    else:
        amount_usd = (
            payload.source_amount * ngn_per_cny / ngn_per_usd
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # 6. Monthly limit check
    if trader.exceeds_monthly_limit(amount_usd):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Transaction would exceed your monthly limit. "
                f"Used: ${trader.monthly_used:,.2f} / "
                f"Limit: ${trader.monthly_limit:,.2f} (Tier {trader.kyc_tier})."
            ),
        )

    # 7. Determine rate, fees, and target amount
    if payload.quote_id:
        raw = await redis.get(f"quote:{payload.quote_id}")
        if raw is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Quote has expired or is invalid. Request a new quote.",
            )
        quote = json.loads(raw)
        exchange_rate = Decimal(quote["mid_market_rate"])
        target_amount = Decimal(quote["target_amount"])
        fee_pct = Decimal(quote["fee_percentage"])
        fee_amount = Decimal(quote["fee_amount"])
    else:
        exchange_rate = ngn_per_cny
        _, fee_pct = RateService.get_fee_tier(amount_usd)

        if src == "NGN":
            fee_amount = max(
                (payload.source_amount * fee_pct / Decimal("100")).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP,
                ),
                MIN_FEE_NGN,
            )
            target_amount = (payload.source_amount / exchange_rate).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )
        else:
            min_fee_cny = (MIN_FEE_NGN / ngn_per_cny).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )
            fee_amount = max(
                (payload.source_amount * fee_pct / Decimal("100")).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP,
                ),
                min_fee_cny,
            )
            target_amount = (payload.source_amount * exchange_rate).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )

    # 8. Create transaction
    direction = (
        TransactionDirection.NGN_TO_CNY if src == "NGN"
        else TransactionDirection.CNY_TO_NGN
    )

    txn = Transaction(
        trader_id=trader.id,
        direction=direction,
        source_amount=payload.source_amount,
        target_amount=target_amount,
        exchange_rate=exchange_rate,
        fee_amount=fee_amount,
        fee_percentage=fee_pct,
        supplier_name=payload.supplier_name,
        supplier_bank=payload.supplier_bank,
        status=TransactionStatus.INITIATED,
    )
    txn.set_supplier_account(payload.supplier_account)

    # 9. Update trader monthly usage
    trader.monthly_used = trader.monthly_used + amount_usd

    db.add(txn)
    await db.flush()

    logger.info(
        "Transaction %s created: %s %s → %s %s (fee %s)",
        txn.reference, src, payload.source_amount, tgt, target_amount, fee_amount,
    )

    # 10. Mock deposit instructions
    instructions = DepositInstructions(
        bank_name="Providus Bank",
        account_number=f"TF{txn.reference[4:]}",
        account_name=f"TradeFlow/{txn.reference}",
        amount=payload.source_amount + fee_amount,
        currency=src,
        reference=txn.reference,
        expires_at=txn.created_at + timedelta(hours=24),
    )

    return _build_response(txn, instructions)


# ---------------------------------------------------------------------------
# GET /{id} — Get transaction
# ---------------------------------------------------------------------------


@router.get("/{transaction_id}", response_model=TransactionResponse)
async def get_transaction(
    transaction_id: UUID,
    trader: Trader = Depends(get_current_trader),
    db: AsyncSession = Depends(get_db),
):
    """Get details of a specific transaction. Must be the owner."""
    result = await db.execute(
        select(Transaction).where(Transaction.id == transaction_id)
    )
    txn = result.scalar_one_or_none()

    if txn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transaction not found",
        )

    if txn.trader_id != trader.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this transaction",
        )

    return _build_response(txn)


# ---------------------------------------------------------------------------
# GET / — List transactions
# ---------------------------------------------------------------------------


@router.get("/", response_model=TransactionListResponse)
async def list_transactions(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    date_from: str | None = Query(None, description="ISO 8601 datetime"),
    date_to: str | None = Query(None, description="ISO 8601 datetime"),
    trader: Trader = Depends(get_current_trader),
    db: AsyncSession = Depends(get_db),
):
    """
    List the authenticated trader's transactions with pagination.

    Supports optional filtering by status and date range.
    """
    # Base filter: only this trader's transactions
    base_filter = Transaction.trader_id == trader.id

    filters = [base_filter]
    if status_filter:
        filters.append(Transaction.status == status_filter)
    if date_from:
        filters.append(Transaction.created_at >= date_from)
    if date_to:
        filters.append(Transaction.created_at <= date_to)

    # Count total matching
    count_stmt = select(func.count(Transaction.id)).where(*filters)
    total = (await db.execute(count_stmt)).scalar_one()

    # Fetch page
    items_stmt = (
        select(Transaction)
        .where(*filters)
        .order_by(Transaction.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    result = await db.execute(items_stmt)
    items = list(result.scalars().all())

    return TransactionListResponse(
        items=[_build_response(t) for t in items],
        total=total,
        page=page,
        per_page=per_page,
    )


# ---------------------------------------------------------------------------
# POST /{id}/cancel — Cancel transaction
# ---------------------------------------------------------------------------


@router.post("/{transaction_id}/cancel", response_model=TransactionResponse)
async def cancel_transaction(
    transaction_id: UUID,
    payload: CancelRequest,
    trader: Trader = Depends(get_current_trader),
    db: AsyncSession = Depends(get_db),
):
    """
    Cancel a transaction. Only allowed when status is INITIATED.

    Requires PIN verification.
    """
    # 1. Verify PIN
    _verify_trader_pin(payload.pin, trader)

    # 2. Find transaction
    result = await db.execute(
        select(Transaction).where(Transaction.id == transaction_id)
    )
    txn = result.scalar_one_or_none()

    if txn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transaction not found",
        )

    if txn.trader_id != trader.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this transaction",
        )

    # 3. Only INITIATED transactions can be cancelled via this endpoint
    if txn.status != TransactionStatus.INITIATED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot cancel transaction in '{txn.status.value}' status. "
                f"Only INITIATED transactions can be cancelled."
            ),
        )

    # 4. Transition to CANCELLED
    txn.transition_to(TransactionStatus.CANCELLED)
    await db.flush()

    logger.info("Transaction %s cancelled by trader", txn.reference)

    return _build_response(txn)
