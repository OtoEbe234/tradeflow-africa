"""
Test data seeder — populates the database with sample data for development.

Usage:
    python scripts/seed_data.py

Creates:
  - 10 traders across all 3 KYC tiers
  - 20 transactions in various statuses
  - 5 matching pool entries (3 NGN_TO_CNY, 2 CNY_TO_NGN)

Idempotent: checks for existing phone numbers before inserting.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.database import async_session
from app.models.trader import Trader, TraderStatus, TIER_LIMITS
from app.models.transaction import (
    Transaction,
    TransactionDirection,
    TransactionStatus,
    SettlementMethod,
)
from app.models.matching_pool import MatchingPool
from app.models.match import Match, MatchType, MatchStatus

# ---------------------------------------------------------------------------
# Rate used across seed data (approximate real‑world NGN/CNY rate)
# ---------------------------------------------------------------------------

RATE_NGN_CNY = Decimal("0.004600")  # 1 NGN = 0.0046 CNY
RATE_CNY_NGN = Decimal("217.391304")  # 1 CNY ≈ 217.39 NGN

# ---------------------------------------------------------------------------
# Traders — 10 realistic Nigerian and Chinese identities
# ---------------------------------------------------------------------------

SAMPLE_TRADERS: list[dict] = [
    # --- Tier 1 (limit $5,000) — 3 traders ----------------------------------
    {
        "phone": "+2348031234501",
        "full_name": "Emeka Okafor",
        "business_name": "Emeka Phone Accessories",
        "bvn": "22345678901",
        "status": TraderStatus.ACTIVE,
        "kyc_tier": 1,
        "pin": "1234",
    },
    {
        "phone": "+2348051234502",
        "full_name": "Aisha Bello",
        "business_name": None,
        "bvn": "22345678902",
        "status": TraderStatus.PENDING,
        "kyc_tier": 1,
        "pin": "5678",
    },
    {
        "phone": "+2348071234503",
        "full_name": "Tunde Bakare",
        "business_name": "Bakare Mini Imports",
        "bvn": "22345678903",
        "status": TraderStatus.ACTIVE,
        "kyc_tier": 1,
        "pin": "9012",
    },
    # --- Tier 2 (limit $50,000) — 4 traders ---------------------------------
    {
        "phone": "+2348091234504",
        "full_name": "Ngozi Eze",
        "business_name": "Ngozi Fabrics & Textiles",
        "bvn": "22345678904",
        "nin": "12345678904",
        "status": TraderStatus.ACTIVE,
        "kyc_tier": 2,
        "pin": "1111",
    },
    {
        "phone": "+2348021234505",
        "full_name": "Ibrahim Musa",
        "business_name": "Kano Electronics Hub",
        "bvn": "22345678905",
        "nin": "12345678905",
        "status": TraderStatus.ACTIVE,
        "kyc_tier": 2,
        "pin": "2222",
    },
    {
        "phone": "+2348061234506",
        "full_name": "Funke Adeyemi",
        "business_name": "Adeyemi Beauty Supplies",
        "bvn": "22345678906",
        "nin": "12345678906",
        "status": TraderStatus.ACTIVE,
        "kyc_tier": 2,
        "pin": "3333",
    },
    {
        "phone": "+2348081234507",
        "full_name": "Chidi Nnamdi",
        "business_name": "Onitsha General Merchandise",
        "bvn": "22345678907",
        "nin": "12345678907",
        "status": TraderStatus.SUSPENDED,
        "kyc_tier": 2,
        "pin": "4444",
    },
    # --- Tier 3 (limit $500,000) — 3 traders --------------------------------
    {
        "phone": "+2348011234508",
        "full_name": "Adebayo Ogunlesi",
        "business_name": "Lagos Continental Trading Co.",
        "bvn": "22345678908",
        "nin": "12345678908",
        "cac_number": "RC1234567",
        "status": TraderStatus.ACTIVE,
        "kyc_tier": 3,
        "pin": "5555",
    },
    {
        "phone": "+2348041234509",
        "full_name": "Chioma Nwosu",
        "business_name": "Abuja Global Exports Ltd.",
        "bvn": "22345678909",
        "nin": "12345678909",
        "cac_number": "RC7654321",
        "status": TraderStatus.ACTIVE,
        "kyc_tier": 3,
        "pin": "6666",
    },
    {
        "phone": "+2348101234510",
        "full_name": "Yusuf Abdullahi",
        "business_name": "Northern Star Import-Export",
        "bvn": "22345678910",
        "nin": "12345678910",
        "cac_number": "RC2468013",
        "status": TraderStatus.ACTIVE,
        "kyc_tier": 3,
        "pin": "7777",
    },
]

# ---------------------------------------------------------------------------
# Chinese supplier details (used across transactions)
# ---------------------------------------------------------------------------

CHINESE_SUPPLIERS = [
    ("Guangzhou Hongli Electronics Co. Ltd", "Industrial and Commercial Bank of China", "6222021001100123456"),
    ("Shenzhen Huawei Trading Co.", "Bank of China", "6217001234567890123"),
    ("Yiwu Jinfeng Import-Export Co.", "China Construction Bank", "6227001234560098765"),
    ("Shanghai Mingda Textiles Co. Ltd", "Agricultural Bank of China", "6228481234567891234"),
    ("Foshan Dongpeng Ceramics Co.", "Bank of Communications", "6222801234567890987"),
    ("Ningbo Hengfeng Auto Parts Co.", "China Merchants Bank", "6225881234567890321"),
]

NIGERIAN_BANKS = [
    ("First Bank of Nigeria", "201"),
    ("Guaranty Trust Bank", "058"),
    ("Zenith Bank", "057"),
    ("Access Bank", "044"),
    ("United Bank for Africa", "033"),
    ("Fidelity Bank", "070"),
]


def _ngn_to_cny(ngn: Decimal) -> Decimal:
    """Convert NGN to CNY at the seed rate."""
    return (ngn * RATE_NGN_CNY).quantize(Decimal("0.01"))


def _cny_to_ngn(cny: Decimal) -> Decimal:
    """Convert CNY to NGN at the seed rate."""
    return (cny * RATE_CNY_NGN).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# Main seed routine
# ---------------------------------------------------------------------------


async def seed() -> None:
    """Insert sample data into the database. Safe to run multiple times."""

    async with async_session() as session:
        # ==================================================================
        # 1. TRADERS (10)
        # ==================================================================

        existing_phones = set(
            (await session.execute(select(Trader.phone))).scalars().all()
        )

        traders: list[Trader] = []
        new_count = 0
        for data in SAMPLE_TRADERS:
            if data["phone"] in existing_phones:
                # Fetch the existing row so we can use its ID later
                result = await session.execute(
                    select(Trader).where(Trader.phone == data["phone"])
                )
                traders.append(result.scalar_one())
                continue

            pin = data.pop("pin")
            bvn = data.pop("bvn", None)
            nin = data.pop("nin", None)

            trader = Trader(**data)
            trader.set_pin(pin)
            if bvn:
                trader.set_bvn(bvn)
            if nin:
                trader.set_nin(nin)

            session.add(trader)
            traders.append(trader)
            new_count += 1

        # Flush so trader IDs are usable for FK relationships
        await session.flush()
        print(f"  Traders: {new_count} new, {len(traders) - new_count} existing")

        # Set up a referral chain: traders[1] was referred by traders[0]
        if new_count > 0 and traders[1].referred_by is None:
            traders[1].referred_by = traders[0].id

        # ==================================================================
        # 2. TRANSACTIONS (20)
        # ==================================================================

        existing_txn_count = (
            await session.execute(select(Transaction.id))
        ).scalars().all()

        if len(existing_txn_count) >= 20:
            print(f"  Transactions: 0 new, {len(existing_txn_count)} existing")
            await session.commit()
            _print_summary(traders, [])
            return

        now = datetime.now(timezone.utc)

        # Helper to build an NGN→CNY transaction
        def _make_ngn_cny(trader: Trader, ngn: Decimal, supplier_idx: int,
                          status: TransactionStatus, **overrides) -> Transaction:
            sup = CHINESE_SUPPLIERS[supplier_idx % len(CHINESE_SUPPLIERS)]
            tx = Transaction(
                trader_id=trader.id,
                direction=TransactionDirection.NGN_TO_CNY,
                source_amount=ngn,
                target_amount=_ngn_to_cny(ngn),
                exchange_rate=RATE_NGN_CNY,
                fee_amount=(ngn * Decimal("0.015")).quantize(Decimal("0.01")),
                fee_percentage=Decimal("1.5000"),
                supplier_name=sup[0],
                supplier_bank=sup[1],
                status=TransactionStatus.INITIATED,  # start at INITIATED, transition below
                **overrides,
            )
            tx.set_supplier_account(sup[2])
            return tx

        # Helper to build a CNY→NGN transaction
        def _make_cny_ngn(trader: Trader, cny: Decimal, bank_idx: int,
                          status: TransactionStatus, **overrides) -> Transaction:
            bank = NIGERIAN_BANKS[bank_idx % len(NIGERIAN_BANKS)]
            tx = Transaction(
                trader_id=trader.id,
                direction=TransactionDirection.CNY_TO_NGN,
                source_amount=cny,
                target_amount=_cny_to_ngn(cny),
                exchange_rate=RATE_CNY_NGN,
                fee_amount=(cny * Decimal("0.012")).quantize(Decimal("0.01")),
                fee_percentage=Decimal("1.2000"),
                supplier_name=f"{trader.full_name} ({trader.business_name or 'Personal'})",
                supplier_bank=bank[0],
                status=TransactionStatus.INITIATED,
                **overrides,
            )
            tx.set_supplier_account(f"{bank[1]}0123456789")
            return tx

        # Build 20 transactions in various statuses
        txns: list[tuple[Transaction, TransactionStatus]] = []

        # -- INITIATED (2) ------------------------------------------------
        txns.append((_make_ngn_cny(traders[0], Decimal("4500000"), 0, TransactionStatus.INITIATED), TransactionStatus.INITIATED))
        txns.append((_make_cny_ngn(traders[4], Decimal("15000"), 0, TransactionStatus.INITIATED), TransactionStatus.INITIATED))

        # -- FUNDED (2) ---------------------------------------------------
        txns.append((_make_ngn_cny(traders[3], Decimal("8700000"), 1, TransactionStatus.FUNDED), TransactionStatus.FUNDED))
        txns.append((_make_cny_ngn(traders[5], Decimal("22000"), 1, TransactionStatus.FUNDED), TransactionStatus.FUNDED))

        # -- MATCHING (3) — these will also go into the matching pool ------
        txns.append((_make_ngn_cny(traders[7], Decimal("25000000"), 2, TransactionStatus.MATCHING), TransactionStatus.MATCHING))
        txns.append((_make_ngn_cny(traders[8], Decimal("43000000"), 3, TransactionStatus.MATCHING), TransactionStatus.MATCHING))
        txns.append((_make_cny_ngn(traders[9], Decimal("95000"), 2, TransactionStatus.MATCHING), TransactionStatus.MATCHING))

        # -- MATCHED (2) --------------------------------------------------
        txns.append((_make_ngn_cny(traders[7], Decimal("18000000"), 4, TransactionStatus.MATCHED), TransactionStatus.MATCHED))
        txns.append((_make_cny_ngn(traders[9], Decimal("80000"), 3, TransactionStatus.MATCHED), TransactionStatus.MATCHED))

        # -- PARTIAL_MATCHED (1) ------------------------------------------
        txns.append((_make_ngn_cny(traders[8], Decimal("32000000"), 5, TransactionStatus.PARTIAL_MATCHED), TransactionStatus.PARTIAL_MATCHED))

        # -- PENDING_SETTLEMENT (2) — matching pool entries -----------------
        txns.append((_make_ngn_cny(traders[3], Decimal("12000000"), 0, TransactionStatus.PENDING_SETTLEMENT), TransactionStatus.PENDING_SETTLEMENT))
        txns.append((_make_cny_ngn(traders[4], Decimal("55000"), 4, TransactionStatus.PENDING_SETTLEMENT), TransactionStatus.PENDING_SETTLEMENT))

        # -- SETTLING (1) -------------------------------------------------
        txns.append((_make_ngn_cny(traders[7], Decimal("9500000"), 1, TransactionStatus.SETTLING), TransactionStatus.SETTLING))

        # -- COMPLETED (3) ------------------------------------------------
        txns.append((_make_ngn_cny(traders[0], Decimal("6200000"), 2, TransactionStatus.COMPLETED), TransactionStatus.COMPLETED))
        txns.append((_make_cny_ngn(traders[8], Decimal("45000"), 5, TransactionStatus.COMPLETED), TransactionStatus.COMPLETED))
        txns.append((_make_ngn_cny(traders[3], Decimal("15000000"), 3, TransactionStatus.COMPLETED), TransactionStatus.COMPLETED))

        # -- CANCELLED (1) ------------------------------------------------
        txns.append((_make_ngn_cny(traders[2], Decimal("2100000"), 4, TransactionStatus.CANCELLED), TransactionStatus.CANCELLED))

        # -- EXPIRED (1) --------------------------------------------------
        txns.append((_make_cny_ngn(traders[5], Decimal("18000"), 0, TransactionStatus.EXPIRED), TransactionStatus.EXPIRED))

        # -- FAILED (1) ---------------------------------------------------
        txns.append((_make_ngn_cny(traders[4], Decimal("7800000"), 5, TransactionStatus.FAILED), TransactionStatus.FAILED))

        # -- REFUNDED (1) — from EXPIRED -----------------------------------
        txns.append((_make_cny_ngn(traders[0], Decimal("12000"), 1, TransactionStatus.REFUNDED), TransactionStatus.REFUNDED))

        # Walk each transaction through the valid transition path
        created_txns: list[Transaction] = []
        for tx, target_status in txns:
            _walk_to_status(tx, target_status)
            session.add(tx)
            created_txns.append(tx)

        await session.flush()
        print(f"  Transactions: {len(created_txns)} new")

        # ==================================================================
        # 3. MATCHING POOL (5 entries: 3 NGN_TO_CNY, 2 CNY_TO_NGN)
        # ==================================================================

        existing_pool_count = len(
            (await session.execute(select(MatchingPool.id))).scalars().all()
        )
        if existing_pool_count >= 5:
            print(f"  Pool entries: 0 new, {existing_pool_count} existing")
            await session.commit()
            _print_summary(traders, created_txns)
            return

        # Use the 3 MATCHING + 2 PENDING_SETTLEMENT txns for pool entries
        pool_txns = [
            # 3 NGN_TO_CNY (MATCHING status, indices 4, 5, and PENDING_SETTLEMENT index 10)
            (created_txns[4], "NGN"),
            (created_txns[5], "NGN"),
            (created_txns[10], "NGN"),
            # 2 CNY_TO_NGN (MATCHING index 6, PENDING_SETTLEMENT index 11)
            (created_txns[6], "CNY"),
            (created_txns[11], "CNY"),
        ]

        pool_entries = []
        for tx, currency in pool_txns:
            pool = MatchingPool(
                transaction_id=tx.id,
                trader_id=tx.trader_id,
                direction=tx.direction,
                amount=tx.source_amount,
                currency=currency,
                priority_score=Decimal("1.0000"),
                expires_at=now + timedelta(hours=24),
            )
            session.add(pool)
            pool_entries.append(pool)

        await session.flush()
        print(f"  Pool entries: {len(pool_entries)} new")

        # ==================================================================
        # Commit everything
        # ==================================================================

        await session.commit()
        _print_summary(traders, created_txns)


# ---------------------------------------------------------------------------
# Transition walker — safely moves a transaction through its lifecycle
# ---------------------------------------------------------------------------

# The shortest path from INITIATED to each target status
_STATUS_PATHS: dict[TransactionStatus, list[TransactionStatus]] = {
    TransactionStatus.INITIATED: [],
    TransactionStatus.FUNDED: [
        TransactionStatus.FUNDED,
    ],
    TransactionStatus.MATCHING: [
        TransactionStatus.FUNDED, TransactionStatus.MATCHING,
    ],
    TransactionStatus.MATCHED: [
        TransactionStatus.FUNDED, TransactionStatus.MATCHING,
        TransactionStatus.MATCHED,
    ],
    TransactionStatus.PARTIAL_MATCHED: [
        TransactionStatus.FUNDED, TransactionStatus.MATCHING,
        TransactionStatus.PARTIAL_MATCHED,
    ],
    TransactionStatus.PENDING_SETTLEMENT: [
        TransactionStatus.FUNDED, TransactionStatus.MATCHING,
        TransactionStatus.MATCHED, TransactionStatus.PENDING_SETTLEMENT,
    ],
    TransactionStatus.SETTLING: [
        TransactionStatus.FUNDED, TransactionStatus.MATCHING,
        TransactionStatus.MATCHED, TransactionStatus.PENDING_SETTLEMENT,
        TransactionStatus.SETTLING,
    ],
    TransactionStatus.COMPLETED: [
        TransactionStatus.FUNDED, TransactionStatus.MATCHING,
        TransactionStatus.MATCHED, TransactionStatus.PENDING_SETTLEMENT,
        TransactionStatus.SETTLING, TransactionStatus.COMPLETED,
    ],
    TransactionStatus.CANCELLED: [
        TransactionStatus.CANCELLED,
    ],
    TransactionStatus.EXPIRED: [
        TransactionStatus.EXPIRED,
    ],
    TransactionStatus.FAILED: [
        TransactionStatus.FUNDED, TransactionStatus.MATCHING,
        TransactionStatus.MATCHED, TransactionStatus.PENDING_SETTLEMENT,
        TransactionStatus.SETTLING, TransactionStatus.FAILED,
    ],
    TransactionStatus.REFUNDED: [
        TransactionStatus.EXPIRED, TransactionStatus.REFUNDED,
    ],
}


def _walk_to_status(tx: Transaction, target: TransactionStatus) -> None:
    """Transition a transaction through the valid path to *target*."""
    path = _STATUS_PATHS[target]
    for step in path:
        tx.transition_to(step)


def _print_summary(traders: list[Trader], txns: list[Transaction]) -> None:
    """Print a readable summary of seeded data."""
    print("\n  Seed complete!")
    print(f"  Total traders: {len(traders)}")
    tier_counts = {}
    for t in traders:
        tier_counts[t.kyc_tier] = tier_counts.get(t.kyc_tier, 0) + 1
    for tier, count in sorted(tier_counts.items()):
        print(f"    Tier {tier}: {count} traders (limit ${TIER_LIMITS[tier]:,.0f})")

    if txns:
        print(f"  Total transactions: {len(txns)}")
        status_counts: dict[str, int] = {}
        for tx in txns:
            status_counts[tx.status.value] = status_counts.get(tx.status.value, 0) + 1
        for status, count in sorted(status_counts.items()):
            print(f"    {status}: {count}")


if __name__ == "__main__":
    asyncio.run(seed())
