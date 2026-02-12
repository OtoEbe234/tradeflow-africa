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


class TestWordFormParser:
    """Tests for English word-form number parsing."""

    def test_fifty_million(self):
        assert parse_amount("fifty million") == Decimal("50000000")

    def test_two_hundred_thousand(self):
        assert parse_amount("two hundred thousand") == Decimal("200000")

    def test_five_billion(self):
        assert parse_amount("five billion") == Decimal("5000000000")

    def test_one_million(self):
        assert parse_amount("one million") == Decimal("1000000")

    def test_ten_thousand(self):
        assert parse_amount("ten thousand") == Decimal("10000")

    def test_one_hundred_fifty_million(self):
        assert parse_amount("one hundred fifty million") == Decimal("150000000")

    def test_twenty_five_thousand(self):
        assert parse_amount("twenty five thousand") == Decimal("25000")

    def test_million_alone(self):
        """Single scale word: 'million' -> 1,000,000."""
        assert parse_amount("million") == Decimal("1000000")

    def test_case_insensitive(self):
        assert parse_amount("Fifty Million") == Decimal("50000000")

    def test_word_form_invalid(self):
        """Random text should not match word-form parser."""
        assert parse_amount("please send money") is None

    def test_word_form_zero_word(self):
        """'zero' should return None (amount must be > 0)."""
        assert parse_amount("zero") is None
