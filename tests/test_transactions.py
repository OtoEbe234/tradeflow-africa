"""Tests for transaction endpoints."""

import pytest


@pytest.mark.asyncio
async def test_create_transaction_returns_501_placeholder(client, sample_transaction):
    """Transaction creation returns 501 until implemented."""
    response = await client.post("/api/v1/transactions/", json=sample_transaction)
    assert response.status_code == 501


@pytest.mark.asyncio
async def test_list_transactions_returns_501_placeholder(client):
    """Transaction listing returns 501 until implemented."""
    response = await client.get("/api/v1/transactions/")
    assert response.status_code == 501


@pytest.mark.asyncio
async def test_get_transaction_returns_501_placeholder(client):
    """Single transaction lookup returns 501 until implemented."""
    response = await client.get(
        "/api/v1/transactions/00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code == 501
