"""
Amount parser — understands Nigerian currency shorthand.

Parses various formats traders commonly use when stating amounts
in WhatsApp conversations:

  - "50m" or "50M"       -> 50,000,000
  - "N50,000,000"        -> 50,000,000
  - "₦50,000,000"        -> 50,000,000
  - "5000000"            -> 5,000,000
  - "50k"                -> 50,000
  - "1.5m"               -> 1,500,000
  - "2.5b"               -> 2,500,000,000
  - "fifty million"      -> 50,000,000
"""

import re
from decimal import Decimal, InvalidOperation


MULTIPLIERS = {
    "k": Decimal("1_000"),
    "m": Decimal("1_000_000"),
    "b": Decimal("1_000_000_000"),
}

# Matches patterns like: N50,000,000 | ₦50m | 50000000 | 1.5M | 50k
AMOUNT_PATTERN = re.compile(
    r"^[N₦]?\s*([0-9]{1,3}(?:,?[0-9]{3})*(?:\.[0-9]+)?)\s*([kmb])?$",
    re.IGNORECASE,
)

# ── Word-form number support ─────────────────────────────────────────────

_WORD_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}

_WORD_SCALES = {
    "hundred": Decimal("100"),
    "thousand": Decimal("1_000"),
    "million": Decimal("1_000_000"),
    "billion": Decimal("1_000_000_000"),
}


def _parse_word_number(text: str) -> Decimal | None:
    """
    Parse an English word-form number like 'fifty million' into a Decimal.

    Supports patterns such as:
      - "fifty million"
      - "two hundred thousand"
      - "one hundred fifty million"
      - "five billion"
    """
    words = text.strip().lower().split()
    if not words:
        return None

    # Quick check: at least one word must be a known number word
    known = set(_WORD_UNITS) | set(_WORD_SCALES)
    if not any(w in known for w in words):
        return None

    # Filter out connectors like "and"
    words = [w for w in words if w != "and"]

    current = Decimal("0")
    result = Decimal("0")

    for word in words:
        if word in _WORD_UNITS:
            current += Decimal(str(_WORD_UNITS[word]))
        elif word == "hundred":
            if current == 0:
                current = Decimal("1")
            current *= _WORD_SCALES["hundred"]
        elif word in ("thousand", "million", "billion"):
            if current == 0:
                current = Decimal("1")
            current *= _WORD_SCALES[word]
            result += current
            current = Decimal("0")
        else:
            return None  # Unknown word

    result += current

    if result <= 0:
        return None

    return result


def parse_amount(text: str) -> Decimal | None:
    """
    Parse a human-friendly amount string into a Decimal value.

    Returns None if the input cannot be parsed.
    """
    cleaned = text.strip().replace(" ", "")
    match = AMOUNT_PATTERN.match(cleaned)
    if match:
        number_str = match.group(1).replace(",", "")
        suffix = match.group(2)

        try:
            value = Decimal(number_str)
        except InvalidOperation:
            return None

        if suffix:
            multiplier = MULTIPLIERS.get(suffix.lower())
            if multiplier:
                value *= multiplier

        if value <= 0:
            return None

        return value

    # Fallback: try word-form parsing
    return _parse_word_number(text)
