"""Tests for FX rate engine — rate fetching, caching, fees, circuit breaker."""

import json
from decimal import Decimal, ROUND_HALF_UP
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import auth_service
from app.services.rate_service import (
    CIRCUIT_BREAKER_KEY,
    MIN_FEE_NGN,
    MOCK_CNY_PER_USD,
    MOCK_NGN_PER_USD,
    QUOTE_KEY_PREFIX,
    RATE_CACHE_KEY,
    CircuitBreakerOpenError,
    MockRateProvider,
    RateService,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def rate_redis(mock_redis):
    """Extend mock_redis with sorted set methods needed by RateService."""
    mock_redis.zadd = AsyncMock()
    mock_redis.zremrangebyscore = AsyncMock()
    mock_redis.zrange = AsyncMock(return_value=[])
    return mock_redis


# ---------------------------------------------------------------------------
# MockRateProvider unit tests
# ---------------------------------------------------------------------------


class TestMockRateProvider:

    @pytest.mark.asyncio
    async def test_returns_deterministic_rates(self):
        """Mock provider returns fixed NGN and CNY rates."""
        provider = MockRateProvider()
        rates = await provider.fetch_rates()
        assert rates["NGN"] == MOCK_NGN_PER_USD
        assert rates["CNY"] == MOCK_CNY_PER_USD


# ---------------------------------------------------------------------------
# Fee tier calculation
# ---------------------------------------------------------------------------


class TestFeeTiers:

    def test_standard_tier_zero_volume(self):
        """$0 volume gets standard tier (2.00%)."""
        name, pct = RateService.get_fee_tier(Decimal("0"))
        assert name == "standard"
        assert pct == Decimal("2.00")

    def test_standard_tier_under_50k(self):
        """$49,999 volume gets standard tier."""
        name, pct = RateService.get_fee_tier(Decimal("49999"))
        assert name == "standard"
        assert pct == Decimal("2.00")

    def test_silver_tier_at_50k(self):
        """$50,000 volume gets silver tier (1.50%)."""
        name, pct = RateService.get_fee_tier(Decimal("50000"))
        assert name == "silver"
        assert pct == Decimal("1.50")

    def test_silver_tier_under_200k(self):
        """$199,999 volume stays in silver tier."""
        name, pct = RateService.get_fee_tier(Decimal("199999"))
        assert name == "silver"
        assert pct == Decimal("1.50")

    def test_gold_tier_at_200k(self):
        """$200,000 volume gets gold tier (1.00%)."""
        name, pct = RateService.get_fee_tier(Decimal("200000"))
        assert name == "gold"
        assert pct == Decimal("1.00")

    def test_gold_tier_under_500k(self):
        """$499,999 volume stays in gold tier."""
        name, pct = RateService.get_fee_tier(Decimal("499999"))
        assert name == "gold"
        assert pct == Decimal("1.00")

    def test_platinum_tier_at_500k(self):
        """$500,000 volume gets platinum tier (0.75%)."""
        name, pct = RateService.get_fee_tier(Decimal("500000"))
        assert name == "platinum"
        assert pct == Decimal("0.75")

    def test_platinum_tier_high_volume(self):
        """$2M volume stays in platinum tier."""
        name, pct = RateService.get_fee_tier(Decimal("2000000"))
        assert name == "platinum"
        assert pct == Decimal("0.75")


# ---------------------------------------------------------------------------
# Rate fetching and caching
# ---------------------------------------------------------------------------


class TestRateFetching:

    @pytest.mark.asyncio
    async def test_fetch_rates_from_provider(self, rate_redis):
        """When cache is empty, fetch from provider and cache result."""
        svc = RateService(rate_redis)
        rates = await svc.get_rates()

        assert Decimal(rates["ngn_per_usd"]) == MOCK_NGN_PER_USD
        assert Decimal(rates["cny_per_usd"]) == MOCK_CNY_PER_USD

        # Cross rate = 1550 / 7.25 = 213.7931
        expected_cross = (MOCK_NGN_PER_USD / MOCK_CNY_PER_USD).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        assert Decimal(rates["ngn_per_cny"]) == expected_cross
        assert rates["source"] == "mock"

        # Should have cached the rates in Redis
        rate_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_rates_from_cache(self, rate_redis):
        """When cache exists, return cached data without calling provider."""
        cached = json.dumps({
            "ngn_per_usd": "1550.00",
            "cny_per_usd": "7.25",
            "ngn_per_cny": "213.7931",
            "timestamp": "2025-01-01T00:00:00+00:00",
            "source": "mock",
        })
        rate_redis.get = AsyncMock(return_value=cached)

        svc = RateService(rate_redis)
        rates = await svc.get_rates()

        assert Decimal(rates["ngn_per_usd"]) == Decimal("1550.00")
        assert rates["source"] == "mock"
        # No setex call since we used cache
        rate_redis.setex.assert_not_called()


# ---------------------------------------------------------------------------
# Quote generation
# ---------------------------------------------------------------------------


class TestQuoteGeneration:

    @pytest.mark.asyncio
    async def test_ngn_to_cny_quote(self, rate_redis):
        """Standard NGN->CNY quote with correct fee calculation."""
        svc = RateService(rate_redis)
        quote = await svc.generate_quote("NGN", "CNY", Decimal("50000000"), Decimal("0"))

        assert quote["source_currency"] == "NGN"
        assert quote["target_currency"] == "CNY"
        assert Decimal(quote["source_amount"]) == Decimal("50000000")
        assert quote["fee_tier"] == "standard"
        assert Decimal(quote["fee_percentage"]) == Decimal("2.00")

        # Fee = 50M * 2% = 1,000,000 NGN
        assert Decimal(quote["fee_amount"]) == Decimal("1000000.00")

        # Total cost = source + fee = 51,000,000 NGN
        assert Decimal(quote["total_cost"]) == Decimal("51000000.00")

        # Target = 50M / 213.7931 ≈ 233,882.xx CNY (at mid-market)
        target = Decimal(quote["target_amount"])
        assert target > Decimal("233000")
        assert target < Decimal("234000")

        # TradeFlow rate > mid-market rate (fee on top)
        assert Decimal(quote["tradeflow_rate"]) > Decimal(quote["mid_market_rate"])

        assert quote["quote_id"].startswith("QT-")
        assert "quote_valid_until" in quote

        # Quote should be stored in Redis
        quote_stored = any(
            call.args[0].startswith(QUOTE_KEY_PREFIX)
            for call in rate_redis.setex.call_args_list
        )
        assert quote_stored

    @pytest.mark.asyncio
    async def test_cny_to_ngn_quote(self, rate_redis):
        """CNY->NGN quote works correctly."""
        svc = RateService(rate_redis)
        quote = await svc.generate_quote("CNY", "NGN", Decimal("100000"), Decimal("0"))

        assert quote["source_currency"] == "CNY"
        assert quote["target_currency"] == "NGN"
        assert Decimal(quote["fee_percentage"]) == Decimal("2.00")

        # Target = 100K * 213.7931 ≈ 21,379,310 NGN
        target = Decimal(quote["target_amount"])
        assert target > Decimal("21000000")
        assert target < Decimal("22000000")

        # TradeFlow rate < mid-market rate for CNY->NGN (trader gets less NGN per CNY)
        assert Decimal(quote["tradeflow_rate"]) < Decimal(quote["mid_market_rate"])

    @pytest.mark.asyncio
    async def test_min_fee_applied(self, rate_redis):
        """Small amounts trigger the NGN 5,000 minimum fee."""
        svc = RateService(rate_redis)
        # 100,000 NGN * 2% = 2,000 NGN < min 5,000
        quote = await svc.generate_quote("NGN", "CNY", Decimal("100000"), Decimal("0"))

        assert Decimal(quote["fee_amount"]) == MIN_FEE_NGN

    @pytest.mark.asyncio
    async def test_platinum_tier_quote(self, rate_redis):
        """High-volume trader gets platinum fee tier."""
        svc = RateService(rate_redis)
        quote = await svc.generate_quote(
            "NGN", "CNY", Decimal("50000000"), Decimal("600000"),  # $600K volume
        )

        assert quote["fee_tier"] == "platinum"
        assert Decimal(quote["fee_percentage"]) == Decimal("0.75")
        # Fee = 50M * 0.75% = 375,000 NGN
        assert Decimal(quote["fee_amount"]) == Decimal("375000.00")

    @pytest.mark.asyncio
    async def test_savings_vs_bank(self, rate_redis):
        """Savings = bank fee (5%) - TradeFlow fee."""
        svc = RateService(rate_redis)
        quote = await svc.generate_quote("NGN", "CNY", Decimal("50000000"), Decimal("0"))

        savings = Decimal(quote["savings_vs_bank"])
        # Bank fee = 50M * 5% = 2,500,000
        # TF fee = 50M * 2% = 1,000,000
        # Savings = 1,500,000
        assert savings == Decimal("1500000.00")

    @pytest.mark.asyncio
    async def test_unsupported_pair_raises(self, rate_redis):
        """Unsupported currency pair raises ValueError."""
        svc = RateService(rate_redis)
        with pytest.raises(ValueError, match="Unsupported"):
            await svc.generate_quote("USD", "EUR", Decimal("1000"), Decimal("0"))


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:

    @pytest.mark.asyncio
    async def test_not_triggered_small_movement(self, rate_redis):
        """Normal rate movement (<3%) does not trip the circuit breaker."""
        # 2 rates within 3%: (215 - 213.79) / 213.79 = 0.57%
        rate_redis.zrange = AsyncMock(return_value=["213.79", "215.00"])

        svc = RateService(rate_redis)
        await svc._check_circuit_breaker()

        # Should NOT have set the circuit breaker key
        for call in rate_redis.setex.call_args_list:
            assert not call.args[0].startswith("circuit_breaker")

    @pytest.mark.asyncio
    async def test_triggered_large_movement(self, rate_redis):
        """Rate movement >3% trips the circuit breaker."""
        # (210 - 200) / 200 = 5% > 3%
        rate_redis.zrange = AsyncMock(return_value=["200.00", "210.00"])

        svc = RateService(rate_redis)
        await svc._check_circuit_breaker()

        # Should have set the circuit breaker key
        breaker_calls = [
            call for call in rate_redis.setex.call_args_list
            if call.args[0] == CIRCUIT_BREAKER_KEY
        ]
        assert len(breaker_calls) == 1

    @pytest.mark.asyncio
    async def test_blocks_quotes_when_open(self, rate_redis):
        """When circuit breaker is open, quote generation raises error."""
        rate_redis.get = AsyncMock(return_value='{"reason":"test"}')

        svc = RateService(rate_redis)
        with pytest.raises(CircuitBreakerOpenError):
            await svc.generate_quote("NGN", "CNY", Decimal("1000000"), Decimal("0"))

    @pytest.mark.asyncio
    async def test_closed_when_no_key(self, rate_redis):
        """When circuit breaker key is absent, breaker is closed."""
        rate_redis.get = AsyncMock(return_value=None)

        svc = RateService(rate_redis)
        assert await svc.is_circuit_breaker_open() is False

    @pytest.mark.asyncio
    async def test_single_rate_no_trigger(self, rate_redis):
        """With only 1 historical rate, circuit breaker cannot trigger."""
        rate_redis.zrange = AsyncMock(return_value=["213.79"])

        svc = RateService(rate_redis)
        await svc._check_circuit_breaker()

        # No breaker set
        for call in rate_redis.setex.call_args_list:
            assert not call.args[0].startswith("circuit_breaker")


# ---------------------------------------------------------------------------
# Endpoint tests: GET /api/v1/rates/current (public)
# ---------------------------------------------------------------------------


class TestCurrentRatesEndpoint:

    @pytest.mark.asyncio
    async def test_returns_200(self, client, rate_redis):
        """Public rates endpoint returns current rates."""
        response = await client.get("/api/v1/rates/current")
        assert response.status_code == 200
        data = response.json()
        assert "ngn_per_usd" in data
        assert "cny_per_usd" in data
        assert "ngn_per_cny" in data
        assert data["source"] == "mock"

    @pytest.mark.asyncio
    async def test_circuit_breaker_503(self, client, rate_redis):
        """Returns 503 when circuit breaker is open."""
        async def _get_by_key(key):
            if key == CIRCUIT_BREAKER_KEY:
                return '{"reason":"volatile"}'
            return None

        rate_redis.get = _get_by_key

        response = await client.get("/api/v1/rates/current")
        assert response.status_code == 503
        assert "market movement" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Endpoint tests: GET /api/v1/rates/quote (authenticated)
# ---------------------------------------------------------------------------


class TestQuoteEndpoint:

    @pytest.mark.asyncio
    async def test_returns_200(self, client, mock_db, rate_redis, make_trader):
        """Authenticated trader gets a valid quote."""
        trader = make_trader()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=trader)
        mock_db.execute.return_value = mock_result

        access = auth_service.create_access_token(str(trader.id), trader.phone)

        response = await client.get(
            "/api/v1/rates/quote",
            params={"source": "NGN", "target": "CNY", "amount": "50000000"},
            headers={"Authorization": f"Bearer {access}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["source_currency"] == "NGN"
        assert data["target_currency"] == "CNY"
        assert data["fee_tier"] == "standard"
        assert data["quote_id"].startswith("QT-")
        assert float(data["fee_amount"]) > 0
        assert float(data["total_cost"]) > float(data["source_amount"])
        assert float(data["savings_vs_bank"]) > 0

    @pytest.mark.asyncio
    async def test_unsupported_pair_400(self, client, mock_db, rate_redis, make_trader):
        """Unsupported currency pair returns 400."""
        trader = make_trader()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=trader)
        mock_db.execute.return_value = mock_result

        access = auth_service.create_access_token(str(trader.id), trader.phone)

        response = await client.get(
            "/api/v1/rates/quote",
            params={"source": "USD", "target": "EUR", "amount": "1000"},
            headers={"Authorization": f"Bearer {access}"},
        )
        assert response.status_code == 400
        assert "supported" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_unauthenticated_422(self, client, rate_redis):
        """Quote without auth header returns 422 (missing header)."""
        response = await client.get(
            "/api/v1/rates/quote",
            params={"source": "NGN", "target": "CNY", "amount": "50000000"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_circuit_breaker_503(self, client, mock_db, rate_redis, make_trader):
        """Returns 503 when circuit breaker is open."""
        trader = make_trader()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=trader)
        mock_db.execute.return_value = mock_result

        access = auth_service.create_access_token(str(trader.id), trader.phone)

        # Circuit breaker open for generate_quote, but not for initial get_rates
        async def _get_by_key(key):
            if key == CIRCUIT_BREAKER_KEY:
                return '{"reason":"volatile"}'
            return None

        rate_redis.get = _get_by_key

        response = await client.get(
            "/api/v1/rates/quote",
            params={"source": "NGN", "target": "CNY", "amount": "50000000"},
            headers={"Authorization": f"Bearer {access}"},
        )
        assert response.status_code == 503
