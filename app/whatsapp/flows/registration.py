"""
Registration conversation flow.

Guides a new user through account creation via WhatsApp:
business name -> trader type -> BVN/NIN -> OTP verification.
"""

from app.whatsapp.messages import send_text


STEPS = ["start", "business_name", "trader_type", "kyc_document", "otp", "complete"]


async def handle_text(sender: str, text: str, state: dict) -> dict | None:
    """Handle text input during the registration flow."""
    step = state.get("step", "start")
    data = state.get("data", {})

    if step == "start":
        await send_text(sender, "Welcome to TradeFlow Africa! Let's set up your account.\n\nWhat is your business name?")
        return {"flow": "registration", "step": "business_name", "data": data}

    if step == "business_name":
        data["business_name"] = text.strip()
        await send_text(
            sender,
            f"Got it, *{data['business_name']}*.\n\n"
            "What type of trader are you?\n"
            "1. Nigerian Importer\n"
            "2. Nigerian Exporter\n"
            "3. Chinese Supplier",
        )
        return {"flow": "registration", "step": "trader_type", "data": data}

    if step == "trader_type":
        type_map = {"1": "nigerian_importer", "2": "nigerian_exporter", "3": "chinese_supplier"}
        data["trader_type"] = type_map.get(text.strip(), text.strip())
        await send_text(sender, "Please provide your BVN (11 digits) for verification:")
        return {"flow": "registration", "step": "kyc_document", "data": data}

    if step == "kyc_document":
        data["bvn"] = text.strip()
        # TODO: Validate BVN format, send OTP
        await send_text(sender, "We've sent you a verification code. Please enter the 6-digit OTP:")
        return {"flow": "registration", "step": "otp", "data": data}

    if step == "otp":
        # TODO: Verify OTP, create trader account
        await send_text(
            sender,
            "Your account has been created successfully! "
            "Your KYC verification is in progress.\n\n"
            "Type *menu* to see available actions.",
        )
        return {"flow": "menu", "step": "start", "data": {}}

    return None


async def handle_interactive(sender: str, reply_id: str, state: dict) -> dict | None:
    """Handle interactive replies during registration."""
    # Registration primarily uses text input
    return await handle_text(sender, reply_id, state)
