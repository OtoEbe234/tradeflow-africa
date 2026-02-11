"""
Test data seeder — populates the database with sample data for development.

Usage:
    python scripts/seed_data.py

Creates sample traders, transactions, and pool entries.
"""

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.database import async_session
from app.models.trader import Trader, TraderType, KYCLevel
from app.models.transaction import Transaction, TransactionDirection, TransactionStatus


SAMPLE_TRADERS = [
    {
        "phone": "+2348012345678",
        "business_name": "Lagos Trading Co.",
        "trader_type": TraderType.NIGERIAN_IMPORTER,
        "kyc_level": KYCLevel.VERIFIED,
        "phone_verified": True,
    },
    {
        "phone": "+2348098765432",
        "business_name": "Abuja Exports Ltd.",
        "trader_type": TraderType.NIGERIAN_EXPORTER,
        "kyc_level": KYCLevel.VERIFIED,
        "phone_verified": True,
    },
    {
        "phone": "+8613800138000",
        "business_name": "Guangzhou Supplies",
        "trader_type": TraderType.CHINESE_SUPPLIER,
        "kyc_level": KYCLevel.VERIFIED,
        "phone_verified": True,
    },
]


async def seed():
    """Insert sample data into the database."""
    async with async_session() as session:
        traders = []
        for data in SAMPLE_TRADERS:
            trader = Trader(id=uuid.uuid4(), **data)
            session.add(trader)
            traders.append(trader)

        # Sample transactions
        tx1 = Transaction(
            id=uuid.uuid4(),
            trader_id=traders[0].id,
            direction=TransactionDirection.NGN_TO_CNY,
            source_amount=Decimal("5000000.00"),
            source_currency="NGN",
            target_amount=Decimal("23000.00"),
            target_currency="CNY",
            locked_rate=Decimal("0.004600"),
            status=TransactionStatus.PENDING,
            reference=f"TF-{uuid.uuid4().hex[:8].upper()}",
        )
        tx2 = Transaction(
            id=uuid.uuid4(),
            trader_id=traders[2].id,
            direction=TransactionDirection.CNY_TO_NGN,
            source_amount=Decimal("23000.00"),
            source_currency="CNY",
            target_amount=Decimal("5000000.00"),
            target_currency="NGN",
            locked_rate=Decimal("217.391304"),
            status=TransactionStatus.PENDING,
            reference=f"TF-{uuid.uuid4().hex[:8].upper()}",
        )
        session.add_all([tx1, tx2])

        await session.commit()
        print(f"Seeded {len(traders)} traders and 2 transactions.")


if __name__ == "__main__":
    asyncio.run(seed())
