"""
KYC verification service — BVN and NIN identity checks.

Integrates with VerifyMe (or similar Nigerian identity providers)
to validate Bank Verification Numbers and National Identity Numbers.
"""

import httpx

from app.config import settings


class KYCService:
    """Handles BVN and NIN verification against identity providers."""

    def __init__(self):
        self.bvn_url = settings.BVN_API_URL
        self.nin_url = settings.NIN_API_URL
        self.bvn_key = settings.BVN_API_KEY
        self.nin_key = settings.NIN_API_KEY

    async def verify_bvn(self, bvn: str, first_name: str, last_name: str) -> dict:
        """
        Verify a Bank Verification Number against the identity provider.

        Returns verification result with match confidence.
        """
        # TODO: Call VerifyMe BVN endpoint
        # TODO: Compare returned name against provided name
        # TODO: Return structured result with match score
        return {
            "verified": False,
            "match_score": 0.0,
            "message": "BVN verification not implemented",
        }

    async def verify_nin(self, nin: str, first_name: str, last_name: str) -> dict:
        """
        Verify a National Identity Number against the identity provider.

        Returns verification result with match confidence.
        """
        # TODO: Call VerifyMe NIN endpoint
        # TODO: Compare returned name against provided name
        return {
            "verified": False,
            "match_score": 0.0,
            "message": "NIN verification not implemented",
        }


kyc_service = KYCService()
