"""
Status check flow — lets traders check transaction status via WhatsApp.

Allows lookup by transaction reference or listing recent transactions.
"""

from app.whatsapp.messages import send_text


async def handle_text(sender: str, text: str, state: dict) -> dict | None:
    """Handle text input during the status check flow."""
    step = state.get("step", "start")

    if step == "start":
        await send_text(
            sender,
            "Check transaction status:\n"
            "1. Enter a transaction reference\n"
            "2. View my recent transactions",
        )
        return {"flow": "status", "step": "choice", "data": {}}

    if step == "choice":
        if text.strip() == "1":
            await send_text(sender, "Please enter your transaction reference:")
            return {"flow": "status", "step": "by_reference", "data": {}}
        elif text.strip() == "2":
            # TODO: Fetch recent transactions for this phone number
            await send_text(sender, "No recent transactions found.\nType *menu* for options.")
            return {"flow": "menu", "step": "start", "data": {}}

    if step == "by_reference":
        reference = text.strip()
        # TODO: Look up transaction by reference
        await send_text(sender, f"Transaction *{reference}*: Status lookup not yet implemented.\nType *menu* for options.")
        return {"flow": "menu", "step": "start", "data": {}}

    return None


async def handle_interactive(sender: str, reply_id: str, state: dict) -> dict | None:
    """Handle interactive replies during status check."""
    return await handle_text(sender, reply_id, state)
