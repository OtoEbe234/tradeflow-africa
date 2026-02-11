"""
Shared test fixtures for TradeFlow Africa.

Provides async test client, database session, and Redis mocks.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest_asyncio.fixture
async def client():
    """Async HTTP test client for the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def sample_trader():
    """Sample trader data for testing."""
    return {
        "phone": "+2348012345678",
        "business_name": "Lagos Trading Co.",
        "trader_type": "nigerian_importer",
    }


@pytest.fixture
def sample_transaction():
    """Sample transaction data for testing."""
    return {
        "direction": "ngn_to_cny",
        "source_amount": 5_000_000.00,
        "source_currency": "NGN",
        "beneficiary_name": "Guangzhou Supplies Ltd",
        "beneficiary_account": "6222021234567890",
        "beneficiary_bank": "Bank of China",
    }
