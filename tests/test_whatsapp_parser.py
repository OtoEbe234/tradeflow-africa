"""Tests for WhatsApp amount parser."""

import pytest
from decimal import Decimal

from app.whatsapp.parser import parse_amount


class TestAmountParser:
    """Tests for Nigerian currency amount parsing."""

    def test_plain_number(self):
        assert parse_amount("5000000") == Decimal("5000000")

    def test_with_commas(self):
        assert parse_amount("5,000,000") == Decimal("5000000")

    def test_naira_prefix_n(self):
        assert parse_amount("N50,000,000") == Decimal("50000000")

    def test_naira_prefix_symbol(self):
        assert parse_amount("₦50,000,000") == Decimal("50000000")

    def test_millions_suffix_lowercase(self):
        assert parse_amount("50m") == Decimal("50000000")

    def test_millions_suffix_uppercase(self):
        assert parse_amount("50M") == Decimal("50000000")

    def test_thousands_suffix(self):
        assert parse_amount("50k") == Decimal("50000")

    def test_billions_suffix(self):
        assert parse_amount("2b") == Decimal("2000000000")

    def test_decimal_with_suffix(self):
        assert parse_amount("1.5m") == Decimal("1500000")

    def test_naira_prefix_with_suffix(self):
        assert parse_amount("N50m") == Decimal("50000000")

    def test_invalid_text(self):
        assert parse_amount("hello") is None

    def test_empty_string(self):
        assert parse_amount("") is None

    def test_zero(self):
        assert parse_amount("0") is None

    def test_negative(self):
        assert parse_amount("-5000") is None
