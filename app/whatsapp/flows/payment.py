"""
Payment conversation flow.

Guides a trader through creating a cross-border payment:
START → DIRECTION → AMOUNT_INPUT → RATE_DISPLAY → SUPPLIER_NAME →
SUPPLIER_BANK → SUPPLIER_ACCOUNT → INVOICE_UPLOAD → SUMMARY_CONFIRM →
PIN_ENTRY → DEPOSIT_INSTRUCTIONS

Integrations:
  - RateService for quote generation
  - Database for Transaction creation
  - Trader model for PIN verification
"""

import logging
from decimal import Decimal

from app.whatsapp.messages import (
    send_text,
    send_button,
    send_rate_quote,
    send_payment_summary,
    send_deposit_instructions,
    send_menu,
)
from app.whatsapp.parser import parse_amount
from app.whatsapp.flows.helpers import (
    get_trader_by_phone,
    get_user_lang,
    validate_account_number,
    validate_pin_format,
    format_direction,
)
from app.services.rate_service import RateService, CircuitBreakerOpenError
from app.database import async_session
from app.models.transaction import Transaction, TransactionStatus
from app.redis_client import redis
from app.config import settings

logger = logging.getLogger(__name__)

# Minimum amounts
MIN_NGN = Decimal("10000")
MIN_CNY = Decimal("100")

MAX_PIN_ATTEMPTS = 3


async def handle_text(sender: str, text: str, state: dict) -> dict | None:
    """Handle text input during the payment flow."""
    step = state.get("step", "start")
    data = state.get("data", {})

    # ── START ────────────────────────────────────────────────────────
    if step == "start":
        trader = await get_trader_by_phone(sender)
        if not trader or trader.status.value != "active":
            await send_text(
                sender,
                "You need a registered and active account to make payments.\n"
                "Type *register* to create an account.",
            )
            await send_menu(sender)
            return {"flow": "menu", "step": "start", "data": {}}

        data["trader_phone"] = sender

        # If direction already preset (from menu), skip direction step
        if data.get("direction"):
            currency = "NGN" if data["direction"] == "ngn_to_cny" else "CNY"
            await send_text(
                sender,
                f"How much {currency} do you want to send?\n"
                "(e.g., 50m, N50,000,000, 5000000)",
            )
            return {"flow": "payment", "step": "amount_input", "data": data}

        await send_button(
            sender,
            "What type of payment?\n\n"
            "1. Send NGN, receive CNY (pay Chinese supplier)\n"
            "2. Send CNY, receive NGN (receive from Nigerian buyer)",
            [
                {"id": "dir_ngn_cny", "title": "NGN → CNY"},
                {"id": "dir_cny_ngn", "title": "CNY → NGN"},
            ],
        )
        return {"flow": "payment", "step": "direction", "data": data}

    # ── DIRECTION ────────────────────────────────────────────────────
    if step == "direction":
        choice = text.strip()
        if choice == "1":
            data["direction"] = "ngn_to_cny"
        elif choice == "2":
            data["direction"] = "cny_to_ngn"
        else:
            await send_text(sender, "Please reply *1* (NGN → CNY) or *2* (CNY → NGN):")
            return {"flow": "payment", "step": "direction", "data": data}

        currency = "NGN" if data["direction"] == "ngn_to_cny" else "CNY"
        await send_text(
            sender,
            f"How much {currency} do you want to send?\n"
            "(e.g., 50m, N50,000,000, 5000000)",
        )
        return {"flow": "payment", "step": "amount_input", "data": data}

    # ── AMOUNT INPUT ─────────────────────────────────────────────────
    if step == "amount_input":
        amount = parse_amount(text)
        if amount is None:
            await send_text(
                sender,
                "I couldn't understand that amount. Please try again:\n"
                "(e.g., 50m, N50,000,000, 5000000)",
            )
            return {"flow": "payment", "step": "amount_input", "data": data}

        # Check minimum
        direction = data.get("direction", "ngn_to_cny")
        if direction == "ngn_to_cny" and amount < MIN_NGN:
            await send_text(sender, f"Minimum amount is NGN {MIN_NGN:,.0f}. Please enter a higher amount:")
            return {"flow": "payment", "step": "amount_input", "data": data}
        if direction == "cny_to_ngn" and amount < MIN_CNY:
            await send_text(sender, f"Minimum amount is CNY {MIN_CNY:,.0f}. Please enter a higher amount:")
            return {"flow": "payment", "step": "amount_input", "data": data}

        # Generate quote
        source_currency = "NGN" if direction == "ngn_to_cny" else "CNY"
        target_currency = "CNY" if direction == "ngn_to_cny" else "NGN"

        try:
            svc = RateService(redis)
            quote = await svc.generate_quote(
                source_currency=source_currency,
                target_currency=target_currency,
                source_amount=amount,
            )
        except CircuitBreakerOpenError:
            await send_text(
                sender,
                "Rate quotes are temporarily paused due to unusual market movement.\n"
                "Please try again in a few minutes.",
            )
            await send_menu(sender)
            return {"flow": "menu", "step": "start", "data": {}}

        data["quote"] = quote
        lang = await get_user_lang(sender)

        await send_rate_quote(
            sender,
            direction=format_direction(direction),
            source_currency=quote["source_currency"],
            target_currency=quote["target_currency"],
            rate=quote["mid_market_rate"],
            source_amount=quote["source_amount"],
            target_amount=quote["target_amount"],
            fee_amount=quote["fee_amount"],
            lang=lang,
        )
        await send_button(
            sender,
            "Would you like to proceed with this rate?",
            [
                {"id": "rate_accept", "title": "Proceed"},
                {"id": "rate_decline", "title": "Cancel"},
            ],
        )
        return {"flow": "payment", "step": "rate_display", "data": data}

    # ── RATE DISPLAY ─────────────────────────────────────────────────
    if step == "rate_display":
        lower = text.strip().lower()
        if lower in ("proceed", "yes", "1"):
            await send_text(sender, "Enter the supplier/beneficiary name:")
            return {"flow": "payment", "step": "supplier_name", "data": data}
        if lower in ("cancel", "no", "2"):
            await send_text(sender, "Payment cancelled.")
            await send_menu(sender)
            return {"flow": "menu", "step": "start", "data": {}}
        await send_text(sender, "Please reply *proceed* or *cancel*.")
        return {"flow": "payment", "step": "rate_display", "data": data}

    # ── SUPPLIER NAME ────────────────────────────────────────────────
    if step == "supplier_name":
        data["supplier_name"] = text.strip()
        await send_text(sender, "Enter the supplier's bank name:")
        return {"flow": "payment", "step": "supplier_bank", "data": data}

    # ── SUPPLIER BANK ────────────────────────────────────────────────
    if step == "supplier_bank":
        data["supplier_bank"] = text.strip()
        await send_text(sender, "Enter the supplier's account number:")
        return {"flow": "payment", "step": "supplier_account", "data": data}

    # ── SUPPLIER ACCOUNT ─────────────────────────────────────────────
    if step == "supplier_account":
        acct = text.strip()
        if not validate_account_number(acct):
            await send_text(sender, "Please enter a valid account number (10-20 digits):")
            return {"flow": "payment", "step": "supplier_account", "data": data}
        data["supplier_account"] = acct
        await send_text(
            sender,
            "Upload an invoice image/document, or type *skip* to continue without one:",
        )
        return {"flow": "payment", "step": "invoice_upload", "data": data}

    # ── INVOICE UPLOAD ───────────────────────────────────────────────
    if step == "invoice_upload":
        lower = text.strip().lower()
        has_media = bool(data.get("last_media"))

        if lower != "skip" and not has_media:
            await send_text(
                sender,
                "Please upload an invoice or type *skip* to continue without one.",
            )
            return {"flow": "payment", "step": "invoice_upload", "data": data}

        if has_media:
            data["invoice_media"] = data.pop("last_media")

        return await _show_summary(sender, data)

    # ── SUMMARY CONFIRM ──────────────────────────────────────────────
    if step == "summary_confirm":
        lower = text.strip().lower()
        if lower in ("confirm", "yes", "1"):
            await send_text(sender, "Enter your 4-digit PIN:")
            return {"flow": "payment", "step": "pin_entry", "data": data}
        if lower in ("cancel", "no", "2"):
            await send_text(sender, "Payment cancelled.")
            await send_menu(sender)
            return {"flow": "menu", "step": "start", "data": {}}
        await send_text(sender, "Please reply *confirm* or *cancel*.")
        return {"flow": "payment", "step": "summary_confirm", "data": data}

    # ── PIN ENTRY ────────────────────────────────────────────────────
    if step == "pin_entry":
        pin = text.strip()
        if not validate_pin_format(pin):
            await send_text(sender, "Please enter exactly 4 digits:")
            return {"flow": "payment", "step": "pin_entry", "data": data}

        trader = await get_trader_by_phone(sender)
        if not trader or not trader.verify_pin(pin):
            attempts = data.get("pin_attempts", 0) + 1
            data["pin_attempts"] = attempts
            if attempts >= MAX_PIN_ATTEMPTS:
                await send_text(
                    sender,
                    "Too many incorrect PIN attempts. Payment cancelled for security.",
                )
                await send_menu(sender)
                return {"flow": "menu", "step": "start", "data": {}}
            remaining = MAX_PIN_ATTEMPTS - attempts
            await send_text(
                sender,
                f"Incorrect PIN. {remaining} attempt(s) remaining.\nTry again:",
            )
            return {"flow": "payment", "step": "pin_entry", "data": data}

        # PIN verified — create transaction
        return await _create_transaction(sender, data, trader)

    return None


async def handle_interactive(sender: str, reply_id: str, state: dict) -> dict | None:
    """Handle interactive replies during the payment flow."""
    # Map button IDs to text equivalents
    button_map = {
        "dir_ngn_cny": "1",
        "dir_cny_ngn": "2",
        "rate_accept": "proceed",
        "rate_decline": "cancel",
        "pay_confirm": "confirm",
        "pay_cancel": "cancel",
    }
    text = button_map.get(reply_id, reply_id)
    return await handle_text(sender, text, state)


# ── Private helpers ──────────────────────────────────────────────────────


async def _show_summary(sender: str, data: dict) -> dict:
    """Build and send the payment summary, ask for confirmation."""
    quote = data.get("quote", {})
    lang = await get_user_lang(sender)

    await send_payment_summary(
        sender,
        direction=format_direction(data.get("direction", "")),
        amount=f"{quote.get('source_amount', '?')} {quote.get('source_currency', '')}",
        beneficiary_name=data.get("supplier_name", ""),
        beneficiary_account=data.get("supplier_account", ""),
        beneficiary_bank=data.get("supplier_bank", ""),
        rate=f"1 {quote.get('source_currency', '')} = {quote.get('mid_market_rate', '?')} {quote.get('target_currency', '')}",
        lang=lang,
    )
    await send_button(
        sender,
        "Confirm this payment?",
        [
            {"id": "pay_confirm", "title": "Confirm"},
            {"id": "pay_cancel", "title": "Cancel"},
        ],
    )
    return {"flow": "payment", "step": "summary_confirm", "data": data}


async def _create_transaction(sender: str, data: dict, trader) -> dict:
    """Create the Transaction in DB and send deposit instructions."""
    quote = data.get("quote", {})
    direction = data.get("direction", "ngn_to_cny")

    async with async_session() as session:
        txn = Transaction(
            trader_id=trader.id,
            direction=direction,
            source_amount=Decimal(quote["source_amount"]),
            target_amount=Decimal(quote["target_amount"]),
            exchange_rate=Decimal(quote["mid_market_rate"]),
            fee_amount=Decimal(quote["fee_amount"]),
            fee_percentage=Decimal(quote["fee_percentage"]),
            supplier_name=data.get("supplier_name"),
            supplier_bank=data.get("supplier_bank"),
            status=TransactionStatus.INITIATED,
        )
        txn.set_supplier_account(data.get("supplier_account", ""))
        session.add(txn)
        await session.commit()
        reference = txn.reference

    lang = await get_user_lang(sender)
    await send_deposit_instructions(
        sender,
        reference=reference,
        amount=f"{quote['total_cost']} {quote['source_currency']}",
        bank_name="Providus Bank",
        account_number=f"TF{reference[4:]}",
        account_name=f"TradeFlow/{reference}",
        expiry_hours=settings.PAYMENT_EXPIRY_HOURS,
        lang=lang,
    )
    return {"flow": "menu", "step": "start", "data": {}}
