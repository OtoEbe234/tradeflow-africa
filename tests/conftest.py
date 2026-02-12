"""
Shared test fixtures for TradeFlow Africa.

Provides async test client, database session mocks, Redis mocks,
and RSA key fixtures for JWT testing.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

from app.database import get_db
from app.redis_client import get_redis
from app.models.trader import Trader, TraderStatus, configure_fernet
from app.services import auth_service


# --- Fernet Key Fixture ---


@pytest.fixture(scope="session")
def test_fernet_key():
    """Generate a Fernet key for tests."""
    return Fernet.generate_key()


@pytest.fixture(autouse=True)
def setup_fernet(test_fernet_key):
    """Configure the Trader model to use the test Fernet key."""
    configure_fernet(test_fernet_key)


# --- RSA Key Fixtures ---


@pytest.fixture(scope="session")
def test_rsa_keys():
    """Generate a temporary RSA keypair for test JWT signing."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    return {"private_key": private_pem, "public_key": public_pem}


@pytest.fixture(autouse=True)
def auth_service_with_keys(test_rsa_keys):
    """Configure auth_service to use test RSA keys for every test."""
    auth_service.configure_keys(
        private_key=test_rsa_keys["private_key"],
        public_key=test_rsa_keys["public_key"],
        algorithm="RS256",
    )


# --- Mock Redis ---


@pytest.fixture
def mock_redis():
    """AsyncMock Redis client with common methods."""
    redis = AsyncMock()
    redis.setex = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.delete = AsyncMock()
    redis.incr = AsyncMock(return_value=1)
    redis.expire = AsyncMock()
    return redis


# --- Mock Database Session ---


def _make_trader(**overrides) -> Trader:
    """Create a Trader instance with test defaults via the normal constructor."""
    defaults = {
        "phone": "+2348012345678",
        "full_name": "Adebayo Ogunlesi",
        "business_name": "Lagos Trading Co.",
        "status": TraderStatus.ACTIVE,
    }
    defaults.update(overrides)
    return Trader(**defaults)


@pytest.fixture
def make_trader():
    """Factory fixture for creating Trader instances."""
    return _make_trader


@pytest.fixture
def mock_db():
    """AsyncMock database session."""
    db = AsyncMock()

    # Mock the result object returned by db.execute()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=None)
    db.execute = AsyncMock(return_value=mock_result)
    db.flush = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    return db


# --- Dependency Override Helpers ---


@pytest_asyncio.fixture
async def client(mock_db, mock_redis):
    """
    Async HTTP test client with get_db and get_redis overridden
    to use test doubles.
    """
    from app.main import app

    async def override_get_db():
        yield mock_db

    async def override_get_redis():
        return mock_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


# --- Sample Data ---


@pytest.fixture
def sample_registration():
    """Sample registration payload (phone only)."""
    return {"phone": "+2348012345678"}


@pytest.fixture
def sample_trader():
    """Sample full trader creation payload for testing."""
    return {
        "phone": "+2348012345678",
        "full_name": "Adebayo Ogunlesi",
        "business_name": "Lagos Trading Co.",
        "pin": "1234",
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
