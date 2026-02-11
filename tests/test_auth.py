"""Tests for authentication endpoints."""

import pytest


@pytest.mark.asyncio
async def test_health_check(client):
    """Health check endpoint returns 200 with service info."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "TradeFlow" in data["service"]


@pytest.mark.asyncio
async def test_register_returns_501_placeholder(client, sample_trader):
    """Registration endpoint returns 501 until implemented."""
    response = await client.post("/api/v1/auth/register", json=sample_trader)
    assert response.status_code == 501


@pytest.mark.asyncio
async def test_otp_request_returns_501_placeholder(client):
    """OTP request endpoint returns 501 until implemented."""
    response = await client.post(
        "/api/v1/auth/otp/request", json={"phone": "+2348012345678"}
    )
    assert response.status_code == 501


@pytest.mark.asyncio
async def test_otp_verify_returns_501_placeholder(client):
    """OTP verify endpoint returns 501 until implemented."""
    response = await client.post(
        "/api/v1/auth/otp/verify",
        json={"phone": "+2348012345678", "otp": "123456"},
    )
    assert response.status_code == 501
