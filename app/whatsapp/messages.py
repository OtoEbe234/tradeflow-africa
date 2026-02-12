"""
WhatsApp message sending utilities and templates.

Wraps the Meta Cloud API to send text, interactive (button/list),
and template messages to WhatsApp users.

All user-facing templates support English (``en``) and
Pidgin English (``pcm``) via the ``lang`` parameter.
"""

import httpx
import logging

from app.config import settings

logger = logging.getLogger(__name__)

API_URL = f"{settings.WHATSAPP_API_URL}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
HEADERS = {
    "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
    "Content-Type": "application/json",
}


# ── Low-level senders ────────────────────────────────────────────────────


async def mark_as_read(message_id: str):
    """Mark an incoming message as read (blue ticks)."""
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }
    async with httpx.AsyncClient() as client:
        await client.post(API_URL, json=payload, headers=HEADERS)


async def send_text(to: str, body: str):
    """Send a plain text message to a WhatsApp number."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }
    async with httpx.AsyncClient() as client:
        await client.post(API_URL, json=payload, headers=HEADERS)


async def send_menu(to: str):
    """Send the main menu as an interactive list message."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": "TradeFlow Africa"},
            "body": {"text": "Welcome! What would you like to do?"},
            "action": {
                "button": "Choose an option",
                "sections": [
                    {
                        "title": "Actions",
                        "rows": [
                            {"id": "action_pay", "title": "Make a Payment", "description": "Send money across the Nigeria-China corridor"},
                            {"id": "action_status", "title": "Check Status", "description": "Check your transaction status"},
                            {"id": "action_register", "title": "Register", "description": "Create a new TradeFlow account"},
                            {"id": "action_rate", "title": "Check Rate", "description": "View current NGN/CNY exchange rates"},
                        ],
                    }
                ],
            },
        },
    }
    async with httpx.AsyncClient() as client:
        await client.post(API_URL, json=payload, headers=HEADERS)


async def send_button(to: str, body: str, buttons: list[dict]):
    """Send an interactive button message (max 3 buttons)."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": btn["id"], "title": btn["title"]}}
                    for btn in buttons[:3]
                ]
            },
        },
    }
    async with httpx.AsyncClient() as client:
        await client.post(API_URL, json=payload, headers=HEADERS)


# ── Message templates ────────────────────────────────────────────────────

# Template strings keyed by (template_name, language)

_TEMPLATES = {
    # ── Welcome ──────────────────────────────────────────────────────
    ("welcome", "en"): (
        "Welcome to *TradeFlow Africa*!\n\n"
        "We help you send money between Nigeria and China — fast, "
        "secure, and at the best rates.\n\n"
        "Type *menu* to get started or *help* for commands."
    ),
    ("welcome", "pcm"): (
        "Welcome to *TradeFlow Africa*!\n\n"
        "We dey help you send money between Naija and China — "
        "sharp sharp, secure, and beta rates.\n\n"
        "Type *menu* make you start or *help* for how e dey work."
    ),

    # ── Rate quote ───────────────────────────────────────────────────
    ("rate_quote", "en"): (
        "Exchange Rate Quote\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Direction: {direction}\n"
        "Rate: *1 {source_currency} = {rate} {target_currency}*\n"
        "You send: *{source_amount} {source_currency}*\n"
        "They receive: *{target_amount} {target_currency}*\n"
        "Fee: {fee_amount} {source_currency}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "This rate is valid for {validity_minutes} minutes."
    ),
    ("rate_quote", "pcm"): (
        "Exchange Rate\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Direction: {direction}\n"
        "Rate: *1 {source_currency} = {rate} {target_currency}*\n"
        "You dey send: *{source_amount} {source_currency}*\n"
        "Dem go receive: *{target_amount} {target_currency}*\n"
        "Charge: {fee_amount} {source_currency}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "This rate go last for {validity_minutes} minutes."
    ),

    # ── Payment summary ──────────────────────────────────────────────
    ("payment_summary", "en"): (
        "Payment Summary\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Direction: {direction}\n"
        "Amount: *{amount}*\n"
        "Beneficiary: {beneficiary_name}\n"
        "Account: {beneficiary_account}\n"
        "Bank: {beneficiary_bank}\n"
        "Rate: {rate}\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Reply *confirm* to proceed or *cancel* to abort."
    ),
    ("payment_summary", "pcm"): (
        "Payment Summary\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Direction: {direction}\n"
        "Amount: *{amount}*\n"
        "Who go collect: {beneficiary_name}\n"
        "Account: {beneficiary_account}\n"
        "Bank: {beneficiary_bank}\n"
        "Rate: {rate}\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Reply *confirm* to go ahead or *cancel* to stop am."
    ),

    # ── Deposit instructions ─────────────────────────────────────────
    ("deposit_instructions", "en"): (
        "Deposit Instructions\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Reference: *{reference}*\n"
        "Amount: *{amount}*\n\n"
        "Please transfer to:\n"
        "Bank: {bank_name}\n"
        "Account: {account_number}\n"
        "Name: {account_name}\n\n"
        "Your payment will expire in *{expiry_hours} hours*.\n"
        "We'll notify you once the deposit is confirmed."
    ),
    ("deposit_instructions", "pcm"): (
        "How to Pay\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Reference: *{reference}*\n"
        "Amount: *{amount}*\n\n"
        "Abeg transfer to:\n"
        "Bank: {bank_name}\n"
        "Account: {account_number}\n"
        "Name: {account_name}\n\n"
        "Your payment go expire for *{expiry_hours} hours*.\n"
        "We go tell you once e confirm."
    ),

    # ── Status update ────────────────────────────────────────────────
    ("status_update", "en"): (
        "Transaction Update\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Reference: *{reference}*\n"
        "Status: *{status}*\n"
        "{details}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Type *menu* for more options."
    ),
    ("status_update", "pcm"): (
        "Transaction Update\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Reference: *{reference}*\n"
        "Status: *{status}*\n"
        "{details}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Type *menu* for wetin you fit do."
    ),
}


def get_template(name: str, lang: str = "en", **kwargs) -> str:
    """
    Render a named message template.

    Falls back to English if the requested language is not available.
    """
    template = _TEMPLATES.get((name, lang)) or _TEMPLATES.get((name, "en"), "")
    if kwargs:
        return template.format(**kwargs)
    return template


# ── Convenience senders for each template ────────────────────────────────


async def send_welcome(to: str, lang: str = "en"):
    """Send the welcome / onboarding message."""
    await send_text(to, get_template("welcome", lang))


async def send_rate_quote(
    to: str,
    *,
    direction: str,
    source_currency: str,
    target_currency: str,
    rate: str,
    source_amount: str,
    target_amount: str,
    fee_amount: str,
    validity_minutes: int = 1,
    lang: str = "en",
):
    """Send a formatted exchange rate quote."""
    await send_text(
        to,
        get_template(
            "rate_quote", lang,
            direction=direction,
            source_currency=source_currency,
            target_currency=target_currency,
            rate=rate,
            source_amount=source_amount,
            target_amount=target_amount,
            fee_amount=fee_amount,
            validity_minutes=validity_minutes,
        ),
    )


async def send_payment_summary(
    to: str,
    *,
    direction: str,
    amount: str,
    beneficiary_name: str,
    beneficiary_account: str,
    beneficiary_bank: str,
    rate: str,
    lang: str = "en",
):
    """Send a payment confirmation summary."""
    await send_text(
        to,
        get_template(
            "payment_summary", lang,
            direction=direction,
            amount=amount,
            beneficiary_name=beneficiary_name,
            beneficiary_account=beneficiary_account,
            beneficiary_bank=beneficiary_bank,
            rate=rate,
        ),
    )


async def send_deposit_instructions(
    to: str,
    *,
    reference: str,
    amount: str,
    bank_name: str,
    account_number: str,
    account_name: str,
    expiry_hours: int = 2,
    lang: str = "en",
):
    """Send deposit / bank transfer instructions."""
    await send_text(
        to,
        get_template(
            "deposit_instructions", lang,
            reference=reference,
            amount=amount,
            bank_name=bank_name,
            account_number=account_number,
            account_name=account_name,
            expiry_hours=expiry_hours,
        ),
    )


async def send_status_update(
    to: str,
    *,
    reference: str,
    status: str,
    details: str = "",
    lang: str = "en",
):
    """Send a transaction status update notification."""
    await send_text(
        to,
        get_template(
            "status_update", lang,
            reference=reference,
            status=status,
            details=details,
        ),
    )
