"""
Main menu flow — entry point for all WhatsApp conversations.

Presents the user with available actions and routes to
the appropriate sub-flow based on their selection.
"""

from app.whatsapp.messages import send_menu


async def handle_text(sender: str, text: str, state: dict) -> dict | None:
    """Handle free-text input at the main menu."""
    text_lower = text.strip().lower()

    if text_lower in ("hi", "hello", "hey", "start", "menu"):
        await send_menu(sender)
        return {"flow": "menu", "step": "awaiting_selection", "data": {}}

    if text_lower in ("1", "pay", "send", "payment"):
        return {"flow": "payment", "step": "start", "data": {}}

    if text_lower in ("2", "status", "check"):
        return {"flow": "status", "step": "start", "data": {}}

    if text_lower in ("3", "register", "signup"):
        return {"flow": "registration", "step": "start", "data": {}}

    # Default: show menu
    await send_menu(sender)
    return {"flow": "menu", "step": "awaiting_selection", "data": {}}


async def handle_interactive(sender: str, reply_id: str, state: dict) -> dict | None:
    """Handle button/list replies at the main menu."""
    if reply_id == "action_pay":
        return {"flow": "payment", "step": "start", "data": {}}
    if reply_id == "action_status":
        return {"flow": "status", "step": "start", "data": {}}
    if reply_id == "action_register":
        return {"flow": "registration", "step": "start", "data": {}}

    await send_menu(sender)
    return None
