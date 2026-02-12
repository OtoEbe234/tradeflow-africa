"""Tests for authentication endpoints — registration, OTP verification, login, tokens."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import jwt
import pytest

from app.core.security import hash_pin
from app.models.trader import TraderStatus
from app.services import auth_service


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check(client):
    """Health check endpoint returns 200 with service info."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "TradeFlow" in data["service"]


# ---------------------------------------------------------------------------
# POST /register — phone-only registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_success(client, mock_db, mock_redis, sample_registration):
    """Valid Nigerian phone returns 200 with success and OTP stored in Redis."""
    response = await client.post("/api/v1/auth/register", json=sample_registration)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["message"] == "OTP sent"

    # OTP should have been stored in Redis
    mock_redis.setex.assert_called_once()


@pytest.mark.asyncio
async def test_register_duplicate_phone(client, mock_db, mock_redis, sample_registration, make_trader):
    """Registration with an existing phone returns 409."""
    existing = make_trader(phone=sample_registration["phone"])
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=existing)
    mock_db.execute.return_value = mock_result

    response = await client.post("/api/v1/auth/register", json=sample_registration)
    assert response.status_code == 409
    assert "already registered" in response.json()["detail"]


@pytest.mark.asyncio
async def test_register_invalid_phone_format(client):
    """Non-Nigerian phone format returns 422."""
    response = await client.post(
        "/api/v1/auth/register",
        json={"phone": "not-a-phone"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_register_non_nigerian_phone(client):
    """Valid phone but not +234 prefix returns 422."""
    response = await client.post(
        "/api/v1/auth/register",
        json={"phone": "+8613800138000"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_register_short_nigerian_phone(client):
    """Too-short Nigerian number returns 422."""
    response = await client.post(
        "/api/v1/auth/register",
        json={"phone": "+234801234"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_register_rate_limited(client, mock_db, mock_redis, sample_registration):
    """4th OTP request within an hour returns 429."""
    # incr returns 4 — exceeds the 3-per-hour limit
    mock_redis.incr.return_value = 4

    response = await client.post("/api/v1/auth/register", json=sample_registration)
    assert response.status_code == 429
    assert "Too many" in response.json()["detail"]


# ---------------------------------------------------------------------------
# POST /verify-otp — OTP verification during registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_otp_success(client, mock_redis):
    """Correct OTP returns success with next_step=bvn_verification."""
    # get() is called for lock check (otp_lock:*) and OTP lookup (otp:*)
    async def _get_by_key(key):
        if key.startswith("otp_lock:"):
            return None  # not locked
        return "123456"  # stored OTP

    mock_redis.get = _get_by_key

    response = await client.post(
        "/api/v1/auth/verify-otp",
        json={"phone": "+2348012345678", "otp": "123456"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["next_step"] == "bvn_verification"


@pytest.mark.asyncio
async def test_verify_otp_wrong_code(client, mock_redis):
    """Wrong OTP returns 401 with error detail."""
    async def _get_by_key(key):
        if key.startswith("otp_lock:"):
            return None  # not locked
        return "999999"  # stored OTP (different from submitted "123456")

    mock_redis.get = _get_by_key
    mock_redis.incr.return_value = 1  # first failed attempt

    response = await client.post(
        "/api/v1/auth/verify-otp",
        json={"phone": "+2348012345678", "otp": "123456"},
    )
    assert response.status_code == 401
    assert "Invalid or expired" in response.json()["detail"]


@pytest.mark.asyncio
async def test_verify_otp_expired(client, mock_redis):
    """Expired OTP (no value in Redis) returns 401."""
    async def _get_by_key(key):
        return None  # not locked, and no OTP stored

    mock_redis.get = _get_by_key
    mock_redis.incr.return_value = 1

    response = await client.post(
        "/api/v1/auth/verify-otp",
        json={"phone": "+2348012345678", "otp": "123456"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_verify_otp_locked_after_3_failures(client, mock_redis):
    """3rd failed attempt triggers 30-minute lockout (429)."""
    async def _get_by_key(key):
        if key.startswith("otp_lock:"):
            return None  # not locked yet
        return "999999"  # wrong OTP

    mock_redis.get = _get_by_key
    mock_redis.incr.return_value = 3  # 3rd failed attempt

    response = await client.post(
        "/api/v1/auth/verify-otp",
        json={"phone": "+2348012345678", "otp": "123456"},
    )
    assert response.status_code == 429
    assert "30 minutes" in response.json()["detail"]


@pytest.mark.asyncio
async def test_verify_otp_already_locked(client, mock_redis):
    """If phone is already locked out, return 429 immediately."""
    async def _get_by_key(key):
        if key.startswith("otp_lock:"):
            return "1"  # locked
        return None

    mock_redis.get = _get_by_key

    response = await client.post(
        "/api/v1/auth/verify-otp",
        json={"phone": "+2348012345678", "otp": "123456"},
    )
    assert response.status_code == 429
    assert "30 minutes" in response.json()["detail"]


@pytest.mark.asyncio
async def test_verify_otp_invalid_format(client):
    """Non-6-digit OTP returns 422."""
    response = await client.post(
        "/api/v1/auth/verify-otp",
        json={"phone": "+2348012345678", "otp": "12"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /otp/request — OTP request for login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_otp_request_success(client, mock_db, mock_redis, make_trader):
    """OTP request for existing phone returns 200."""
    trader = make_trader()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=trader)
    mock_db.execute.return_value = mock_result

    response = await client.post(
        "/api/v1/auth/otp/request", json={"phone": "+2348012345678"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "OTP sent"
    assert "expires_in" in data


@pytest.mark.asyncio
async def test_otp_request_unknown_phone(client, mock_db, mock_redis):
    """OTP request for unknown phone returns 404."""
    response = await client.post(
        "/api/v1/auth/otp/request", json={"phone": "+2349999999999"}
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_otp_request_rate_limited(client, mock_db, mock_redis, make_trader):
    """OTP request when rate limit exceeded returns 429."""
    mock_redis.incr.return_value = 4

    response = await client.post(
        "/api/v1/auth/otp/request", json={"phone": "+2348012345678"}
    )
    assert response.status_code == 429
    assert "Too many" in response.json()["detail"]


# ---------------------------------------------------------------------------
# POST /otp/verify — OTP verify for login (returns tokens)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_otp_verify_success(client, mock_db, mock_redis, make_trader):
    """Correct OTP returns 200 with access and refresh tokens."""
    trader = make_trader()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=trader)
    mock_db.execute.return_value = mock_result

    mock_redis.get.return_value = "123456"

    response = await client.post(
        "/api/v1/auth/otp/verify",
        json={"phone": "+2348012345678", "otp": "123456"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] > 0

    mock_redis.delete.assert_called_once()


@pytest.mark.asyncio
async def test_login_otp_verify_wrong(client, mock_db, mock_redis, make_trader):
    """Wrong OTP returns 401."""
    trader = make_trader()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=trader)
    mock_db.execute.return_value = mock_result

    mock_redis.get.return_value = "999999"

    response = await client.post(
        "/api/v1/auth/otp/verify",
        json={"phone": "+2348012345678", "otp": "123456"},
    )
    assert response.status_code == 401
    assert "Invalid or expired" in response.json()["detail"]


@pytest.mark.asyncio
async def test_login_otp_verify_expired(client, mock_db, mock_redis, make_trader):
    """Expired OTP (no value in Redis) returns 401."""
    trader = make_trader()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=trader)
    mock_db.execute.return_value = mock_result

    mock_redis.get.return_value = None

    response = await client.post(
        "/api/v1/auth/otp/verify",
        json={"phone": "+2348012345678", "otp": "123456"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /token/refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_refresh_success(client, mock_db, make_trader):
    """Valid refresh token returns new token pair."""
    trader = make_trader()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=trader)
    mock_db.execute.return_value = mock_result

    refresh = auth_service.create_refresh_token(str(trader.id), trader.phone)

    response = await client.post(
        "/api/v1/auth/token/refresh",
        json={"refresh_token": refresh},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_token_refresh_invalid_token(client, mock_db):
    """Invalid refresh token returns 401."""
    response = await client.post(
        "/api/v1/auth/token/refresh",
        json={"refresh_token": "invalid.token.here"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /login — PIN-based login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_success(client, mock_db, make_trader):
    """Valid phone + PIN returns tokens and trader profile."""
    trader = make_trader()
    trader.pin_hash = hash_pin("5678")

    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=trader)
    mock_db.execute.return_value = mock_result

    response = await client.post(
        "/api/v1/auth/login",
        json={"phone": "+2348012345678", "pin": "5678"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] > 0
    assert data["trader"]["phone"] == "+2348012345678"
    assert data["trader"]["full_name"] == "Adebayo Ogunlesi"


@pytest.mark.asyncio
async def test_login_wrong_pin(client, mock_db, make_trader):
    """Wrong PIN returns 401."""
    trader = make_trader()
    trader.pin_hash = hash_pin("5678")

    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=trader)
    mock_db.execute.return_value = mock_result

    response = await client.post(
        "/api/v1/auth/login",
        json={"phone": "+2348012345678", "pin": "0000"},
    )
    assert response.status_code == 401
    assert "Invalid phone or PIN" in response.json()["detail"]


@pytest.mark.asyncio
async def test_login_unknown_phone(client, mock_db):
    """Login with unknown phone returns 401."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"phone": "+2349999999999", "pin": "5678"},
    )
    assert response.status_code == 401
    assert "Invalid phone or PIN" in response.json()["detail"]


@pytest.mark.asyncio
async def test_login_inactive_account(client, mock_db, make_trader):
    """Login with non-active account returns 401."""
    trader = make_trader(status=TraderStatus.PENDING)
    trader.pin_hash = hash_pin("5678")

    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=trader)
    mock_db.execute.return_value = mock_result

    response = await client.post(
        "/api/v1/auth/login",
        json={"phone": "+2348012345678", "pin": "5678"},
    )
    assert response.status_code == 401
    assert "not active" in response.json()["detail"]


@pytest.mark.asyncio
async def test_login_no_pin_set(client, mock_db, make_trader):
    """Login when PIN not yet set returns 401."""
    trader = make_trader()
    trader.pin_hash = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=trader)
    mock_db.execute.return_value = mock_result

    response = await client.post(
        "/api/v1/auth/login",
        json={"phone": "+2348012345678", "pin": "5678"},
    )
    assert response.status_code == 401
    assert "PIN has not been set" in response.json()["detail"]


# ---------------------------------------------------------------------------
# POST /refresh — access token refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_success(client, mock_db, make_trader):
    """Valid refresh token returns new access token only."""
    trader = make_trader()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=trader)
    mock_db.execute.return_value = mock_result

    refresh = auth_service.create_refresh_token(str(trader.id), trader.phone)

    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] > 0
    # Should NOT contain refresh_token (access-only refresh)
    assert "refresh_token" not in data


@pytest.mark.asyncio
async def test_refresh_invalid_token(client, mock_db):
    """Invalid refresh token returns 401."""
    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": "garbage.token.value"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_refresh_with_access_token_fails(client, mock_db, make_trader):
    """Using an access token as refresh token returns 401."""
    trader = make_trader()
    access = auth_service.create_access_token(str(trader.id), trader.phone)

    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": access},
    )
    assert response.status_code == 401
    assert "Expected refresh token" in response.json()["detail"]


@pytest.mark.asyncio
async def test_refresh_suspended_account(client, mock_db, make_trader):
    """Refresh for suspended trader returns 401."""
    trader = make_trader(status=TraderStatus.SUSPENDED)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=trader)
    mock_db.execute.return_value = mock_result

    refresh = auth_service.create_refresh_token(str(trader.id), trader.phone)

    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Protected endpoints — JWT auth dependency tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_protected_endpoint_no_token(client):
    """GET /traders/me without Authorization header returns 401."""
    response = await client.get("/api/v1/traders/me")
    assert response.status_code == 422  # missing required header


@pytest.mark.asyncio
async def test_protected_endpoint_invalid_token(client):
    """GET /traders/me with garbage token returns 401."""
    response = await client.get(
        "/api/v1/traders/me",
        headers={"Authorization": "Bearer invalid.token.here"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_protected_endpoint_valid_token(client, mock_db, make_trader):
    """GET /traders/me with valid access token returns trader profile."""
    trader = make_trader()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=trader)
    mock_db.execute.return_value = mock_result

    access = auth_service.create_access_token(str(trader.id), trader.phone)

    response = await client.get(
        "/api/v1/traders/me",
        headers={"Authorization": f"Bearer {access}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["phone"] == trader.phone
    assert data["full_name"] == trader.full_name


@pytest.mark.asyncio
async def test_protected_endpoint_expired_token(client, mock_db, test_rsa_keys):
    """GET /traders/me with expired token returns 401."""
    # Manually create an expired token
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "00000000-0000-0000-0000-000000000001",
        "phone": "+2348012345678",
        "type": "access",
        "iat": now - timedelta(hours=2),
        "exp": now - timedelta(hours=1),  # expired 1 hour ago
    }
    expired_token = jwt.encode(
        payload, test_rsa_keys["private_key"], algorithm="RS256"
    )

    response = await client.get(
        "/api/v1/traders/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert response.status_code == 401
    assert "expired" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_protected_endpoint_refresh_token_rejected(client, mock_db, make_trader):
    """GET /traders/me with refresh token (not access) returns 401."""
    trader = make_trader()
    refresh = auth_service.create_refresh_token(str(trader.id), trader.phone)

    response = await client.get(
        "/api/v1/traders/me",
        headers={"Authorization": f"Bearer {refresh}"},
    )
    assert response.status_code == 401
    assert "Expected access token" in response.json()["detail"]


@pytest.mark.asyncio
async def test_protected_endpoint_suspended_trader(client, mock_db, make_trader):
    """GET /traders/me for suspended trader returns 401."""
    trader = make_trader(status=TraderStatus.SUSPENDED)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=trader)
    mock_db.execute.return_value = mock_result

    access = auth_service.create_access_token(str(trader.id), trader.phone)

    response = await client.get(
        "/api/v1/traders/me",
        headers={"Authorization": f"Bearer {access}"},
    )
    assert response.status_code == 401
    assert "deactivated" in response.json()["detail"]
