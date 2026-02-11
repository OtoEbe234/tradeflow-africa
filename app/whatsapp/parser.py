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


def parse_amount(text: str) -> Decimal | None:
    """
    Parse a human-friendly amount string into a Decimal value.

    Returns None if the input cannot be parsed.
    """
    cleaned = text.strip().replace(" ", "")
    match = AMOUNT_PATTERN.match(cleaned)
    if not match:
        return None

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
