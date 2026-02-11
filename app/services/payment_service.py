"""
Payment service — Providus Bank integration for NGN collections and payouts.

Handles virtual account generation for collections, transfer initiation
for payouts, and webhook processing for payment confirmations.
"""

import httpx

from app.config import settings


class PaymentService:
    """Providus Bank API integration for NGN-side payments."""

    def __init__(self):
        self.base_url = settings.PROVIDUS_BASE_URL
        self.client_id = settings.PROVIDUS_CLIENT_ID
        self.client_secret = settings.PROVIDUS_CLIENT_SECRET

    async def create_virtual_account(self, trader_id: str, account_name: str) -> dict:
        """
        Generate a dedicated virtual account for a trader's collection.

        The virtual account receives NGN payments that fund transactions.
        """
        # TODO: Call Providus dynamic account creation API
        # TODO: Store mapping of virtual account to trader
        return {
            "account_number": "",
            "account_name": account_name,
            "bank_name": "Providus Bank",
            "status": "not_implemented",
        }

    async def initiate_transfer(
        self, amount: float, account_number: str, bank_code: str, narration: str
    ) -> dict:
        """
        Initiate an NGN payout to a beneficiary bank account.

        Used for settling the NGN leg of completed matches.
        """
        # TODO: Call Providus fund transfer API
        # TODO: Return transaction reference for tracking
        return {
            "reference": "",
            "status": "not_implemented",
        }

    async def verify_payment(self, reference: str) -> dict:
        """Verify the status of a payment by reference."""
        # TODO: Call Providus transaction status API
        return {
            "reference": reference,
            "status": "not_implemented",
        }


payment_service = PaymentService()
