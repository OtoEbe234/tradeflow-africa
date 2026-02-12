"""
Registration conversation flow.

Guides a new user through account creation via WhatsApp:
WELCOME → LANGUAGE → PHONE_CONFIRM → BVN_INPUT → PIN_SET → PIN_CONFIRM → DONE

Integrations:
  - KYC service for BVN verification
  - Database for Trader creation
  - Redis for language persistence
"""

import logging

from app.whatsapp.messages import send_text, send_button, send_welcome, send_menu
from app.whatsapp.flows.helpers import (
    get_trader_by_phone,
    set_user_lang,
    validate_bvn_format,
    validate_pin_format,
    is_weak_pin,
)
from app.services.kyc_service import get_bvn_provider
from app.database import async_session
from app.models.trader import Trader, TraderStatus

logger = logging.getLogger(__name__)

STEPS = [
    "start", "language", "phone_confirm",
    "bvn_input", "pin_set", "pin_confirm",
]


async def handle_text(sender: str, text: str, state: dict) -> dict | None:
    """Handle text input during the registration flow."""
    step = state.get("step", "start")
    data = state.get("data", {})

    # ── START ────────────────────────────────────────────────────────
    if step == "start":
        # Check if already registered
        trader = await get_trader_by_phone(sender)
        if trader:
            await send_text(
                sender,
                f"You already have an account (*{trader.tradeflow_id}*).\n"
                "Type *menu* for options.",
            )
            await send_menu(sender)
            return {"flow": "menu", "step": "start", "data": {}}

        await send_welcome(sender)
        await send_text(
            sender,
            "Choose your language:\n1. English\n2. Pidgin English",
        )
        return {"flow": "registration", "step": "language", "data": data}

    # ── LANGUAGE ─────────────────────────────────────────────────────
    if step == "language":
        choice = text.strip()
        if choice == "1":
            data["lang"] = "en"
        elif choice == "2":
            data["lang"] = "pcm"
        else:
            await send_text(sender, "Please reply *1* for English or *2* for Pidgin:")
            return {"flow": "registration", "step": "language", "data": data}

        await set_user_lang(sender, data["lang"])
        await send_button(
            sender,
            f"We'll register this phone number:\n*{sender}*\n\nIs this correct?",
            [
                {"id": "confirm_yes", "title": "Yes, continue"},
                {"id": "confirm_no", "title": "No, cancel"},
            ],
        )
        return {"flow": "registration", "step": "phone_confirm", "data": data}

    # ── PHONE CONFIRM (text fallback) ────────────────────────────────
    if step == "phone_confirm":
        lower = text.strip().lower()
        if lower in ("yes", "y", "1"):
            await send_text(sender, "Please enter your BVN (11 digits):")
            return {"flow": "registration", "step": "bvn_input", "data": data}
        if lower in ("no", "n", "2"):
            await send_text(sender, "Registration cancelled.\nType *menu* for options.")
            await send_menu(sender)
            return {"flow": "menu", "step": "start", "data": {}}
        await send_text(sender, "Please reply *yes* or *no*.")
        return {"flow": "registration", "step": "phone_confirm", "data": data}

    # ── BVN INPUT ────────────────────────────────────────────────────
    if step == "bvn_input":
        bvn = text.strip()
        if not validate_bvn_format(bvn):
            await send_text(sender, "That doesn't look right. Please enter a valid 11-digit BVN:")
            return {"flow": "registration", "step": "bvn_input", "data": data}

        # Verify BVN via KYC service
        provider = get_bvn_provider()
        result = await provider.verify_bvn(bvn, sender)
        if not result.verified:
            await send_text(
                sender,
                "BVN verification failed. Please check the number and try again:"
            )
            return {"flow": "registration", "step": "bvn_input", "data": data}

        data["bvn"] = bvn
        data["full_name"] = result.full_name
        await send_text(
            sender,
            f"Verified! Welcome, *{result.full_name}*.\n\n"
            "Now create a 4-digit PIN for transaction authorization:"
        )
        return {"flow": "registration", "step": "pin_set", "data": data}

    # ── PIN SET ──────────────────────────────────────────────────────
    if step == "pin_set":
        pin = text.strip()
        if not validate_pin_format(pin):
            await send_text(sender, "Please enter exactly 4 digits:")
            return {"flow": "registration", "step": "pin_set", "data": data}
        if is_weak_pin(pin):
            await send_text(
                sender,
                "That PIN is too easy to guess (e.g. 1234, 0000).\n"
                "Please choose a stronger 4-digit PIN:"
            )
            return {"flow": "registration", "step": "pin_set", "data": data}
        data["pin"] = pin
        await send_text(sender, "Please re-enter your PIN to confirm:")
        return {"flow": "registration", "step": "pin_confirm", "data": data}

    # ── PIN CONFIRM ──────────────────────────────────────────────────
    if step == "pin_confirm":
        pin = text.strip()
        if pin != data.get("pin"):
            await send_text(
                sender,
                "PINs don't match. Let's try again.\n\n"
                "Enter a 4-digit PIN:"
            )
            return {"flow": "registration", "step": "pin_set", "data": data}

        # Create trader in database
        async with async_session() as session:
            trader = Trader(
                phone=sender,
                full_name=data["full_name"],
                status=TraderStatus.ACTIVE,
            )
            trader.set_bvn(data["bvn"])
            trader.set_pin(data["pin"])
            session.add(trader)
            await session.commit()
            tradeflow_id = trader.tradeflow_id

        await send_text(
            sender,
            f"Account created successfully!\n\n"
            f"Your TradeFlow ID: *{tradeflow_id}*\n"
            f"Name: {data['full_name']}\n\n"
            "Type *menu* to get started.",
        )
        await send_menu(sender)
        return {"flow": "menu", "step": "start", "data": {}}

    return None


async def handle_interactive(sender: str, reply_id: str, state: dict) -> dict | None:
    """Handle interactive replies during registration."""
    step = state.get("step", "start")
    data = state.get("data", {})

    # Phone confirmation buttons
    if step == "phone_confirm":
        if reply_id == "confirm_yes":
            await send_text(sender, "Please enter your BVN (11 digits):")
            return {"flow": "registration", "step": "bvn_input", "data": data}
        if reply_id == "confirm_no":
            await send_text(sender, "Registration cancelled.\nType *menu* for options.")
            await send_menu(sender)
            return {"flow": "menu", "step": "start", "data": {}}

    # For all other steps, delegate to text handler
    return await handle_text(sender, reply_id, state)
