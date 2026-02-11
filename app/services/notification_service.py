"""
Notification service — SMS and WhatsApp message delivery.

Sends transactional notifications to traders including OTPs,
transaction status updates, and matching confirmations.
"""

import httpx

from app.config import settings


class NotificationService:
    """Delivers notifications via SMS (Termii) and WhatsApp (Meta Cloud API)."""

    def __init__(self):
        self.sms_url = settings.SMS_API_URL
        self.sms_key = settings.SMS_API_KEY
        self.wa_url = settings.WHATSAPP_API_URL
        self.wa_token = settings.WHATSAPP_ACCESS_TOKEN
        self.wa_phone_id = settings.WHATSAPP_PHONE_NUMBER_ID

    async def send_sms(self, phone: str, message: str) -> dict:
        """Send an SMS message via Termii."""
        # TODO: Call Termii SMS API
        # TODO: Log delivery status
        return {"phone": phone, "channel": "sms", "status": "not_implemented"}

    async def send_whatsapp(self, phone: str, message: str) -> dict:
        """Send a WhatsApp text message via Meta Cloud API."""
        # TODO: Call WhatsApp Cloud API send message endpoint
        # TODO: Handle message templates for first-contact messages
        return {"phone": phone, "channel": "whatsapp", "status": "not_implemented"}

    async def send_whatsapp_template(
        self, phone: str, template_name: str, parameters: list[str]
    ) -> dict:
        """Send a pre-approved WhatsApp template message."""
        # TODO: Build template message payload
        # TODO: Call WhatsApp Cloud API
        return {
            "phone": phone,
            "template": template_name,
            "status": "not_implemented",
        }

    async def send_otp(self, phone: str, otp: str) -> dict:
        """Send OTP via both SMS and WhatsApp for maximum delivery."""
        sms_result = await self.send_sms(phone, f"Your TradeFlow code is {otp}")
        wa_result = await self.send_whatsapp(phone, f"Your TradeFlow code is {otp}")
        return {"sms": sms_result, "whatsapp": wa_result}

    async def notify_match(self, phone: str, match_details: dict) -> dict:
        """Notify a trader that their transaction has been matched."""
        message = (
            f"Your transaction {match_details.get('reference', '')} has been matched! "
            f"Amount: {match_details.get('amount', '')} {match_details.get('currency', '')}. "
            f"Settlement is in progress."
        )
        return await self.send_whatsapp(phone, message)


notification_service = NotificationService()
