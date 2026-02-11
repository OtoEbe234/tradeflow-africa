"""
Payment conversation flow.

Guides a trader through creating a cross-border payment:
direction -> amount -> beneficiary details -> confirmation.
"""

from app.whatsapp.messages import send_text
from app.whatsapp.parser import parse_amount


async def handle_text(sender: str, text: str, state: dict) -> dict | None:
    """Handle text input during the payment flow."""
    step = state.get("step", "start")
    data = state.get("data", {})

    if step == "start":
        await send_text(
            sender,
            "What type of payment?\n"
            "1. Send NGN, receive CNY (pay Chinese supplier)\n"
            "2. Send CNY, receive NGN (receive from Nigerian buyer)",
        )
        return {"flow": "payment", "step": "direction", "data": data}

    if step == "direction":
        direction_map = {"1": "ngn_to_cny", "2": "cny_to_ngn"}
        data["direction"] = direction_map.get(text.strip(), "ngn_to_cny")
        currency = "NGN" if data["direction"] == "ngn_to_cny" else "CNY"
        await send_text(sender, f"How much {currency} do you want to send?\n(e.g., 50m, N50,000,000, 5000000)")
        return {"flow": "payment", "step": "amount", "data": data}

    if step == "amount":
        amount = parse_amount(text)
        if amount is None:
            await send_text(sender, "I couldn't understand that amount. Please try again (e.g., 50m, N50,000,000):")
            return state
        data["amount"] = str(amount)
        await send_text(sender, f"Amount: *{amount:,.2f}*\n\nPlease enter the beneficiary name:")
        return {"flow": "payment", "step": "beneficiary_name", "data": data}

    if step == "beneficiary_name":
        data["beneficiary_name"] = text.strip()
        await send_text(sender, "Please enter the beneficiary account number:")
        return {"flow": "payment", "step": "beneficiary_account", "data": data}

    if step == "beneficiary_account":
        data["beneficiary_account"] = text.strip()
        await send_text(sender, "Please enter the beneficiary bank name:")
        return {"flow": "payment", "step": "beneficiary_bank", "data": data}

    if step == "beneficiary_bank":
        data["beneficiary_bank"] = text.strip()
        # TODO: Fetch rate quote and display summary
        await send_text(
            sender,
            f"Please confirm your payment:\n"
            f"Direction: {data['direction']}\n"
            f"Amount: {data['amount']}\n"
            f"To: {data['beneficiary_name']}\n"
            f"Account: {data['beneficiary_account']}\n"
            f"Bank: {data['beneficiary_bank']}\n\n"
            f"Reply *confirm* to proceed or *cancel* to abort.",
        )
        return {"flow": "payment", "step": "confirm", "data": data}

    if step == "confirm":
        if text.strip().lower() == "confirm":
            # TODO: Create transaction via API
            await send_text(sender, "Payment submitted! You'll receive updates on the status.\nType *menu* for options.")
            return {"flow": "menu", "step": "start", "data": {}}
        else:
            await send_text(sender, "Payment cancelled.\nType *menu* for options.")
            return {"flow": "menu", "step": "start", "data": {}}

    return None


async def handle_interactive(sender: str, reply_id: str, state: dict) -> dict | None:
    """Handle interactive replies during the payment flow."""
    return await handle_text(sender, reply_id, state)
