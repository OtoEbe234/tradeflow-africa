"""
WhatsApp message sending utilities.

Wraps the Meta Cloud API to send text, interactive (button/list),
and template messages to WhatsApp users.
"""

import httpx

from app.config import settings

API_URL = f"{settings.WHATSAPP_API_URL}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
HEADERS = {
    "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
    "Content-Type": "application/json",
}


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
