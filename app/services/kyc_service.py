"""
KYC verification service — BVN identity checks via VerifyMe API.

Architecture:
  - BVNProvider (protocol) defines the interface
  - MockBVNProvider returns test data for development
  - VerifyMeBVNProvider calls the real VerifyMe API
  - VERIFYME_MOCK=true (default) selects the mock provider

Switch to production by setting VERIFYME_MOCK=false and providing
VERIFYME_API_KEY in the environment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# BVN verification result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BVNResult:
    """Structured result from a BVN verification call."""
    verified: bool
    full_name: str
    date_of_birth: str
    phone_number: str
    phone_match: bool


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


class BVNProvider(Protocol):
    async def verify_bvn(self, bvn: str, phone: str) -> BVNResult: ...


# ---------------------------------------------------------------------------
# Mock provider (development / testing)
# ---------------------------------------------------------------------------

# Test BVNs that map to predictable responses
_MOCK_BVN_DB: dict[str, dict] = {
    "12345678901": {
        "full_name": "Adebayo Ogunlesi",
        "date_of_birth": "1985-03-15",
        "phone_number": "+2348012345678",
    },
    "12345678902": {
        "full_name": "Chioma Nwosu",
        "date_of_birth": "1990-07-22",
        "phone_number": "+2348098765432",
    },
    "12345678903": {
        "full_name": "Ibrahim Musa",
        "date_of_birth": "1988-11-01",
        "phone_number": "+2348021234505",
    },
    "99999999999": {
        "full_name": "Test Mismatch",
        "date_of_birth": "2000-01-01",
        "phone_number": "+2340000000000",  # never matches
    },
}


class MockBVNProvider:
    """Returns deterministic test data. BVNs not in the mock DB are rejected."""

    async def verify_bvn(self, bvn: str, phone: str) -> BVNResult:
        record = _MOCK_BVN_DB.get(bvn)
        if record is None:
            return BVNResult(
                verified=False,
                full_name="",
                date_of_birth="",
                phone_number="",
                phone_match=False,
            )
        return BVNResult(
            verified=True,
            full_name=record["full_name"],
            date_of_birth=record["date_of_birth"],
            phone_number=record["phone_number"],
            phone_match=(record["phone_number"] == phone),
        )


# ---------------------------------------------------------------------------
# Real VerifyMe provider
# ---------------------------------------------------------------------------


class VerifyMeBVNProvider:
    """Calls the VerifyMe BVN verification endpoint."""

    def __init__(self, base_url: str, api_key: str):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    async def verify_bvn(self, bvn: str, phone: str) -> BVNResult:
        url = f"{self._base_url}/verifications/identities/bvn/{bvn}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, headers=headers)
                resp.raise_for_status()
                data = resp.json().get("data", {})
        except httpx.HTTPStatusError as exc:
            logger.error("VerifyMe BVN request failed: %s", exc.response.status_code)
            return BVNResult(
                verified=False, full_name="", date_of_birth="",
                phone_number="", phone_match=False,
            )
        except httpx.RequestError as exc:
            logger.error("VerifyMe BVN request error: %s", exc)
            return BVNResult(
                verified=False, full_name="", date_of_birth="",
                phone_number="", phone_match=False,
            )

        first = data.get("firstname", "")
        last = data.get("lastname", "")
        middle = data.get("middlename", "")
        parts = [p for p in (first, middle, last) if p]
        full_name = " ".join(parts)

        bvn_phone = data.get("phone", "")
        dob = data.get("birthdate", "")

        return BVNResult(
            verified=True,
            full_name=full_name,
            date_of_birth=dob,
            phone_number=bvn_phone,
            phone_match=(bvn_phone == phone),
        )


# ---------------------------------------------------------------------------
# Factory — selects provider based on config
# ---------------------------------------------------------------------------

_provider: BVNProvider | None = None


def get_bvn_provider() -> BVNProvider:
    """Return the configured BVN provider (cached after first call)."""
    global _provider
    if _provider is not None:
        return _provider

    if settings.VERIFYME_MOCK:
        logger.info("Using MockBVNProvider for BVN verification")
        _provider = MockBVNProvider()
    else:
        logger.info("Using VerifyMeBVNProvider (live API)")
        _provider = VerifyMeBVNProvider(
            base_url=settings.VERIFYME_BASE_URL,
            api_key=settings.VERIFYME_API_KEY,
        )
    return _provider


def set_bvn_provider(provider: BVNProvider) -> None:
    """Override the BVN provider (used in tests)."""
    global _provider
    _provider = provider
