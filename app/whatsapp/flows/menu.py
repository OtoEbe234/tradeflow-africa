"""
Main menu flow — entry point for all WhatsApp conversations.

Presents the user with available actions and routes to
the appropriate sub-flow based on their selection.

Options:
  1. Pay Supplier (NGN → CNY)
  2. Check Rate
  3. My Transactions
  4. Register
  Unknown / greeting → show menu
"""

from app.whatsapp.messages import send_menu, send_text
from app.whatsapp.flows.helpers import get_trader_by_phone, get_user_lang
from app.services.rate_service import RateService
from app.redis_client import redis


async def _show_rates(sender: str) -> dict:
    """Fetch live rates and send them, then stay on menu."""
    lang = await get_user_lang(sender)
    try:
        svc = RateService(redis)
        rates = await svc.get_rates()
        ngn_per_cny = rates["ngn_per_cny"]
        msg = (
            "Current Exchange Rates\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"1 CNY = *{ngn_per_cny} NGN*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Type *menu* for more options."
        )
    except Exception:
        msg = "Unable to fetch rates right now. Please try again shortly."
    await send_text(sender, msg)
    return {"flow": "menu", "step": "awaiting_selection", "data": {}}


async def handle_text(sender: str, text: str, state: dict) -> dict | None:
    """Handle free-text input at the main menu."""
    text_lower = text.strip().lower()

    if text_lower in ("hi", "hello", "hey", "start", "menu"):
        await send_menu(sender)
        return {"flow": "menu", "step": "awaiting_selection", "data": {}}

    if text_lower in ("1", "pay", "send", "payment"):
        trader = await get_trader_by_phone(sender)
        if not trader or trader.status.value != "active":
            await send_text(
                sender,
                "You need a registered and active account to make payments.\n"
                "Type *register* to create an account.",
            )
            await send_menu(sender)
            return {"flow": "menu", "step": "awaiting_selection", "data": {}}
        return {"flow": "payment", "step": "start", "data": {"direction": "ngn_to_cny"}}

    if text_lower in ("2", "rate", "rates", "check rate"):
        return await _show_rates(sender)

    if text_lower in ("3", "status", "transactions", "check"):
        return {"flow": "status", "step": "start", "data": {}}

    if text_lower in ("4", "register", "signup"):
        return {"flow": "registration", "step": "start", "data": {}}

    # Default: show menu
    await send_menu(sender)
    return {"flow": "menu", "step": "awaiting_selection", "data": {}}


async def handle_interactive(sender: str, reply_id: str, state: dict) -> dict | None:
    """Handle button/list replies at the main menu."""
    if reply_id == "action_pay":
        trader = await get_trader_by_phone(sender)
        if not trader or trader.status.value != "active":
            await send_text(
                sender,
                "You need a registered and active account to make payments.\n"
                "Type *register* to create an account.",
            )
            await send_menu(sender)
            return {"flow": "menu", "step": "awaiting_selection", "data": {}}
        return {"flow": "payment", "step": "start", "data": {"direction": "ngn_to_cny"}}

    if reply_id == "action_rate":
        return await _show_rates(sender)

    if reply_id == "action_status":
        return {"flow": "status", "step": "start", "data": {}}

    if reply_id == "action_register":
        return {"flow": "registration", "step": "start", "data": {}}

    await send_menu(sender)
    return {"flow": "menu", "step": "awaiting_selection", "data": {}}
