"""
Payment service — Providus Bank integration for NGN collections and payouts.

Handles virtual account generation for collections, transfer initiation
for payouts, webhook signature verification, and mock payload generation.
"""

import hashlib
import hmac
from datetime import datetime, timezone

import httpx

from app.config import settings


class PaymentService:
    """Providus Bank API integration for NGN-side payments."""

    def __init__(self):
        self.base_url = settings.PROVIDUS_BASE_URL
        self.client_id = settings.PROVIDUS_CLIENT_ID
        self.client_secret = settings.PROVIDUS_CLIENT_SECRET

    async def generate_virtual_account(
        self,
        transaction_id: str,
        reference: str,
        account_name: str,
        amount: float,
    ) -> dict:
        """
        Generate a dedicated virtual account for a transaction's collection.

        Returns TF{reference[4:]} format account number (matches deposit
        instructions in transactions.py:260). When PROVIDUS_BASE_URL is
        empty (dev/test), returns a mock response. Otherwise calls real API.
        """
        account_number = f"TF{reference[4:]}"

        if not self.base_url:
            return {
                "account_number": account_number,
                "account_name": account_name,
                "bank_name": "Providus Bank",
                "status": "active",
            }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/virtual-accounts",
                json={
                    "account_name": account_name,
                    "amount": amount,
                    "transaction_id": transaction_id,
                    "reference": reference,
                },
                headers={
                    "Client-Id": self.client_id,
                    "Client-Secret": self.client_secret,
                },
            )
            resp.raise_for_status()
            return resp.json()

    def verify_webhook_signature(self, payload_body: bytes, signature: str) -> bool:
        """
        Verify Providus webhook HMAC-SHA512 signature.

        When PROVIDUS_WEBHOOK_SECRET is empty (dev), returns True to
        allow unsigned requests during development.
        """
        secret = settings.PROVIDUS_WEBHOOK_SECRET
        if not secret:
            return True

        expected = hmac.new(
            secret.encode(),
            payload_body,
            hashlib.sha512,
        ).hexdigest()

        return hmac.compare_digest(expected, signature)

    def simulate_webhook_payload(
        self,
        account_number: str,
        amount: float,
        reference: str,
    ) -> dict:
        """
        Build a Providus-format webhook dict for the dev simulate endpoint.
        """
        return {
            "sessionId": f"SIM-{reference}-{int(datetime.now(timezone.utc).timestamp())}",
            "accountNumber": account_number,
            "tranRemarks": f"Payment for {reference}",
            "transactionAmount": str(amount),
            "settledAmount": str(amount),
            "feeAmount": "0.00",
            "vatAmount": "0.00",
            "currency": "NGN",
            "initiationTranRef": reference,
            "settlementId": f"SET-{reference}",
            "sourceAccountNumber": "0012345678",
            "sourceAccountName": "Test Payer",
            "sourceBankName": "Test Bank",
            "channelId": "1",
            "tranDateTime": datetime.now(timezone.utc).isoformat(),
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
