"""Tests for the Transaction model — status transitions, reference generation, encryption."""

import re
import uuid
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet

from app.models.trader import configure_fernet
from app.models.transaction import (
    Transaction,
    TransactionDirection,
    TransactionStatus,
    SettlementMethod,
    VALID_TRANSITIONS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _setup_fernet():
    """Ensure Fernet is configured for encryption tests."""
    configure_fernet(Fernet.generate_key())


@pytest.fixture
def txn():
    """Create a minimal Transaction instance."""
    return Transaction(
        trader_id=uuid.uuid4(),
        direction=TransactionDirection.NGN_TO_CNY,
        source_amount=Decimal("5000000"),
    )


# ---------------------------------------------------------------------------
# Creation & Reference
# ---------------------------------------------------------------------------


class TestTransactionCreation:
    def test_create_with_valid_data(self, txn):
        """Transaction creation populates required fields and defaults."""
        assert txn.id is not None
        assert txn.reference is not None
        assert txn.source_amount == Decimal("5000000")
        assert txn.direction == TransactionDirection.NGN_TO_CNY
        assert txn.status == TransactionStatus.INITIATED
        assert txn.fee_amount == Decimal("0")
        assert txn.fee_percentage == Decimal("0")
        assert txn.match_id is None
        assert txn.settlement_method is None
        assert txn.funded_at is None
        assert txn.matched_at is None
        assert txn.settled_at is None
        assert txn.created_at is not None

    def test_reference_format(self, txn):
        """Auto-generated reference matches TXN-XXXXXXXX pattern."""
        assert re.match(r"^TXN-[A-Z0-9]{8}$", txn.reference)

    def test_generate_reference_format(self):
        """generate_reference() produces correct format."""
        for _ in range(20):
            ref = Transaction.generate_reference()
            assert re.match(r"^TXN-[A-Z0-9]{8}$", ref)

    def test_reference_uniqueness(self):
        """Generated references are (very likely) unique."""
        refs = {Transaction.generate_reference() for _ in range(200)}
        assert len(refs) == 200

    def test_repr_contains_reference(self, txn):
        """__repr__ includes the reference and amount."""
        r = repr(txn)
        assert txn.reference in r
        assert "5000000" in r


# ---------------------------------------------------------------------------
# Status Transitions
# ---------------------------------------------------------------------------


class TestStatusTransitions:
    """Every valid and invalid transition path is tested."""

    # -- Happy-path transitions (the full lifecycle) ----------------------

    def test_initiated_to_funded(self, txn):
        txn.transition_to(TransactionStatus.FUNDED)
        assert txn.status == TransactionStatus.FUNDED
        assert txn.funded_at is not None

    def test_funded_to_matching(self, txn):
        txn.transition_to(TransactionStatus.FUNDED)
        txn.transition_to(TransactionStatus.MATCHING)
        assert txn.status == TransactionStatus.MATCHING

    def test_matching_to_matched(self, txn):
        txn.transition_to(TransactionStatus.FUNDED)
        txn.transition_to(TransactionStatus.MATCHING)
        txn.transition_to(TransactionStatus.MATCHED)
        assert txn.status == TransactionStatus.MATCHED
        assert txn.matched_at is not None

    def test_matching_to_partial_matched(self, txn):
        txn.transition_to(TransactionStatus.FUNDED)
        txn.transition_to(TransactionStatus.MATCHING)
        txn.transition_to(TransactionStatus.PARTIAL_MATCHED)
        assert txn.status == TransactionStatus.PARTIAL_MATCHED
        assert txn.matched_at is not None

    def test_matched_to_pending_settlement(self, txn):
        txn.transition_to(TransactionStatus.FUNDED)
        txn.transition_to(TransactionStatus.MATCHING)
        txn.transition_to(TransactionStatus.MATCHED)
        txn.transition_to(TransactionStatus.PENDING_SETTLEMENT)
        assert txn.status == TransactionStatus.PENDING_SETTLEMENT

    def test_pending_settlement_to_settling(self, txn):
        txn.transition_to(TransactionStatus.FUNDED)
        txn.transition_to(TransactionStatus.MATCHING)
        txn.transition_to(TransactionStatus.MATCHED)
        txn.transition_to(TransactionStatus.PENDING_SETTLEMENT)
        txn.transition_to(TransactionStatus.SETTLING)
        assert txn.status == TransactionStatus.SETTLING

    def test_settling_to_completed(self, txn):
        txn.transition_to(TransactionStatus.FUNDED)
        txn.transition_to(TransactionStatus.MATCHING)
        txn.transition_to(TransactionStatus.MATCHED)
        txn.transition_to(TransactionStatus.PENDING_SETTLEMENT)
        txn.transition_to(TransactionStatus.SETTLING)
        txn.transition_to(TransactionStatus.COMPLETED)
        assert txn.status == TransactionStatus.COMPLETED
        assert txn.settled_at is not None

    def test_full_happy_path(self, txn):
        """Walk the entire happy path: INITIATED -> COMPLETED."""
        path = [
            TransactionStatus.FUNDED,
            TransactionStatus.MATCHING,
            TransactionStatus.MATCHED,
            TransactionStatus.PENDING_SETTLEMENT,
            TransactionStatus.SETTLING,
            TransactionStatus.COMPLETED,
        ]
        for s in path:
            txn.transition_to(s)
        assert txn.status == TransactionStatus.COMPLETED

    # -- Cancellation / expiration paths ----------------------------------

    def test_initiated_to_cancelled(self, txn):
        txn.transition_to(TransactionStatus.CANCELLED)
        assert txn.status == TransactionStatus.CANCELLED

    def test_initiated_to_expired(self, txn):
        txn.transition_to(TransactionStatus.EXPIRED)
        assert txn.status == TransactionStatus.EXPIRED

    def test_funded_to_cancelled(self, txn):
        txn.transition_to(TransactionStatus.FUNDED)
        txn.transition_to(TransactionStatus.CANCELLED)
        assert txn.status == TransactionStatus.CANCELLED

    def test_funded_to_expired(self, txn):
        txn.transition_to(TransactionStatus.FUNDED)
        txn.transition_to(TransactionStatus.EXPIRED)
        assert txn.status == TransactionStatus.EXPIRED

    def test_matching_to_expired(self, txn):
        txn.transition_to(TransactionStatus.FUNDED)
        txn.transition_to(TransactionStatus.MATCHING)
        txn.transition_to(TransactionStatus.EXPIRED)
        assert txn.status == TransactionStatus.EXPIRED

    # -- Failure / refund paths -------------------------------------------

    def test_settling_to_failed(self, txn):
        txn.transition_to(TransactionStatus.FUNDED)
        txn.transition_to(TransactionStatus.MATCHING)
        txn.transition_to(TransactionStatus.MATCHED)
        txn.transition_to(TransactionStatus.PENDING_SETTLEMENT)
        txn.transition_to(TransactionStatus.SETTLING)
        txn.transition_to(TransactionStatus.FAILED)
        assert txn.status == TransactionStatus.FAILED

    def test_failed_to_refunded(self, txn):
        txn.transition_to(TransactionStatus.FUNDED)
        txn.transition_to(TransactionStatus.MATCHING)
        txn.transition_to(TransactionStatus.MATCHED)
        txn.transition_to(TransactionStatus.PENDING_SETTLEMENT)
        txn.transition_to(TransactionStatus.SETTLING)
        txn.transition_to(TransactionStatus.FAILED)
        txn.transition_to(TransactionStatus.REFUNDED)
        assert txn.status == TransactionStatus.REFUNDED

    def test_expired_to_refunded(self, txn):
        txn.transition_to(TransactionStatus.EXPIRED)
        txn.transition_to(TransactionStatus.REFUNDED)
        assert txn.status == TransactionStatus.REFUNDED

    # -- Partial match re-entry -------------------------------------------

    def test_partial_matched_back_to_matching(self, txn):
        txn.transition_to(TransactionStatus.FUNDED)
        txn.transition_to(TransactionStatus.MATCHING)
        txn.transition_to(TransactionStatus.PARTIAL_MATCHED)
        txn.transition_to(TransactionStatus.MATCHING)
        assert txn.status == TransactionStatus.MATCHING

    # -- Invalid transitions raise ----------------------------------------

    def test_initiated_to_completed_raises(self, txn):
        with pytest.raises(ValueError, match="Invalid transition"):
            txn.transition_to(TransactionStatus.COMPLETED)

    def test_completed_to_anything_raises(self, txn):
        txn.transition_to(TransactionStatus.FUNDED)
        txn.transition_to(TransactionStatus.MATCHING)
        txn.transition_to(TransactionStatus.MATCHED)
        txn.transition_to(TransactionStatus.PENDING_SETTLEMENT)
        txn.transition_to(TransactionStatus.SETTLING)
        txn.transition_to(TransactionStatus.COMPLETED)
        with pytest.raises(ValueError, match="Invalid transition"):
            txn.transition_to(TransactionStatus.FUNDED)

    def test_cancelled_to_anything_raises(self, txn):
        txn.transition_to(TransactionStatus.CANCELLED)
        with pytest.raises(ValueError, match="Invalid transition"):
            txn.transition_to(TransactionStatus.FUNDED)

    def test_refunded_to_anything_raises(self, txn):
        txn.transition_to(TransactionStatus.EXPIRED)
        txn.transition_to(TransactionStatus.REFUNDED)
        with pytest.raises(ValueError, match="Invalid transition"):
            txn.transition_to(TransactionStatus.INITIATED)

    def test_initiated_to_matching_raises(self, txn):
        """Cannot skip FUNDED step."""
        with pytest.raises(ValueError, match="Invalid transition"):
            txn.transition_to(TransactionStatus.MATCHING)

    def test_funded_to_matched_raises(self, txn):
        """Cannot skip MATCHING step."""
        txn.transition_to(TransactionStatus.FUNDED)
        with pytest.raises(ValueError, match="Invalid transition"):
            txn.transition_to(TransactionStatus.MATCHED)

    # -- Static method validation -----------------------------------------

    def test_is_valid_transition_true(self):
        assert Transaction.is_valid_transition(
            TransactionStatus.INITIATED, TransactionStatus.FUNDED
        ) is True

    def test_is_valid_transition_false(self):
        assert Transaction.is_valid_transition(
            TransactionStatus.INITIATED, TransactionStatus.COMPLETED
        ) is False

    # -- Every status has an entry in VALID_TRANSITIONS -------------------

    def test_all_statuses_in_transition_map(self):
        """Every TransactionStatus value has an entry in VALID_TRANSITIONS."""
        for s in TransactionStatus:
            assert s in VALID_TRANSITIONS, f"{s} missing from VALID_TRANSITIONS"


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------


class TestLifecycleTimestamps:
    def test_funded_at_set_on_funded(self, txn):
        assert txn.funded_at is None
        txn.transition_to(TransactionStatus.FUNDED)
        assert txn.funded_at is not None

    def test_matched_at_set_on_matched(self, txn):
        txn.transition_to(TransactionStatus.FUNDED)
        txn.transition_to(TransactionStatus.MATCHING)
        assert txn.matched_at is None
        txn.transition_to(TransactionStatus.MATCHED)
        assert txn.matched_at is not None

    def test_settled_at_set_on_completed(self, txn):
        txn.transition_to(TransactionStatus.FUNDED)
        txn.transition_to(TransactionStatus.MATCHING)
        txn.transition_to(TransactionStatus.MATCHED)
        txn.transition_to(TransactionStatus.PENDING_SETTLEMENT)
        txn.transition_to(TransactionStatus.SETTLING)
        assert txn.settled_at is None
        txn.transition_to(TransactionStatus.COMPLETED)
        assert txn.settled_at is not None


# ---------------------------------------------------------------------------
# Encrypted supplier account
# ---------------------------------------------------------------------------


class TestSupplierAccountEncryption:
    def test_encrypt_decrypt_roundtrip(self, txn):
        txn.set_supplier_account("6222021234567890")
        assert txn.supplier_account != "6222021234567890"
        assert txn.get_supplier_account() == "6222021234567890"

    def test_get_returns_none_when_not_set(self, txn):
        assert txn.get_supplier_account() is None


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestEnums:
    def test_transaction_status_has_12_values(self):
        assert len(TransactionStatus) == 12

    def test_direction_values(self):
        assert TransactionDirection.NGN_TO_CNY.value == "ngn_to_cny"
        assert TransactionDirection.CNY_TO_NGN.value == "cny_to_ngn"

    def test_settlement_method_values(self):
        assert SettlementMethod.MATCHED.value == "matched"
        assert SettlementMethod.PARTIAL_MATCHED.value == "partial_matched"
        assert SettlementMethod.CIPS_SETTLED.value == "cips_settled"
