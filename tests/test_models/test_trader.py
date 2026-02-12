"""Tests for the Trader model — creation, encryption, limits, and validation."""

import re
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet

from app.models.trader import (
    Trader,
    TraderStatus,
    TIER_LIMITS,
    configure_fernet,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fernet_key():
    """Generate and configure a fresh Fernet key for each test."""
    key = Fernet.generate_key()
    configure_fernet(key)
    return key


@pytest.fixture
def trader(fernet_key):
    """Create a basic Trader instance using the ORM constructor."""
    return Trader(
        phone="+2348012345678",
        full_name="Adebayo Ogunlesi",
        business_name="Lagos Trading Co.",
    )


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


class TestTraderCreation:
    def test_create_with_valid_data(self, trader):
        """Trader creation sets all expected defaults."""
        assert trader.phone == "+2348012345678"
        assert trader.full_name == "Adebayo Ogunlesi"
        assert trader.business_name == "Lagos Trading Co."
        assert trader.kyc_tier == 1
        assert trader.monthly_limit == TIER_LIMITS[1]
        assert trader.monthly_used == Decimal("0")
        assert trader.status == TraderStatus.PENDING
        assert trader.bvn is None
        assert trader.nin is None
        assert trader.pin_hash is None
        assert trader.referred_by is None

    def test_tradeflow_id_auto_generated(self, trader):
        """tradeflow_id is auto-generated in TF-XXXXX format."""
        assert trader.tradeflow_id is not None
        assert re.match(r"^TF-[A-Z0-9]{5}$", trader.tradeflow_id)

    def test_tradeflow_id_generation_format(self):
        """generate_tradeflow_id() returns the correct format."""
        for _ in range(20):
            tf_id = Trader.generate_tradeflow_id()
            assert re.match(r"^TF-[A-Z0-9]{5}$", tf_id)

    def test_tradeflow_id_uniqueness(self):
        """Generated IDs are (very likely) unique across calls."""
        ids = {Trader.generate_tradeflow_id() for _ in range(100)}
        # With 36^5 ≈ 60M combinations, 100 should all be distinct
        assert len(ids) == 100

    def test_create_with_optional_business_name_none(self, fernet_key):
        """business_name is optional and defaults to None."""
        t = Trader(phone="+2340000000000", full_name="Test User")
        assert t.business_name is None

    def test_repr_does_not_expose_sensitive_fields(self, trader):
        """__repr__ should NOT include phone, bvn, nin, or pin_hash."""
        trader.set_bvn("12345678901")
        trader.set_pin("9999")
        r = repr(trader)
        assert "12345678901" not in r
        assert "9999" not in r
        assert trader.phone not in r
        # Should contain tradeflow_id and name
        assert "TF-" in r
        assert "Adebayo" in r


# ---------------------------------------------------------------------------
# BVN/NIN Encryption
# ---------------------------------------------------------------------------


class TestEncryption:
    def test_bvn_encrypt_decrypt_roundtrip(self, trader):
        """Encrypting then decrypting a BVN returns the original value."""
        trader.set_bvn("12345678901")
        assert trader.bvn is not None
        # The stored value should be ciphertext, not plaintext
        assert trader.bvn != "12345678901"
        # Decrypting should return original
        assert trader.get_bvn() == "12345678901"

    def test_nin_encrypt_decrypt_roundtrip(self, trader):
        """Encrypting then decrypting a NIN returns the original value."""
        trader.set_nin("98765432109")
        assert trader.nin != "98765432109"
        assert trader.get_nin() == "98765432109"

    def test_get_bvn_returns_none_when_not_set(self, trader):
        """get_bvn() returns None if bvn is not set."""
        assert trader.get_bvn() is None

    def test_get_nin_returns_none_when_not_set(self, trader):
        """get_nin() returns None if nin is not set."""
        assert trader.get_nin() is None

    def test_decrypt_with_wrong_key_raises(self, trader):
        """Decryption with a different key raises ValueError."""
        trader.set_bvn("12345678901")
        # Swap to a different key
        configure_fernet(Fernet.generate_key())
        with pytest.raises(ValueError, match="Failed to decrypt"):
            trader.get_bvn()

    def test_encrypt_value_static_method(self, fernet_key):
        """encrypt_value/decrypt_value work as static methods."""
        cipher = Trader.encrypt_value("hello")
        assert cipher != "hello"
        assert Trader.decrypt_value(cipher) == "hello"


# ---------------------------------------------------------------------------
# PIN Hashing
# ---------------------------------------------------------------------------


class TestPinHashing:
    def test_set_and_verify_pin(self, trader):
        """Setting a PIN and verifying it succeeds."""
        trader.set_pin("1234")
        assert trader.pin_hash is not None
        assert trader.pin_hash != "1234"
        assert trader.verify_pin("1234") is True

    def test_wrong_pin_fails(self, trader):
        """Verifying an incorrect PIN returns False."""
        trader.set_pin("1234")
        assert trader.verify_pin("0000") is False

    def test_verify_pin_when_no_hash_set(self, trader):
        """verify_pin returns False when pin_hash is None."""
        assert trader.verify_pin("1234") is False


# ---------------------------------------------------------------------------
# Monthly Limit
# ---------------------------------------------------------------------------


class TestMonthlyLimit:
    def test_tier_1_limit(self, fernet_key):
        """Tier 1 monthly limit is $5,000."""
        t = Trader(phone="+2340000000001", full_name="T1", kyc_tier=1)
        assert t.monthly_limit == Decimal("5000")

    def test_tier_2_limit(self, fernet_key):
        """Tier 2 monthly limit is $50,000."""
        t = Trader(
            phone="+2340000000002", full_name="T2",
            kyc_tier=2, monthly_limit=TIER_LIMITS[2],
        )
        assert t.monthly_limit == Decimal("50000")

    def test_tier_3_limit(self, fernet_key):
        """Tier 3 monthly limit is $500,000."""
        t = Trader(
            phone="+2340000000003", full_name="T3",
            kyc_tier=3, monthly_limit=TIER_LIMITS[3],
        )
        assert t.monthly_limit == Decimal("500000")

    def test_exceeds_monthly_limit_false(self, trader):
        """Amount within limit returns False."""
        trader.monthly_used = Decimal("0")
        trader.monthly_limit = Decimal("5000")
        assert trader.exceeds_monthly_limit(Decimal("4999")) is False

    def test_exceeds_monthly_limit_exact(self, trader):
        """Amount exactly at limit returns False (not exceeded)."""
        trader.monthly_used = Decimal("0")
        trader.monthly_limit = Decimal("5000")
        assert trader.exceeds_monthly_limit(Decimal("5000")) is False

    def test_exceeds_monthly_limit_true(self, trader):
        """Amount exceeding limit returns True."""
        trader.monthly_used = Decimal("0")
        trader.monthly_limit = Decimal("5000")
        assert trader.exceeds_monthly_limit(Decimal("5001")) is True

    def test_exceeds_with_partial_usage(self, trader):
        """Partial usage + new amount that crosses limit returns True."""
        trader.monthly_used = Decimal("4500")
        trader.monthly_limit = Decimal("5000")
        assert trader.exceeds_monthly_limit(Decimal("501")) is True
        assert trader.exceeds_monthly_limit(Decimal("500")) is False

    def test_sync_monthly_limit(self, trader):
        """sync_monthly_limit recalculates from kyc_tier."""
        trader.kyc_tier = 2
        trader.sync_monthly_limit()
        assert trader.monthly_limit == Decimal("50000")

        trader.kyc_tier = 3
        trader.sync_monthly_limit()
        assert trader.monthly_limit == Decimal("500000")

        trader.kyc_tier = 1
        trader.sync_monthly_limit()
        assert trader.monthly_limit == Decimal("5000")


# ---------------------------------------------------------------------------
# Status Enum
# ---------------------------------------------------------------------------


class TestTraderStatus:
    def test_default_status_is_pending(self, trader):
        """New traders start in 'pending' status."""
        assert trader.status == TraderStatus.PENDING

    def test_status_values(self):
        """All expected status values exist."""
        assert TraderStatus.PENDING.value == "pending"
        assert TraderStatus.ACTIVE.value == "active"
        assert TraderStatus.SUSPENDED.value == "suspended"
        assert TraderStatus.BLOCKED.value == "blocked"


# ---------------------------------------------------------------------------
# Validation / Edge Cases
# ---------------------------------------------------------------------------


class TestValidation:
    def test_tier_limits_dict_has_all_tiers(self):
        """TIER_LIMITS contains entries for tiers 1-3."""
        assert set(TIER_LIMITS.keys()) == {1, 2, 3}
        assert all(isinstance(v, Decimal) for v in TIER_LIMITS.values())

    def test_encrypt_empty_string(self, fernet_key):
        """Encrypting an empty string round-trips correctly."""
        cipher = Trader.encrypt_value("")
        assert Trader.decrypt_value(cipher) == ""

    def test_decrypt_invalid_ciphertext_raises(self, fernet_key):
        """Decrypting garbage raises ValueError."""
        with pytest.raises(ValueError, match="Failed to decrypt"):
            Trader.decrypt_value("not-valid-ciphertext")
