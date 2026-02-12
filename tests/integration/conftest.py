"""
Integration test fixtures — real PostgreSQL and Redis.

Requires docker-compose.test.yml services to be running:
  docker compose -f docker-compose.test.yml up -d

Environment variables (set by the test runner or .env.test):
  DATABASE_URL=postgresql+asyncpg://tradeflow_test:tradeflow_test@localhost:5433/tradeflow_test
  REDIS_URL=redis://localhost:6380
  APP_ENV=development
"""

import os
import socket

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


# ── Service availability check ─────────────────────────────────────────────

def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """Return True if a TCP connection to host:port succeeds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# Read ports from env (fallback to docker-compose.test.yml defaults)
_PG_HOST = os.environ.get("PGHOST", "localhost")
_PG_PORT = int(os.environ.get("PGPORT", "5433"))
_REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
_REDIS_PORT = int(os.environ.get("REDIS_PORT", "6380"))

_PG_UP = _port_open(_PG_HOST, _PG_PORT)
_REDIS_UP = _port_open(_REDIS_HOST, _REDIS_PORT)

# Skip the entire module if either service is down
pytestmark = pytest.mark.skipif(
    not (_PG_UP and _REDIS_UP),
    reason=(
        f"Integration tests require PostgreSQL ({_PG_HOST}:{_PG_PORT}) "
        f"and Redis ({_REDIS_HOST}:{_REDIS_PORT}). "
        "Start them with: docker compose -f docker-compose.test.yml up -d"
    ),
)


# ── Patch settings BEFORE any app modules are imported ─────────────────────

os.environ.setdefault(
    "DATABASE_URL",
    f"postgresql+asyncpg://tradeflow_test:tradeflow_test@{_PG_HOST}:{_PG_PORT}/tradeflow_test",
)
os.environ.setdefault("REDIS_URL", f"redis://{_REDIS_HOST}:{_REDIS_PORT}")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("REDIS_SSL", "false")

from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402
from app.models.trader import Trader, TraderStatus, configure_fernet  # noqa: E402
from app.services import auth_service  # noqa: E402


# ── Fernet key (session-scoped, same as unit tests) ────────────────────────

@pytest.fixture(scope="session")
def test_fernet_key():
    return Fernet.generate_key()


@pytest.fixture(autouse=True)
def setup_fernet(test_fernet_key):
    configure_fernet(test_fernet_key)


# ── RSA keys for JWT (session-scoped) ──────────────────────────────────────

@pytest.fixture(scope="session")
def test_rsa_keys():
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
    auth_service.configure_keys(
        private_key=test_rsa_keys["private_key"],
        public_key=test_rsa_keys["public_key"],
        algorithm="RS256",
    )


# ── Real async engine + session ────────────────────────────────────────────

@pytest.fixture(scope="session")
def db_engine():
    """Create an async engine pointing at the test database."""
    if not (_PG_UP and _REDIS_UP):
        pytest.skip("PostgreSQL/Redis not available")
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    yield engine


@pytest_asyncio.fixture(scope="session")
async def _create_tables(db_engine):
    """Create all tables once per session, drop them after."""
    # Import all models so Base.metadata knows about them
    import app.models  # noqa: F401

    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await db_engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine, _create_tables):
    """
    Yield a real AsyncSession that rolls back after each test
    so tests stay isolated without needing to truncate tables.
    """
    async with db_engine.connect() as conn:
        # Begin a transaction that we will roll back
        txn = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        yield session
        await session.close()
        await txn.rollback()


# ── Real Redis client ──────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def real_redis():
    """Provide a real Redis client and flush the test DB after each test."""
    if not _REDIS_UP:
        pytest.skip("Redis not available")
    import redis.asyncio as aioredis

    r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    yield r
    await r.flushdb()
    await r.aclose()


# ── ASGI test client wired to real deps ────────────────────────────────────

@pytest_asyncio.fixture
async def integration_client(db_session, real_redis):
    """
    httpx AsyncClient pointing at the real FastAPI app but with
    get_db / get_redis overridden to use the test session / redis.
    """
    from app.database import get_db
    from app.redis_client import get_redis
    from app.main import app

    async def _override_db():
        yield db_session

    async def _override_redis():
        return real_redis

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_redis] = _override_redis

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
